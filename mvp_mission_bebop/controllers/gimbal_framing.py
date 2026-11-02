"""Stage 4 framing law: point the optical axis at the target with the gimbal alone.

In the lateral flight pattern the airframe never advances on the target. Stage 3
nulls the horizontal pixel error with ``vy`` and freezes, so the horizontal range
``R`` from the airframe to the target is the range at which Stage 2 detected it.
A fixed nadir attitude frames the target only when ``R = h / tan(|tilt|)``; for any
other range it would push the target out of a 50 degree vertical field of view.
Stage 4 therefore closes the loop on the vertical pixel error with the pitch axis
of the gimbal, the only actuator allowed to move while the airframe hovers.

The law is the exact pinhole inversion::

    theta_y = atan((v - c_y) / c_y * tan(vfov / 2))      positive below the axis
    tilt_d  = tilt - degrees(theta_y)
    tilt'   = tilt + k * (tilt_d - tilt)                 k = gimbal.tracking_gain

followed by the slew limit ``|tilt' - tilt| <= gimbal.max_slew_limit_deg * dt`` and
the band ``[gimbal.nadir_tilt_deg, gimbal.search_tilt_deg]``. The gimbal tilt is
negative below the horizon, so a target below the axis (``theta_y > 0``) pitches
the camera down. ``theta_y`` is independent of altitude and range: the airframe
holds still and the target does not move, so the bearing measured in one frame is
the whole correction, and ``k = 1`` reaches the fixed point in one step whenever
the slew limit does not bind.

``CameraIntrinsics.bearing`` (``controllers.geometry``) supplies ``theta_y``. The
Nectar SDK's ``ImageCalculus`` linearises the pixel-to-angle conversion and is
documented for small attitudes only; the reasons are recorded in that module.

The module is pure: no wall clock, no ROS. The caller supplies ``dt``.
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from typing import Any, Final, Optional, Tuple

from mvp_mission_bebop.controllers.geometry import CameraIntrinsics
from mvp_mission_bebop.parameters import GimbalConstraintsConfig, VisionConfig

#: Absolute tolerance used when comparing a tilt against the band edges, in
#: degrees. Absorbs the rounding of ``tilt + step`` so a tilt that is exactly on
#: the floor is reported as such.
_BAND_EPSILON_DEG: Final[float] = 1e-9


@dataclass(frozen=True)
class FramingCommand:
    """Output of one cycle of the framing law."""

    #: Absolute gimbal tilt to command, in degrees (negative below the horizon).
    tilt_deg: float
    #: Vertical pixel error of the frame, positive below the centre; ``nan``
    #: when the frame carries no target.
    vertical_error_px: float
    #: True once the target is framed: centred vertically for the dwell, or
    #: the tilt has reached the floor with the target still in view.
    framed: bool
    #: True when the tilt is at ``nadir_tilt_deg`` within ``nadir_tilt_tolerance_deg``.
    at_floor: bool
    #: Human-readable reason, for the log and the stream caption.
    note: str


class GimbalFramingController:
    """Closed-loop vertical pointing of the gimbal for the Stage 4 inspection.

    Parameters
    ----------
    gimbal_cfg : GimbalConstraintsConfig
        Band (``nadir_tilt_deg``, ``search_tilt_deg``), floor tolerance, slew
        limit and tracking gain.
    vision_cfg : VisionConfig
        Field of view, optical-centre tolerance and alignment dwell.

    Raises
    ------
    TypeError
        If either configuration is not of the expected type.
    ValueError
        If the band is empty or not finite, or the slew limit is not a finite
        positive rate.
    """

    def __init__(self, gimbal_cfg: GimbalConstraintsConfig, vision_cfg: VisionConfig) -> None:
        if not isinstance(gimbal_cfg, GimbalConstraintsConfig):
            raise TypeError(
                f"gimbal_cfg must be a GimbalConstraintsConfig, got {type(gimbal_cfg)!r}"
            )
        if not isinstance(vision_cfg, VisionConfig):
            raise TypeError(f"vision_cfg must be a VisionConfig, got {type(vision_cfg)!r}")
        floor = _finite("gimbal.nadir_tilt_deg", gimbal_cfg.nadir_tilt_deg)
        ceiling = _finite("gimbal.search_tilt_deg", gimbal_cfg.search_tilt_deg)
        if not (-90.0 <= floor <= 20.0):
            raise ValueError(
                f"gimbal.nadir_tilt_deg ({floor}) outside mechanical envelope [-90, 20]"
            )
        if not (-90.0 <= ceiling <= 20.0):
            raise ValueError(
                f"gimbal.search_tilt_deg ({ceiling}) outside mechanical envelope [-90, 20]"
            )
        slew = _finite("gimbal.max_slew_limit_deg", gimbal_cfg.max_slew_limit_deg)
        if slew <= 0.0:
            raise ValueError(f"gimbal.max_slew_limit_deg must be positive, got {slew!r}")

        self.gimbal_cfg = gimbal_cfg
        self.vision_cfg = vision_cfg
        self.min_tilt_deg: float = min(floor, ceiling)
        self.max_tilt_deg: float = max(floor, ceiling)
        self.is_fixed_tilt: bool = abs(floor - ceiling) < 1e-3
        self.final_tilt_deg: float = floor
        self.start_tilt_deg: float = ceiling
        #: Consecutive frames with the vertical error inside the tolerance.
        self._centred_frames: int = 0

    def reset(self) -> None:
        """Clear the centring dwell. Call once at the start of each framing phase."""
        self._centred_frames = 0

    # ------------------------------------------------------------------ law

    def open_loop_tilt(
        self,
        confirmed_px: Tuple[float, float],
        confirmed_tilt_deg: float,
        frame_dimensions: Tuple[int, int],
    ) -> float:
        """Pointing solution from a single past observation, without feedback.

        The fixed point of :meth:`compute` for a target seen at ``confirmed_px``
        while the gimbal was at ``confirmed_tilt_deg``, bounded to the band. The
        slew limit does not apply: this is a setpoint, not a step. With the
        airframe frozen and the target static it remains valid for the whole
        stage, which is why it is both the starting setpoint and the fallback
        when no detection arrives.

        Parameters
        ----------
        confirmed_px : Tuple[float, float]
            Last measured target centre, in pixels.
        confirmed_tilt_deg : float
            Gimbal tilt at which that pixel was measured, in degrees.
        frame_dimensions : Tuple[int, int]
            ``(width, height)`` of the frame the pixel belongs to.

        Returns
        -------
        float
            Tilt in degrees, inside ``[nadir_tilt_deg, search_tilt_deg]``.

        Raises
        ------
        TypeError
            If ``confirmed_px`` is not a pair of real numbers.
        ValueError
            If any input is not finite or the frame dimensions are not positive.
        """
        width, height = _validated_frame(frame_dimensions)
        pixel = _validated_pixel(confirmed_px)
        tilt = _finite("confirmed_tilt_deg", confirmed_tilt_deg)
        return self._bounded(self._pointing(width, height, pixel, tilt))

    def compute(
        self,
        target_center: Optional[Tuple[float, float]],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        dt: float,
    ) -> FramingCommand:
        """One cycle of the framing law, called once per new perception frame.

        Parameters
        ----------
        target_center : Optional[Tuple[float, float]]
            Measured target centre in pixels, or ``None`` when the frame carries
            no target. Without a target the tilt is held and the dwell restarts.
        frame_dimensions : Tuple[int, int]
            ``(width, height)`` of the frame.
        current_tilt_deg : float
            Tilt currently commanded to the gimbal, in degrees.
        dt : float
            Time since the previous frame, in seconds. Scales the slew limit.

        Returns
        -------
        FramingCommand
            The tilt to command and the framing verdict.

        Raises
        ------
        TypeError
            If ``target_center`` is neither ``None`` nor a pair of real numbers.
        ValueError
            If ``dt`` is not finite and positive, the frame dimensions are not
            positive, or any coordinate or angle is not finite.
        """
        width, height = _validated_frame(frame_dimensions)
        current = _finite("current_tilt_deg", current_tilt_deg)
        step_dt = _finite("dt", dt)
        if step_dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt!r}")

        if self.is_fixed_tilt:
            return FramingCommand(
                tilt_deg=current,
                vertical_error_px=0.0 if target_center is not None else float("nan"),
                framed=True,
                at_floor=True,
                note="fixed tilt: held",
            )

        if target_center is None:
            self._centred_frames = 0
            at_target = self._at_floor(current)
            return FramingCommand(
                tilt_deg=current,
                vertical_error_px=float("nan"),
                framed=at_target,
                at_floor=at_target,
                note="no target in frame: tilt held"
                if not at_target
                else f"framed at final tilt {self.final_tilt_deg:.1f} deg",
            )

        pixel = _validated_pixel(target_center)
        err_y = pixel[1] - height / 2.0
        desired = self._pointing(width, height, pixel, current)
        gain = float(self.gimbal_cfg.tracking_gain)
        tilt = self._slewed(current, current + gain * (desired - current), step_dt)

        if abs(err_y) <= self._tolerance_px(width):
            self._centred_frames += 1
        else:
            self._centred_frames = 0

        at_floor = self._at_floor(tilt)
        centred = self._centred_frames >= self._dwell_cycles()
        if centred:
            note = f"framed: |err_y| {abs(err_y):.0f} px for {self._centred_frames} frames"
        elif at_floor:
            note = f"framed at final tilt {self.final_tilt_deg:.1f} deg"
        else:
            note = f"pointing: err_y {err_y:+.0f} px"
        return FramingCommand(
            tilt_deg=tilt,
            vertical_error_px=err_y,
            framed=centred or at_floor,
            at_floor=at_floor,
            note=note,
        )

    def slew_toward(self, current_tilt_deg: float, goal_tilt_deg: float, dt: float) -> float:
        """Advance from ``current_tilt_deg`` toward a setpoint under the slew limit.

        Used to carry the gimbal to the :meth:`open_loop_tilt` setpoint before the
        first frame with a target arrives, under the same rate and band limits as
        :meth:`compute`.

        Parameters
        ----------
        current_tilt_deg : float
            Tilt currently commanded, in degrees.
        goal_tilt_deg : float
            Setpoint, in degrees.
        dt : float
            Time since the previous command, in seconds.

        Returns
        -------
        float
            Next tilt, in degrees, inside the band.

        Raises
        ------
        ValueError
            If any input is not finite or ``dt`` is not positive.
        """
        current = _finite("current_tilt_deg", current_tilt_deg)
        goal = _finite("goal_tilt_deg", goal_tilt_deg)
        step_dt = _finite("dt", dt)
        if step_dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt!r}")
        if self.is_fixed_tilt:
            return current
        return self._slewed(current, goal, step_dt)

    # -------------------------------------------------------------- helpers

    def _pointing(
        self, width: int, height: int, pixel: Tuple[float, float], tilt_deg: float
    ) -> float:
        """Tilt that puts ``pixel`` on the optical axis, before any limit."""
        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            horizontal_fov_deg=self.vision_cfg.horizontal_fov_deg,
            vertical_fov_deg=self.vision_cfg.vertical_fov_deg,
        )
        _theta_x, theta_y = intrinsics.bearing(pixel)
        return tilt_deg - math.degrees(theta_y)

    def _slewed(self, current: float, target: float, dt: float) -> float:
        """Rate-limit the step from ``current`` to ``target``, then bound it."""
        max_step = float(self.gimbal_cfg.max_slew_limit_deg) * dt
        step = max(-max_step, min(max_step, target - current))
        return self._bounded(current + step)

    def _bounded(self, tilt: float) -> float:
        return max(self.min_tilt_deg, min(self.max_tilt_deg, tilt))

    def _at_floor(self, tilt: float) -> bool:
        if self.is_fixed_tilt:
            return True
        tolerance = max(0.0, float(self.gimbal_cfg.nadir_tilt_tolerance_deg))
        target = self.final_tilt_deg
        start = self.start_tilt_deg
        if abs(start - target) <= tolerance + _BAND_EPSILON_DEG:
            return abs(tilt - target) <= 1e-3
        if start > target:
            return tilt <= target + tolerance + _BAND_EPSILON_DEG
        return tilt >= target - tolerance - _BAND_EPSILON_DEG

    def _tolerance_px(self, width: int) -> float:
        """Optical-centre gate, the same one the Stage 3 lateral lock applies."""
        return max(
            float(self.vision_cfg.optical_center_tolerance_px),
            float(self.vision_cfg.optical_center_tolerance_ratio) * width,
        )

    def _dwell_cycles(self) -> int:
        """Centred frames required, clamped so a zero cannot frame unmeasured."""
        return max(1, int(self.vision_cfg.alignment_dwell_cycles))


def _finite(name: str, value: Any) -> float:
    """Return ``value`` as a float after checking it is a finite real number.

    Raises
    ------
    TypeError
        If ``value`` is a boolean or not a real number.
    ValueError
        If ``value`` is not finite.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be a real number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


