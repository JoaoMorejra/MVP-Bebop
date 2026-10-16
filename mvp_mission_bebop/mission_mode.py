"""Mission profiles.

All three profiles fly the same five deterministic stages and emit the same
``[STEP N: ...]`` markers; they differ in what stages 2 to 5 do:

* ``advanced``: the complete mission, ending with the ArUco return and landing
  on the launch pad.
* ``intermediate``: stages 1 to 4 as in ``advanced``; stage 5 lands vertically
  where the inspection ended.
* ``simple``: the airframe never translates. Stage 2 and 3 rotate about the
  vertical axis, stage 4 restores the takeoff heading after the capture and
  stage 5 centres on the pad marker and lands.

This module depends on nothing but the standard library so that the argument
parser, the parameter store and the tests can use it without importing the
flight stack.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Final, Optional, Union


class MissionMode(str, Enum):
    """The three selectable mission profiles."""

    SIMPLE = "simple"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"

    @property
    def translates(self) -> bool:
        """Whether the airframe moves horizontally in stages 2 and 3."""
        return self is not MissionMode.SIMPLE

    @property
    def returns_to_base(self) -> bool:
        """Whether stage 5 flies the reverse leg to the launch pad."""
        return self is MissionMode.ADVANCED


#: Profile flown when nothing selects one. The complete mission.
DEFAULT_MODE: Final[MissionMode] = MissionMode.ADVANCED

#: Height at which the Bebop 2 firmware ends its launch profile, metres. The
#: airframe ignores the altitude it is given and stabilizes here; the Simple
#: profile flies at it. Mirrors ``actuators.simulator.FIRMWARE_HOVER_ALTITUDE_M``
#: and ``parameter_conflicts.FIRMWARE_HOVER_HEIGHT_M`` (a test holds the three equal).
NATIVE_HOVER_HEIGHT_M: Final[float] = 1.0

#: Safety headroom above the target altitude in the Simple profile, metres. The
#: factory value of ``kinematics.altitude_ceiling_margin_m``; the profile does
#: not expose the margin, so a stored value cannot weaken the ceiling.
SIMPLE_CEILING_MARGIN_M: Final[float] = 0.60

#: Lowest target altitude of the Simple profile, metres. The Bebop 2 native
#: takeoff range is ``[MIN_SIMPLE_ALTITUDE_M, NATIVE_HOVER_HEIGHT_M]``; the value
#: is absolute and independent of the launch pad.
MIN_SIMPLE_ALTITUDE_M: Final[float] = 0.1


def parse_mission_mode(
    value: Union[str, MissionMode, None], default: MissionMode = DEFAULT_MODE
) -> MissionMode:
    """Read a mission profile from a configuration value.

    Parameters
    ----------
    value : str, MissionMode or None
        Raw value. ``None`` and a blank string select ``default``. A string is
        matched case-insensitively after trimming.
    default : MissionMode
        Profile returned when ``value`` is absent.

    Returns
    -------
    MissionMode
        The selected profile.

    Raises
    ------
    TypeError
        If ``value`` is neither a string nor a :class:`MissionMode`.
    ValueError
        If a non-blank string names no profile. An unknown name is never
        replaced by the default: flying a profile the operator did not ask for
        is worse than refusing to start.
    """
    if value is None:
        return default
    if isinstance(value, MissionMode):
        return value
    if not isinstance(value, str):
        raise TypeError(f"mission mode must be a string, got {type(value).__name__}")
    token = value.strip().lower()
    if not token:
        return default
    try:
        return MissionMode(token)
    except ValueError as error:
        names = ", ".join(mode.value for mode in MissionMode)
        raise ValueError(f"mission mode '{value}' is not one of: {names}") from error


def apply_mission_mode(params: Any, cli_mode: Optional[str]) -> MissionMode:
    """Resolve the profile of a run and write its canonical name back.

    Precedence: the ``--mode`` flag, then ``params.mission_mode`` (which already
    holds the parameter store merged with ``--params-json`` or the standby
    ``go`` document).

    Parameters
    ----------
    params : MissionParameters
        Mutated: ``params.mission_mode`` becomes the lower-case canonical name,
        which is what gets persisted to ``mission_config.json``. In the simple
        profile the target altitude must lie in the native takeoff range and the
        ceiling margin is fixed.
    cli_mode : str or None
        Value of ``--mode``.

    Returns
    -------
    MissionMode

    Raises
    ------
    TypeError, ValueError
        As :func:`parse_mission_mode`.
    ValueError
        If the simple profile is selected with ``kinematics.target_altitude_m``
        not finite or outside ``[MIN_SIMPLE_ALTITUDE_M, NATIVE_HOVER_HEIGHT_M]``.
        ``platform.height_m`` takes no part in the check.
    """
    mode = parse_mission_mode(cli_mode if cli_mode is not None else getattr(params, "mission_mode", None))
    params.mission_mode = mode.value
    if mode is MissionMode.SIMPLE:
        kinematics = params.kinematics
        altitude = float(kinematics.target_altitude_m)
        if not math.isfinite(altitude):
            raise ValueError(f"kinematics.target_altitude_m={altitude:g} is not a finite height")
        if not (MIN_SIMPLE_ALTITUDE_M - 1e-9 <= altitude <= NATIVE_HOVER_HEIGHT_M + 1e-9):
            raise ValueError(
                f"kinematics.target_altitude_m={altitude:g} is outside the native takeoff range "
                f"[{MIN_SIMPLE_ALTITUDE_M:g}, {NATIVE_HOVER_HEIGHT_M:g}] m of the simple profile"
            )
        kinematics.altitude_ceiling_margin_m = SIMPLE_CEILING_MARGIN_M
    return mode


def neutralize_launch_pad(params: Any) -> None:
    """Make the Simple profile fly independently of the launch pad, in memory.

    The airframe does not translate, so the pad height and the pad edge guard
    are irrelevant to altitude control. Call it after the parameters were
    persisted: the stored document keeps the configured pad. The firmware's
    native takeoff climbs to :data:`NATIVE_HOVER_HEIGHT_M` whatever the target,
    so the ceiling margin is widened to keep ``target + margin`` at the native
    height plus :data:`SIMPLE_CEILING_MARGIN_M`.

    Parameters
    ----------
    params : MissionParameters
        Mutated in place (the ``platform`` and ``kinematics`` objects are
        shared with the supervisors and are never replaced). The configured pad
        height is kept in the plain attribute ``launch_pad_height_m_original``,
        which is not a dataclass field. No-op unless the profile is simple.
    """
    if parse_mission_mode(getattr(params, "mission_mode", None)) is not MissionMode.SIMPLE:
        return
    platform = params.platform
    kinematics = params.kinematics
    params.launch_pad_height_m_original = float(platform.height_m)
    platform.height_m = 0.0
    platform.edge_ahead_m = None
    kinematics.altitude_ceiling_margin_m = SIMPLE_CEILING_MARGIN_M + max(
        0.0, NATIVE_HOVER_HEIGHT_M - float(kinematics.target_altitude_m)
    )
