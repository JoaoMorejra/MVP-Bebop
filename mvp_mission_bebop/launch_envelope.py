"""Altitude envelope a launch document must satisfy, shared with the ground station.

The operator states the operating altitude over the floor
(``kinematics.target_altitude_m``). Which values are flyable depends on the
surface the airframe takes off from:

* the top of the pad (``platform.height_m > 0`` and
  ``platform.takeoff_from_ground`` false): the lowest altitude is the pad top
  plus :func:`pad_clearance_m`. Below it the airframe would hover inside the
  pad, or so close to its top that no hover sample counts as airborne
  (``MissionParameters.relative_target_altitude_m`` refuses it);
* the floor (``platform.takeoff_from_ground`` true, or no pad configured):
  :data:`ALTITUDE_MIN_M`.

The highest altitude is :data:`ALTITUDE_MAX_M` on both surfaces, capped
:data:`VENUE_HEADROOM_M` under a known venue ceiling.

``bebop_mission_control/src/lib/altitudeEnvelope.ts`` mirrors this module
constant for constant. ``test/fixtures/altitude_envelope_cases.json`` is read by
both test suites, so a document the station lets through is a document the
mission accepts.

Pure: depends on nothing but the standard library and reads the parameter
objects by attribute, so the argument parser and the tests can use it without
importing the flight stack.
"""

from __future__ import annotations

import math
from typing import Any, Final, Tuple

#: Lowest operating altitude over the floor on any surface, in metres.
ALTITUDE_MIN_M: Final[float] = 0.10
#: Highest operating altitude over the floor on any surface, in metres. From
#: the pad top the flight climbs past the native hover height of the firmware
#: under ``TakeoffStep._acquire_altitude``; a known venue ceiling lowers it.
ALTITUDE_MAX_M: Final[float] = 3.00
#: Resolution of the altitude slider of the station, in metres.
ALTITUDE_STEP_M: Final[float] = 0.05
#: Least height kept above the pad top, in metres. The rotor wash recirculates
#: over a 60 x 60 cm top below it, and it is the factory value of
#: ``kinematics.takeoff_settle_min_altitude_m``.
PAD_CLEARANCE_M: Final[float] = 0.30
#: Least distance kept under a known venue ceiling, in metres.
VENUE_HEADROOM_M: Final[float] = 0.10
#: Operating altitude over the floor above which the pad top applies the
#: operator margin ``kinematics.altitude_ceiling_margin_m`` instead of the fixed
#: totem headroom, in metres. At or below it the relative target never exceeds
#: the native hover height and the fixed headroom covers the firmware profile.
TOTEM_MARGIN_UNLOCK_ABOVE_M: Final[float] = 2.00

#: Comparison slack, in metres. Absorbs the binary representation of sums such
#: as ``1.0 + 0.3`` so a value the slider offers is never refused by rounding.
_TOLERANCE_M: Final[float] = 1e-9


