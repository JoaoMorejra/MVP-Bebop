"""Altitude ceiling of a flight, with a ratchet through the takeoff transient.

Everything is in the control frame, above the launch surface. With ``h_p`` the
effective pad height (0 from the floor), ``z*`` the target in that frame,
``m`` the operator margin, ``m_T`` :data:`TOTEM_CEILING_HEADROOM_M`, ``H_fw``
the native takeoff height, ``V`` the venue ceiling over the floor and ``a`` the
edge-guard allowance:

    floor (ground) : c_n = z* + m
    pad top (totem): c_n = max(z*, H_fw) + m_T        (D2; m is ignored)
    pad top, target over the floor above TOTEM_MARGIN_UNLOCK_ABOVE_M:
                     c_n = max(z*, H_fw) + m
    edge guard     : c_n += a   while the guard is active
    venue          : c   = min(c_n, V - h_p)          (always)

The firmware ends its launch profile about ``H_fw`` above the launch surface
whatever the target, so a ceiling tied to ``z*`` alone tripped right after the
takeoff whenever ``z* + m`` sat close to ``H_fw`` (field symptom A). Between
:meth:`CeilingSchedule.arm` and :meth:`CeilingSchedule.release` the ceiling is
a ratchet instead, with ``m_e`` the effective margin (``m`` on the floor and
on the pad top above the unlock altitude, ``m_T`` otherwise):

    arm()      : c_t <- max(c_n, H_fw + m_e)
    update(z)  : c_t <- min(c_t, max(c_n, z + m_e))   (non-increasing)
    release()  : the transient ends; value() = c

While the firmware flies its own launch profile (from :meth:`arm` until
:meth:`end_native_profile`) only the venue ceiling applies, and the ratchet
does not move: an emergency landing commanded inside the profile is worse than
letting it finish, unless the venue is at stake.

Pure: no lock, no clock, no ROS. The configuration objects are read on every
call, so a pad resolved in memory after construction
(``mission_mode.apply_launch_surface``) is honoured. The caller serializes
access (``OdometrySupervisor`` holds its state lock).
"""

from __future__ import annotations

import enum
import math
from typing import Any, Final, Optional

from mvp_mission_bebop.launch_envelope import TOTEM_MARGIN_UNLOCK_ABOVE_M
from mvp_mission_bebop.mission_mode import NATIVE_HOVER_HEIGHT_M

#: Fixed headroom above ``max(z*, H_fw)`` on the pad top, in metres (D2). The
#: operator margin does not apply there while the target over the floor is at
#: or below :data:`TOTEM_MARGIN_UNLOCK_ABOVE_M`: with it, a target close to the
#: native height left the end of the native profile a few centimetres under the
#: ceiling. With the defaults the pad-top ceiling is 1.60 m above the pad,
#: capped by the venue ceiling.
TOTEM_CEILING_HEADROOM_M: Final[float] = 0.60


