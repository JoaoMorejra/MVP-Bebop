"""Yaw laws of the Simple mission profile: scan ramp, pixel servo, heading restore.

Pure mathematics. Nothing here reads a wall clock, touches a ROS node or sends
a command; the steps advance time (``dt`` from ``LoopRate.tick``) and transmit
what these laws return through the failsafe and the proxy, both of which refuse
yaw outside a ``YawAuthority`` window.

Sign convention
---------------
``vyaw > 0`` turns left (counter-clockwise seen from above, REP-103). A target
right of the image centre has a positive normalized pixel error and must be
answered with a negative ``vyaw``. As in ``VisualServoingController``, feeding
the error to :class:`~mvp_mission_bebop.controllers.pid.FilteredPID` as the
*measurement* against a zero setpoint produces exactly that, because the PID's
error term is then ``-error``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional, Tuple

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.visual_servoing import ServoCommand, TrackingPhase
from mvp_mission_bebop.parameters import VisionConfig, YawPatternConfig

#: Normalized yaw magnitude above which a configuration is not a plausible
#: scan or restore on this airframe (1.0 is full authority).
_MAX_PLAUSIBLE_VYAW: Final[float] = 0.5


def wrap_angle(angle_rad: float) -> float:
    """Wrap an angle to the half-open interval ``(-pi, pi]``.

    Parameters
    ----------
    angle_rad : float
        Angle in radians.

    Returns
    -------
    float

    Raises
    ------
    ValueError
        If ``angle_rad`` is not finite.
    """
    if not math.isfinite(angle_rad):
        raise ValueError(f"angle must be finite, got {angle_rad!r}")
    wrapped = math.fmod(angle_rad + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def validate_yaw_pattern(cfg: YawPatternConfig) -> None:
    """Refuse a yaw configuration that cannot be flown.

    Called from ``mission.py`` before the first command of a Simple run, so a
    bad value is discovered on the ground and not halfway through Stage 4.

    Raises
    ------
    ValueError
        On a non-positive rate, a magnitude above :data:`_MAX_PLAUSIBLE_VYAW`,
        a yaw window ceiling below any commanded magnitude, a quantization floor
        below one driver step, or a non-positive window or tolerance.
    """
    magnitudes = {
        "scan_vyaw": cfg.scan_vyaw,
        "pid_output_limit": cfg.pid_output_limit,
        "reacquire_vyaw": cfg.reacquire_vyaw,
        "return_max_vyaw": cfg.return_max_vyaw,
    }
    for name, value in magnitudes.items():
        if not math.isfinite(value) or not 0.0 < value <= _MAX_PLAUSIBLE_VYAW:
            raise ValueError(f"yaw_pattern.{name} must be in (0, {_MAX_PLAUSIBLE_VYAW}], got {value!r}")
    if not math.isfinite(cfg.max_vyaw) or cfg.max_vyaw < max(magnitudes.values()):
        raise ValueError(
            f"yaw_pattern.max_vyaw ({cfg.max_vyaw!r}) must be at least the largest commanded "
            f"yaw magnitude ({max(magnitudes.values())!r})"
        )
    positives = {
        "scan_ramp_per_sec": cfg.scan_ramp_per_sec,
        "return_tolerance_deg": cfg.return_tolerance_deg,
        "return_timeout_sec": cfg.return_timeout_sec,
        "quantization_step": cfg.quantization_step,
        "kp": cfg.kp,
        "return_kp": cfg.return_kp,
    }
    for name, value in positives.items():
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"yaw_pattern.{name} must be positive, got {value!r}")
    if cfg.min_effective_vyaw < cfg.quantization_step:
        raise ValueError(
            f"yaw_pattern.min_effective_vyaw ({cfg.min_effective_vyaw!r}) must be at least one "
            f"quantization step ({cfg.quantization_step!r})"
        )
    if int(cfg.return_settle_cycles) < 1:
        raise ValueError(f"yaw_pattern.return_settle_cycles must be >= 1, got {cfg.return_settle_cycles!r}")


class YawRateLimiter:
    """Slew limiter for a normalized yaw-rate command.

    Parameters
    ----------
    max_rate_per_sec : float
        Largest change of the command per second, normalized units.

    Raises
    ------
    ValueError
        If ``max_rate_per_sec`` is not positive and finite.
    """

    def __init__(self, max_rate_per_sec: float) -> None:
        if not math.isfinite(max_rate_per_sec) or max_rate_per_sec <= 0.0:
            raise ValueError(f"max_rate_per_sec must be positive and finite, got {max_rate_per_sec!r}")
        self._max_rate = float(max_rate_per_sec)
        self._value = 0.0

    @property
    def value(self) -> float:
        """The command last returned by :meth:`step`."""
        return self._value

    def reset(self) -> None:
        """Return the command to zero without ramping."""
        self._value = 0.0

    def step(self, target: float, dt: float) -> float:
        """Move the command toward ``target`` by at most ``max_rate * dt``."""
        if dt <= 0.0 or not math.isfinite(target):
            return self._value
        limit = self._max_rate * dt
        delta = max(-limit, min(limit, target - self._value))
        self._value += delta
        if abs(target - self._value) <= 1e-12:
            self._value = float(target)
        return self._value


class YawServoingController:
    """Nulls the horizontal pixel error of the target with yaw alone.

    Duck-type compatible with ``VisualServoingController`` for ``VisualServoingStep``:
    the returned :class:`ServoCommand` carries ``vx == vy == 0.0`` and the yaw
    demand in ``vyaw``. The freeze latches on the same conditions as the lateral
    law: the target measured (not predicted) inside the centring gate for
    ``vision.alignment_dwell_cycles`` frames, with credit capped at the dwell,
    and the latching frame itself measured, inside the gate and with the
    airframe at rest.

    Parameters
    ----------
    yaw_cfg : YawPatternConfig
    vision_cfg : VisionConfig
        Optical tolerance, dwell and reacquisition windows, shared with the
        lateral law so a lock means the same thing in every profile.

    Raises
    ------
    ValueError
        From :func:`validate_yaw_pattern`.
    """

    def __init__(self, yaw_cfg: YawPatternConfig, vision_cfg: VisionConfig) -> None:
        validate_yaw_pattern(yaw_cfg)
        self.yaw_cfg = yaw_cfg
        self.vision_cfg = vision_cfg
        self._pid = FilteredPID(
            PIDGains(
                kp=yaw_cfg.kp,
                ki=yaw_cfg.ki,
                kd=yaw_cfg.kd,
                output_limits=(-yaw_cfg.pid_output_limit, yaw_cfg.pid_output_limit),
                output_deadband=yaw_cfg.deadband,
            )
        )
        self._shaper = QuantizedCommandShaper(yaw_cfg.min_effective_vyaw, yaw_cfg.quantization_step)
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._locked = False
        self._last_err_x: Optional[float] = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False
        self._tolerance_px = vision_cfg.optical_center_tolerance_px

    @property
    def phase(self) -> TrackingPhase:
        return self._phase

    @property
    def locked(self) -> bool:
        return self._locked

    def reset(self) -> None:
        """Clear all state and return to centring."""
        self._pid.reset()
        self._shaper.reset()
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._locked = False
        self._last_err_x = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False

    def compute(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        relative_altitude_m: float,
        dt: float,
        *,
        speed_scale: float = 1.0,
        measured: bool = True,
        airframe_at_rest: bool = True,
    ) -> ServoCommand:
        """One servoing cycle from a resolved observation.

        ``relative_altitude_m`` and ``speed_scale`` are accepted for call
        compatibility with the lateral law and ignored: yaw servoing needs no
        ground geometry.

        Raises
        ------
        ValueError
            If a frame dimension is not positive.
        """
        width, height = frame_dimensions
        if width <= 0 or height <= 0:
            raise ValueError(f"frame dimensions must be positive, got {frame_dimensions!r}")
        half_width = width / 2.0
        err_x = float(target_center[0]) - half_width
        err_y = float(target_center[1]) - height / 2.0
        pixel_error = math.hypot(err_x, err_y)
        self._tolerance_px = max(
            self.vision_cfg.optical_center_tolerance_px,
            self.vision_cfg.optical_center_tolerance_ratio * width,
        )
        in_gate = abs(err_x) <= self._tolerance_px

        if self._phase is TrackingPhase.LOCKED:
            return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "locked (yaw freeze)")

        self._last_err_x = err_x

        if self._phase is TrackingPhase.REACQUIRING:
            if not measured:
                return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "reacquiring (predicted)")
            self._reacquire_hits += 1
            if self._reacquire_hits < max(1, int(self.vision_cfg.reacquire_confirm_frames)):
                return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "reacquiring (confirming)")
            self._phase = TrackingPhase.CENTERING
            self._reacquire_hits = 0
            self._reacquire_elapsed_sec = 0.0
            self._pid.reset()
            self._shaper.reset()

        demand = self._pid.update(err_x / half_width, dt)
        vyaw = self._shaper.shape(demand, dt)

        dwell = max(1, int(self.vision_cfg.alignment_dwell_cycles))
        if measured and in_gate:
            self._centered_frames = min(dwell, self._centered_frames + 1)
        elif not in_gate:
            self._centered_frames = max(0, self._centered_frames - 1)

        if in_gate and measured and airframe_at_rest and self._centered_frames >= dwell:
            self._locked = True
            self._phase = TrackingPhase.LOCKED
            return self._command(0.0, err_x, pixel_error, in_gate, current_tilt_deg, "yaw freeze latched")

        return self._command(vyaw, err_x, pixel_error, in_gate, current_tilt_deg, "centring on yaw")

    def note_target_lost(self, current_tilt_deg: float, dt: float) -> ServoCommand:
        """One cycle without a usable target: search toward the side it left from.

        Rotation direction follows the last measured error (target last seen to
        the right means turn right). With no sighting at all, turn left, the
        direction of the Stage 2 scan. The window is
        ``vision.reacquire_timeout_sec``; on expiry the command is zero and
        ``recovery_exhausted`` is set so the step can end.
        """
        self._phase = TrackingPhase.REACQUIRING
        self._reacquire_hits = 0
        self._reacquire_elapsed_sec += max(0.0, dt)
        if self._reacquire_elapsed_sec >= self.vision_cfg.reacquire_timeout_sec:
            self._recovery_exhausted = True
            return self._command(
                0.0, self._last_err_x or 0.0, 0.0, False, current_tilt_deg, "reacquisition exhausted"
            )
        rate = self.yaw_cfg.reacquire_vyaw
        vyaw = rate if self._last_err_x is None else -math.copysign(rate, self._last_err_x)
        return self._command(vyaw, self._last_err_x or 0.0, 0.0, False, current_tilt_deg, "reacquiring on yaw")

    def _command(
        self,
        vyaw: float,
        err_x: float,
        pixel_error: float,
        in_gate: bool,
        tilt_deg: float,
        note: str,
    ) -> ServoCommand:
        half_width = max(self._tolerance_px, 1.0)
        alignment = 1.0 if in_gate else max(0.0, 1.0 - abs(err_x) / max(half_width * 10.0, 1.0))
        return ServoCommand(
            vx=0.0,
            vy=0.0,
            tilt_deg=tilt_deg,
            pixel_error=pixel_error,
            ground_range_m=None,
            depression_deg=None,
            phase=self._phase,
            laterally_centered=in_gate,
            locked=self._locked,
            note=note,
            tolerance_px=self._tolerance_px,
            alignment=alignment,
            recovery_exhausted=self._recovery_exhausted,
            vyaw=vyaw,
        )


@dataclass(frozen=True)
class HeadingCommand:
    """One cycle of the heading restore."""

    #: Normalized yaw command, positive to the left.
    vyaw: float
    #: Wrapped heading error ``reference - yaw``, radians, in ``(-pi, pi]``.
    error_rad: float
    #: True once the error has stayed inside the tolerance for the settle cycles.
    settled: bool


class YawHeadingController:
    """Returns the airframe to a reference heading by the short way round.

    Parameters
    ----------
    yaw_cfg : YawPatternConfig

    Raises
    ------
    ValueError
        From :func:`validate_yaw_pattern`.
    """

    def __init__(self, yaw_cfg: YawPatternConfig) -> None:
        validate_yaw_pattern(yaw_cfg)
        self._pid = FilteredPID(
            PIDGains(
                kp=yaw_cfg.return_kp,
                ki=0.0,
                kd=yaw_cfg.return_kd,
                output_limits=(-yaw_cfg.return_max_vyaw, yaw_cfg.return_max_vyaw),
            )
        )
        self._shaper = QuantizedCommandShaper(yaw_cfg.min_effective_vyaw, yaw_cfg.quantization_step)
        self._tolerance_rad = math.radians(yaw_cfg.return_tolerance_deg)
        self._settle_cycles = max(1, int(yaw_cfg.return_settle_cycles))
        self._in_tolerance = 0

    def reset(self) -> None:
        self._pid.reset()
        self._shaper.reset()
        self._in_tolerance = 0

    def update(self, yaw_rad: float, reference_rad: float, dt: float) -> HeadingCommand:
        """Command for one cycle.

        Raises
        ------
        ValueError
            If either heading is not finite.
        """
        error = wrap_angle(reference_rad - yaw_rad)
        if abs(error) <= self._tolerance_rad:
            self._in_tolerance += 1
            return HeadingCommand(0.0, error, self._in_tolerance >= self._settle_cycles)
        self._in_tolerance = 0
        demand = self._pid.update(-error, dt)
        return HeadingCommand(self._shaper.shape(demand, dt), error, False)
