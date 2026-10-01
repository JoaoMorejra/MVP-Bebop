"""Payload of the ``mission.parameters`` milestone.

Decision D2: every number the copilot speaks that corresponds to a mission
parameter is the value configured for the mission being flown -- the document
``mission.py`` merged from ``mission_config.json`` and ``--params-json`` --
never telemetry and never a default held by the ground station. The mission
emits that document once, before Stage 1, and the station formats the numbers
it speaks from it and from the milestones that repeat them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Final, Tuple

if TYPE_CHECKING:
    from mvp_mission_bebop.estimation.calibration import SpeedCalibration
    from mvp_mission_bebop.parameters import MissionParameters

#: Keys of the payload, in emission order. IPC contract with
#: ``src/lib/copilotPhrases.ts:phraseValues``.
SPOKEN_PARAMETER_KEYS: Final[Tuple[str, ...]] = (
    "target_altitude_m",
    "cruise_mps",
    "rtl_mps",
    "search_timeout_sec",
    "hover_duration_sec",
    "confidence",
    "arrival_radius_m",
    "battery_land_pct",
    "countdown_sec",
)


def spoken_parameters(
    params: "MissionParameters", calibration: "SpeedCalibration"
) -> Dict[str, float]:
    """Effective mission parameters as the station may speak them.

    Parameters
    ----------
    params : MissionParameters
        The merged document the steps fly.
    calibration : SpeedCalibration
        Converts the normalized cruise commands the document holds into metres
        per second, the unit the copilot speaks.

    Returns
    -------
    dict of str to float
        One entry per :data:`SPOKEN_PARAMETER_KEYS`, in that order. Speeds are
        rounded to 3 decimals, everything else to 2.
    """
    kinematics = params.kinematics
    return {
        "target_altitude_m": round(float(kinematics.target_altitude_m), 2),
        "cruise_mps": round(float(calibration.to_mps(kinematics.forward_cruise_velocity)), 3),
        "rtl_mps": round(float(calibration.to_mps(params.rtl.max_speed)), 3),
        "search_timeout_sec": round(float(params.timeouts.search_timeout_sec), 2),
        "hover_duration_sec": round(float(kinematics.hover_duration_sec), 2),
        "confidence": round(float(params.vision.confidence_threshold), 2),
        "arrival_radius_m": round(float(params.rtl.arrival_radius_m), 2),
        "battery_land_pct": round(float(params.battery.land_pct), 2),
        "countdown_sec": round(float(kinematics.countdown_sec), 2),
    }
