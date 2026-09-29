"""Boot-time audit of the operational configuration against its safe envelopes.

``mission_config.json`` is rewritten by every run and edited by the station, a
text editor and ``--params-json`` alike, so the values a flight actually uses
can drift away from the envelopes the ``MissionParameters`` comments document
as safe. Nothing here aborts: the audit logs one structured warning per
divergence, on the mission's stdout, and the operator decides.

Each check below restates one "Safe envelope" note from
:mod:`mvp_mission_bebop.parameters`; a check without such a note does not
belong here.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Final, List, Optional, Tuple, Union

from mvp_mission_bebop.parameters import MissionParameters

logger = logging.getLogger("ConfigAudit")

#: Structured-log prefix of a divergence line.
ENVELOPE_TAG: Final[str] = "CONFIG ENVELOPE"

#: Documented bounds of ``timeouts.video_stream_timeout_sec``, in seconds.
VIDEO_TIMEOUT_ENVELOPE_SEC: Final[Tuple[float, float]] = (1.5, 2.0)

MarkerResolver = Callable[[Union[int, str]], Optional[int]]


@dataclass(frozen=True)
class EnvelopeDivergence:
    """One operational value outside its documented safe envelope."""

    #: Dotted path of the field in the ``MissionParameters`` document.
    path: str
    value: Any
    #: The envelope, as the dataclass comment states it.
    envelope: str
    #: What the divergence costs in flight.
    consequence: str

    def as_log_line(self) -> str:
        """Render as ``[CONFIG ENVELOPE] {json}`` on a single line."""
        value = self.value
        if isinstance(value, float) and not math.isfinite(value):
            value = repr(value)
        body = json.dumps(
            {
                "path": self.path,
                "value": value,
                "envelope": self.envelope,
                "consequence": self.consequence,
            },
            default=repr,
            ensure_ascii=False,
            sort_keys=True,
        )
        return f"[{ENVELOPE_TAG}] {body}"


def _real(value: Any) -> Optional[float]:
    """``value`` as a finite float, or ``None`` if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _default_marker_resolver(marker_dict: Union[int, str]) -> Optional[int]:
    """The resolver Stage 5 builds its detector through, so both agree."""
    from mvp_mission_bebop.steps.rtl import _resolve_marker_dict

    return _resolve_marker_dict(marker_dict)


def _dictionary_size(enum_code: int) -> Optional[int]:
    """Number of markers in an OpenCV predefined dictionary, if it can be read."""
    try:
        import cv2

        return int(cv2.aruco.getPredefinedDictionary(int(enum_code)).bytesList.shape[0])
    except Exception:  # noqa: BLE001 - size is a refinement, not a requirement
        return None