class LaunchEnvelopeError(ValueError):
    """A launch document outside the flyable envelope.

    Parameters
    ----------
    field : str
        Dotted path of the offending field, as the station names it
        (``"kinematics.target_altitude_m"``).
    reason : str
        Short explanation in Portuguese, fit to be shown to the operator.

    Attributes
    ----------
    field : str
    reason : str
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _strict_bool(container: Any, name: str, default: bool, path: str) -> bool:
    """Read a boolean field that a truthy string must never satisfy.

    Raises
    ------
    LaunchEnvelopeError
        If the attribute exists and is not a ``bool``: ``"false"`` is a truthy
        string, and ``0``/``1`` are not what the station writes.
    """
    value = getattr(container, name, default)
    if not isinstance(value, bool):
        raise LaunchEnvelopeError(path, f"precisa ser verdadeiro ou falso, recebeu {value!r}")
    return value


def pad_height_m(params: Any) -> float:
    """Configured pad height, in metres; 0 when absent or not a finite number."""
    raw = getattr(params.platform, "height_m", 0.0)
    return float(raw) if _finite_number(raw) else 0.0


def launches_from_ground(params: Any) -> bool:
    """Whether the airframe takes off from the floor.

    True when ``platform.takeoff_from_ground`` is true, and also when no pad is
    configured (``platform.height_m <= 0``): a pad of zero height is the floor,
    which keeps the factory document a flight from the floor. A non-boolean
    ``takeoff_from_ground`` reads as false here; :func:`validate_launch_envelope`
    refuses it.
    """
    if getattr(params.platform, "takeoff_from_ground", False) is True:
        return True
    return pad_height_m(params) <= 0.0


def pad_clearance_m(params: Any) -> float:
    """Least height kept above the pad top, in metres.

    ``max(PAD_CLEARANCE_M, kinematics.takeoff_settle_min_altitude_m)``: a hover
    under the settle floor never counts as airborne, so a raised floor raises
    the clearance with it.
    """
    settle = getattr(params.kinematics, "takeoff_settle_min_altitude_m", PAD_CLEARANCE_M)
    return max(PAD_CLEARANCE_M, float(settle) if _finite_number(settle) else PAD_CLEARANCE_M)


def altitude_envelope(params: Any) -> Tuple[float, float]:
    """Accepted range of ``kinematics.target_altitude_m`` over the floor.

    Parameters
    ----------
    params : MissionParameters
        Read only, with the pad as configured (before
        ``mission_mode.apply_launch_surface`` resolves it for the flight).

    Returns
    -------
    tuple of float
        ``(minimum, maximum)`` in metres, both inclusive. On the pad top the
        minimum is ``height_m + pad_clearance_m(params)``, on the floor
        :data:`ALTITUDE_MIN_M`. The maximum is :data:`ALTITUDE_MAX_M` (3.0 m) on
        both surfaces, lowered to ``venue_ceiling_agl_m - VENUE_HEADROOM_M`` when
        the venue ceiling is known. The range is empty (minimum above maximum)
        when a low venue leaves no room above the pad.
    """
    if launches_from_ground(params):
        minimum = ALTITUDE_MIN_M
    else:
        minimum = max(ALTITUDE_MIN_M, pad_height_m(params) + pad_clearance_m(params))
    maximum = ALTITUDE_MAX_M
    venue = getattr(params.platform, "venue_ceiling_agl_m", None)
    if _finite_number(venue):
        maximum = min(maximum, float(venue) - VENUE_HEADROOM_M)
    return minimum, maximum


def margin_unlocked(params: Any) -> bool:
    """Whether the ceiling applies the operator margin rather than the fixed totem headroom.

    Parameters
    ----------
    params : MissionParameters
        Read only.

    Returns
    -------
    bool
        True on the floor (:func:`launches_from_ground`), and on the pad top
        when ``kinematics.target_altitude_m`` exceeds
        :data:`TOTEM_MARGIN_UNLOCK_ABOVE_M`. A target that is not a finite
        number keeps the pad top locked.
    """
    if launches_from_ground(params):
        return True
    altitude = getattr(params.kinematics, "target_altitude_m", None)
    return _finite_number(altitude) and altitude > TOTEM_MARGIN_UNLOCK_ABOVE_M + _TOLERANCE_M


def validate_launch_envelope(params: Any) -> None:
    """Refuse a launch document the mission cannot fly as written.

    Call it after ``mission_mode.apply_mission_mode`` and before the pad is
    resolved for the flight, on both the cold start and the standby ``go``.

    Parameters
    ----------
    params : MissionParameters
        Read only.

    Raises
    ------
    LaunchEnvelopeError
        With the path of the first offending field, checked in this order:
        ``rtl.enabled`` and ``platform.takeoff_from_ground`` not ``bool``;
        ``platform.height_m`` negative or not a finite number;
        ``kinematics.target_altitude_m`` not a finite number or outside
        :func:`altitude_envelope`.
    """
    _strict_bool(params.rtl, "enabled", True, "rtl.enabled")
    _strict_bool(params.platform, "takeoff_from_ground", False, "platform.takeoff_from_ground")

    height = getattr(params.platform, "height_m", 0.0)
    if not _finite_number(height) or height < 0.0:
        raise LaunchEnvelopeError("platform.height_m", f"altura do totem inválida: {height!r}")

    altitude = params.kinematics.target_altitude_m
    if not _finite_number(altitude):
        raise LaunchEnvelopeError("kinematics.target_altitude_m", f"altitude inválida: {altitude!r}")
    minimum, maximum = altitude_envelope(params)
    if altitude < minimum - _TOLERANCE_M:
        if launches_from_ground(params):
            reason = f"{altitude:.2f} m abaixo do mínimo de {minimum:.2f} m sobre o piso"
        else:
            reason = (
                f"{altitude:.2f} m abaixo do mínimo de {minimum:.2f} m sobre o totem "
                f"de {pad_height_m(params):.2f} m"
            )
        raise LaunchEnvelopeError("kinematics.target_altitude_m", reason)
    if altitude > maximum + _TOLERANCE_M:
        raise LaunchEnvelopeError(
            "kinematics.target_altitude_m", f"{altitude:.2f} m acima do máximo de {maximum:.2f} m"
        )
