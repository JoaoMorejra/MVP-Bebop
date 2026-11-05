"""Mission profiles.

Both profiles fly the same five deterministic stages and emit the same
``[STEP N: ...]`` markers; they differ in what stages 2 to 4 do:

* ``advanced``: the lateral search, the visual approach and the inspection.
* ``simple``: the airframe never translates. Stage 2 and 3 rotate about the
  vertical axis; stage 4 keeps the heading of the lock and hands over to
  stage 5 right after the capture.

What stage 5 does is a separate, per-flight choice, ``rtl.enabled``: the ArUco
return and precision landing on the pad (in the Simple profile, the centering
over the pad it never left), or a landing in place right after the evidence.
The former ``intermediate`` profile was exactly ``advanced`` without the
return; stored documents that name it are migrated on load
(``parameters.migrate_legacy_mission_mode``).

This module depends on nothing but the standard library so that the argument
parser, the parameter store and the tests can use it without importing the
flight stack.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Final, Optional, Union


class MissionMode(str, Enum):
    """The two selectable mission profiles."""

    SIMPLE = "simple"
    ADVANCED = "advanced"

    @property
    def translates(self) -> bool:
        """Whether the airframe moves horizontally in stages 2 and 3."""
        return self is not MissionMode.SIMPLE


logger = logging.getLogger("MissionMode")

#: Profile flown when nothing selects one. The complete mission.
DEFAULT_MODE: Final[MissionMode] = MissionMode.ADVANCED

#: Refusal of the removed profile. Stored documents never reach it: they are
#: migrated on load; only a ``--mode intermediate`` or an external document does.
INTERMEDIATE_REMOVED_MESSAGE: Final[str] = (
    "mission mode 'intermediate' was removed; stored documents migrate to 'advanced' with rtl.enabled=false"
)

#: Stage 5 marker names, by profile and return plan. The ``[STEP 5:`` prefix is
#: the IPC contract; the text after it is free.
_STAGE_FIVE_RETURN: Final[Dict[MissionMode, str]] = {
    MissionMode.ADVANCED: "ArUco RTL & Precision Landing",
    MissionMode.SIMPLE: "ArUco Centering & Base Landing",
}
_STAGE_FIVE_IN_PLACE: Final[str] = "Landing at Inspection Point"

#: Stage 5 as the station speaks it in a failure alert, by return plan.
_SPOKEN_STAGE_FIVE: Final[Dict[bool, str]] = {True: "Retorno à base", False: "Pouso no local"}

#: Height at which the Bebop 2 firmware ends its launch profile, metres. The
#: airframe ignores the altitude it is given and stabilizes here, and Stage 1
#: then acquires the target from it in either direction. Mirrors ``actuators.simulator.FIRMWARE_HOVER_ALTITUDE_M``
#: and ``parameter_conflicts.FIRMWARE_HOVER_HEIGHT_M`` (a test holds the three equal).
NATIVE_HOVER_HEIGHT_M: Final[float] = 1.0


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
        is worse than refusing to start. ``"intermediate"`` is refused with
        :data:`INTERMEDIATE_REMOVED_MESSAGE`.
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
    if token == "intermediate":
        raise ValueError(INTERMEDIATE_REMOVED_MESSAGE)
    try:
        return MissionMode(token)
    except ValueError as error:
        names = ", ".join(mode.value for mode in MissionMode)
        raise ValueError(f"mission mode '{value}' is not one of: {names}") from error


def stage_five_name(mode: MissionMode, rtl_enabled: bool) -> str:
    """Name of stage 5 for a profile and a return plan, without the ``STEP 5:`` prefix.

    Parameters
    ----------
    mode : MissionMode
    rtl_enabled : bool
        ``rtl.enabled`` of the flight.

    Returns
    -------
    str
        ``"ArUco RTL & Precision Landing"`` (Advanced with the return),
        ``"ArUco Centering & Base Landing"`` (Simple with the return) or
        ``"Landing at Inspection Point"`` (either profile without it).
    """
    return _STAGE_FIVE_RETURN[mode] if rtl_enabled else _STAGE_FIVE_IN_PLACE


def spoken_stage_five_name(rtl_enabled: bool) -> str:
    """Stage 5 as the station speaks it in a failure alert, in Portuguese."""
    return _SPOKEN_STAGE_FIVE[bool(rtl_enabled)]


def apply_mission_mode(params: Any, cli_mode: Optional[str]) -> MissionMode:
    """Resolve the profile of a run and write its canonical name back.

    Precedence: the ``--mode`` flag, then ``params.mission_mode`` (which already
    holds the parameter store merged with ``--params-json`` or the standby
    ``go`` document). The altitude envelope is not judged here:
    ``launch_envelope.validate_launch_envelope`` runs right after, with the
    same rules as the station.

    Parameters
    ----------
    params : MissionParameters
        Mutated: ``params.mission_mode`` becomes the lower-case canonical name,
        which is what gets persisted to ``mission_config.json``.
    cli_mode : str or None
        Value of ``--mode``.

    Returns
    -------
    MissionMode

    Raises
    ------
    TypeError, ValueError
        As :func:`parse_mission_mode`.
    """
    mode = parse_mission_mode(cli_mode if cli_mode is not None else getattr(params, "mission_mode", None))
    params.mission_mode = mode.value
    return mode


def apply_launch_surface(params: Any) -> None:
    """Resolve the launch surface and the pad for the flight, in memory.

    Call it after the parameters were persisted and after ``mission.mode`` was
    emitted: the stored document keeps the configured pad (``platform.height_m``
    stays the physical pad, D6). Then:

    * ``platform.takeoff_from_ground`` true: the airframe rests on the floor, so
      the pad takes no part in the flight. ``height_m`` becomes ``0`` and
      ``edge_ahead_m`` ``None`` (no step to guard).
    * otherwise, in the Simple profile: the airframe hovers over the pad top and
      never translates, so ``edge_ahead_m`` becomes ``None``. ``height_m`` is
      kept: the target in the control frame and the ground projection need it.

    The ceiling margin is not touched: the ceiling is the schedule's
    (``estimation.ceiling_schedule``), fixed on the pad top.

    Logs one structured line, ``[SURFACE] takeoff=platform|ground pad=..
    target_floor=.. target_rel=..``, with the pad of the flight.

    Parameters
    ----------
    params : MissionParameters
        Mutated in place (the ``platform`` and ``kinematics`` objects are
        shared with the supervisors and are never replaced). The configured pad
        height is kept in the plain attribute ``launch_pad_height_m_original``,
        which is not a dataclass field.
    """
    platform = params.platform
    kinematics = params.kinematics
    params.launch_pad_height_m_original = float(platform.height_m)
    ground = platform.takeoff_from_ground is True or float(platform.height_m) <= 0.0
    if platform.takeoff_from_ground is True:
        platform.height_m = 0.0
        platform.edge_ahead_m = None
    elif parse_mission_mode(getattr(params, "mission_mode", None)) is MissionMode.SIMPLE:
        platform.edge_ahead_m = None
    logger.info(
        "[SURFACE] takeoff=%s pad=%.2f target_floor=%.2f target_rel=%.2f",
        "ground" if ground else "platform",
        float(platform.height_m),
        float(kinematics.target_altitude_m),
        float(params.relative_target_altitude_m),
    )