class CeilingPhase(enum.Enum):
    """Where the takeoff transient stands."""

    #: Not armed, or released: the nominal ceiling applies.
    NOMINAL = "nominal"
    #: The firmware's launch profile: only the venue ceiling applies.
    NATIVE = "native"
    #: Altitude acquisition after the profile: the ratchet applies.
    RATCHET = "ratchet"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class CeilingSchedule:
    """Ceiling of the control frame through the takeoff transient and after it.

    Parameters
    ----------
    kinematics_cfg : FlightKinematicsConfig
        ``target_altitude_m`` (over the floor) and ``altitude_ceiling_margin_m``.
    platform_cfg : PlatformConfig
        ``height_m``, ``takeoff_from_ground``, ``venue_ceiling_agl_m`` and
        ``ceiling_allowance_m``.
    native_height_m : float
        Height at which the firmware ends its launch profile, ``H_fw``.
    """

    def __init__(self, kinematics_cfg: Any, platform_cfg: Any, native_height_m: float = NATIVE_HOVER_HEIGHT_M) -> None:
        if not _finite(native_height_m) or native_height_m <= 0.0:
            raise ValueError(f"native_height_m must be a positive finite height, got {native_height_m!r}")
        self.kinematics_cfg = kinematics_cfg
        self.platform_cfg = platform_cfg
        self.native_height_m = float(native_height_m)
        self._phase = CeilingPhase.NOMINAL
        self._transient: Optional[float] = None

    # -------------------------------------------------------------- geometry

    @property
    def on_pad_top(self) -> bool:
        """Whether the flight takes off from the top of a pad."""
        platform = self.platform_cfg
        if getattr(platform, "takeoff_from_ground", False) is True:
            return False
        return self.pad_height_m > 0.0

    @property
    def pad_height_m(self) -> float:
        """Effective pad height ``h_p``, metres: 0 from the floor."""
        platform = self.platform_cfg
        if getattr(platform, "takeoff_from_ground", False) is True:
            return 0.0
        height = getattr(platform, "height_m", 0.0)
        return max(0.0, float(height)) if _finite(height) else 0.0

    @property
    def target_m(self) -> float:
        """Target in the control frame, ``z*``."""
        return float(self.kinematics_cfg.target_altitude_m) - self.pad_height_m

    @property
    def margin_unlocked(self) -> bool:
        """Whether the operator margin applies: from the floor, or a target over the floor above the unlock altitude.

        Mirrors ``launch_envelope.margin_unlocked`` on the resolved pad. A
        target that is not a finite number keeps the pad top locked.
        """
        if not self.on_pad_top:
            return True
        target = getattr(self.kinematics_cfg, "target_altitude_m", None)
        return _finite(target) and target > TOTEM_MARGIN_UNLOCK_ABOVE_M + 1e-9

    @property
    def effective_margin_m(self) -> float:
        """``m_e``: the operator margin when :attr:`margin_unlocked`, the fixed headroom otherwise."""
        if self.margin_unlocked:
            return float(self.kinematics_cfg.altitude_ceiling_margin_m)
        return TOTEM_CEILING_HEADROOM_M

    @property
    def venue_limit_m(self) -> float:
        """``V - h_p``; infinite when the venue ceiling is unknown."""
        venue = getattr(self.platform_cfg, "venue_ceiling_agl_m", None)
        if not _finite(venue):
            return math.inf
        return float(venue) - self.pad_height_m

    def nominal_m(self, guard_active: bool = False) -> float:
        """``c_n``, before the venue cap."""
        if self.on_pad_top:
            ceiling = max(self.target_m, self.native_height_m) + self.effective_margin_m
        else:
            ceiling = self.target_m + float(self.kinematics_cfg.altitude_ceiling_margin_m)
        if guard_active:
            allowance = getattr(self.platform_cfg, "ceiling_allowance_m", 0.0)
            ceiling += max(0.0, float(allowance)) if _finite(allowance) else 0.0
        return ceiling

    # ------------------------------------------------------------- transient

    @property
    def phase(self) -> CeilingPhase:
        return self._phase

    @property
    def armed(self) -> bool:
        """Whether the takeoff transient is in force (native profile or ratchet)."""
        return self._phase is not CeilingPhase.NOMINAL

    def arm(self) -> None:
        """Open the transient before the takeoff command: ``c_t = max(c_n, H_fw + m_e)``."""
        self._transient = max(self.nominal_m(False), self.native_height_m + self.effective_margin_m)
        self._phase = CeilingPhase.NATIVE

    def end_native_profile(self) -> None:
        """The firmware's launch profile is over: the ratchet takes over. No-op unless native."""
        if self._phase is CeilingPhase.NATIVE:
            self._phase = CeilingPhase.RATCHET

    def update(self, relative_altitude_m: float, guard_active: bool = False) -> None:
        """Feed one sample: ``c_t = min(c_t, max(c_n, z + m_e))`` in the ratchet phase only.

        Samples of the native profile, of the ground and non-finite ones never
        move the ratchet.
        """
        if self._phase is not CeilingPhase.RATCHET or self._transient is None:
            return
        if not _finite(relative_altitude_m):
            return
        floor = max(self.nominal_m(guard_active), float(relative_altitude_m) + self.effective_margin_m)
        self._transient = min(self._transient, floor)

    def release(self) -> None:
        """Close the transient; :meth:`value` is the nominal ceiling from now on."""
        self._phase = CeilingPhase.NOMINAL
        self._transient = None

    # ----------------------------------------------------------------- value

    def value(self, guard_active: bool = False) -> float:
        """The ceiling in force, in metres above the launch surface.

        ``V - h_p`` in the native profile; ``min(c_t, V - h_p)`` in the
        ratchet; ``min(c_n, V - h_p)`` otherwise. Infinite only in the native
        profile with no known venue ceiling.
        """
        venue = self.venue_limit_m
        if self._phase is CeilingPhase.NATIVE:
            return venue
        if self._phase is CeilingPhase.RATCHET and self._transient is not None:
            return min(self._transient, venue)
        return min(self.nominal_m(guard_active), venue)