def audit_safe_envelopes(
    params: MissionParameters,
    marker_resolver: Optional[MarkerResolver] = None,
) -> List[EnvelopeDivergence]:
    """Compare the operational values against their documented safe envelopes.

    Parameters
    ----------
    params : MissionParameters
        The merged configuration the mission is about to fly: file, then
        ``--params-json``, then CLI flags.
    marker_resolver : Optional[MarkerResolver]
        Maps ``rtl.marker_dict`` to an OpenCV dictionary enum, or ``None`` when
        it cannot. Defaults to the resolver Stage 5 uses.

    Returns
    -------
    List[EnvelopeDivergence]
        One entry per divergence, in declaration order. Empty when nominal.
    """
    resolve = marker_resolver or _default_marker_resolver
    found: List[EnvelopeDivergence] = []

    def diverge(path: str, value: Any, envelope: str, consequence: str) -> None:
        found.append(EnvelopeDivergence(path, value, envelope, consequence))

    gimbal, vision, kinematics = params.gimbal, params.vision, params.kinematics
    rtl, timeouts = params.rtl, params.timeouts

    nadir = _real(gimbal.nadir_tilt_deg)
    search = _real(gimbal.search_tilt_deg)
    if nadir is None or nadir < -90.0 or (search is not None and nadir >= search):
        diverge(
            "gimbal.nadir_tilt_deg",
            gimbal.nadir_tilt_deg,
            f"[-90, search_tilt_deg={gimbal.search_tilt_deg})",
            "the approach cannot reach its inspection attitude",
        )

    altitude = _real(kinematics.target_altitude_m)
    floor = max(
        _real(vision.min_altitude_for_ibvs_m) or 0.0,
        _real(kinematics.takeoff_settle_min_altitude_m) or 0.0,
    )
    if altitude is None or altitude <= floor:
        diverge(
            "kinematics.target_altitude_m",
            kinematics.target_altitude_m,
            f"> {floor:g} (min_altitude_for_ibvs_m, takeoff_settle_min_altitude_m)",
            "the approach flies the open-loop ramp and the takeoff gate cannot settle",
        )

    demands = [
        _real(kinematics.forward_cruise_velocity),
        _real(kinematics.max_approach_forward_speed),
        _real(rtl.max_speed),
    ]
    try:
        demands.append(max(abs(float(v)) for v in params.lateral_pid.output_limits))
    except (TypeError, ValueError):
        pass
    required = max((abs(d) for d in demands if d is not None), default=0.0)
    ceiling = _real(kinematics.max_horizontal_speed)
    if ceiling is None or ceiling < required or ceiling >= 1.0:
        diverge(
            "kinematics.max_horizontal_speed",
            kinematics.max_horizontal_speed,
            f"[{required:g}, 1.0)",
            "the envelope either clips legitimate guidance or no longer bounds a defect",
        )

    enum_code = resolve(rtl.marker_dict)
    if enum_code is None:
        diverge(
            "rtl.marker_dict",
            rtl.marker_dict,
            "a dictionary the Stage 5 resolver accepts",
            "Stage 5 cannot build the marker detector and returns on odometry alone",
        )

    marker_id = rtl.target_aruco_id
    size = None if enum_code is None else _dictionary_size(enum_code)
    if (
        isinstance(marker_id, bool)
        or not isinstance(marker_id, int)
        or marker_id < 0
        or (size is not None and marker_id >= size)
    ):
        diverge(
            "rtl.target_aruco_id",
            marker_id,
            "integer >= 0" + ("" if size is None else f" and < {size}"),
            "the pad marker can never be matched",
        )

    tag = _real(rtl.tag_size)
    if tag is None or tag <= 0.0:
        diverge("rtl.tag_size", rtl.tag_size, "> 0", "the marker pose estimate has no scale")

    return_tilt = _real(rtl.camera_tilt_deg)
    if return_tilt is None or not -90.0 < return_tilt < 0.0:
        diverge(
            "rtl.camera_tilt_deg",
            rtl.camera_tilt_deg,
            "(-90, 0)",
            "the reverse cruise has no forward footprint in which to find the pad",
        )

    reverse = _real(rtl.reverse_cruise_velocity)
    if reverse is None or reverse >= 0.0:
        diverge(
            "rtl.reverse_cruise_velocity",
            rtl.reverse_cruise_velocity,
            "< 0",
            "the marker search is saturated to a standstill",
        )

    radius = _real(rtl.landing_radius_m)
    tolerance = _real(rtl.centering_tolerance_m)
    if radius is None or (tolerance is not None and radius < tolerance):
        diverge(
            "rtl.landing_radius_m",
            rtl.landing_radius_m,
            f">= centering_tolerance_m={rtl.centering_tolerance_m}",
            "landing waits on a tolerance tighter than the centering law converges to",
        )

    video = _real(timeouts.video_stream_timeout_sec)
    low, high = VIDEO_TIMEOUT_ENVELOPE_SEC
    if video is None or not low <= video <= high:
        diverge(
            "timeouts.video_stream_timeout_sec",
            timeouts.video_stream_timeout_sec,
            f"[{low:g}, {high:g}]",
            "a camera stream loss is acted on too early or too late",
        )

    return found


def log_envelope_divergences(
    params: MissionParameters,
    marker_resolver: Optional[MarkerResolver] = None,
) -> List[EnvelopeDivergence]:
    """Audit ``params`` and log each divergence as a structured warning.

    Never raises: an audit fault is logged and the mission continues, since
    nothing here is a reason to keep the aircraft on the ground.

    Returns
    -------
    List[EnvelopeDivergence]
        What was logged; empty when nominal or when the audit itself failed.
    """
    try:
        found = audit_safe_envelopes(params, marker_resolver)
    except Exception as exc:  # noqa: BLE001 - the audit is advisory
        logger.warning("Configuration envelope audit failed: %s", exc)
        return []
    for divergence in found:
        logger.warning("%s", divergence.as_log_line())
    if not found:
        logger.info("Configuration inside every documented safe envelope.")
    return found