def _validated_pixel(pixel: Any) -> Tuple[float, float]:
    """Return ``pixel`` as ``(u, v)`` floats.

    Raises
    ------
    TypeError
        If ``pixel`` is not a pair of real numbers.
    ValueError
        If either coordinate is not finite.
    """
    if isinstance(pixel, (str, bytes)):
        raise TypeError(f"target pixel must be a pair of real numbers, got {pixel!r}")
    try:
        u, v = pixel
    except (TypeError, ValueError) as exc:
        raise TypeError(f"target pixel must be a pair of real numbers, got {pixel!r}") from exc
    return _finite("target pixel u", u), _finite("target pixel v", v)


def _validated_frame(frame_dimensions: Any) -> Tuple[int, int]:
    """Return ``(width, height)`` after checking both are positive integers.

    Raises
    ------
    TypeError
        If the dimensions are not a pair of integers.
    ValueError
        If either dimension is not positive.
    """
    try:
        width, height = frame_dimensions
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"frame_dimensions must be (width, height), got {frame_dimensions!r}"
        ) from exc
    for value in (width, height):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise TypeError(f"frame_dimensions must be integers, got {frame_dimensions!r}")
    if width <= 0 or height <= 0:
        raise ValueError(f"frame_dimensions must be positive, got {frame_dimensions!r}")
    return int(width), int(height)
