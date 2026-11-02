"""Marker-tracked staged descent from the centring height to the commit height.

After the centring gate the terminal landing used to be commanded at 1.0-1.2 m
and no further ``Twist`` was sent: the last metre, ground effect included, was
flown blind and the airframe landed wherever the firmware's drift took it. This
controller keeps the marker in the loop down to ``commit_height_m`` and hands
the touchdown over only from there.

The marker also gives the best height source for this phase: the metric height
over the pad, ``z_cam * sin(t) + y_cam * cos(t)``, free of the drift of the
integrated odometry (see
:meth:`~mvp_mission_bebop.controllers.rtl_guidance.MarkerObservation.height_above_marker_m`).

State machine
-------------
``ALIGN``
    Height held (``vz = 0``), centring active. Moves to ``DESCEND`` after
    ``centering_settle_cycles`` consecutive cycles inside
    ``descent_gate_radius_m`` at residual speed.
``DESCEND``
    ``|vz| = min(descent_speed_mps, kv * (h - commit_height_m) + v_floor)``
    through a jerk-limited profile, so the descent decelerates into the commit
    height instead of stopping on it; centring continues, its output scaled by
    ``ground_effect_gain_scale`` below ``ground_effect_height_m``, where the
    rotor wash reflected by the ground amplifies every horizontal correction.
    A radial error beyond ``descent_gate_radius_m * descent_hysteresis`` stops
    the descent (``vz = 0``) and returns to ``ALIGN``.
``COMMIT``
    Latched once ``h <= commit_height_m`` with the radial error inside
    ``commit_radius_m``, or immediately upon loss of marker sighting during
    active descent (preventing idle hovering when proximity/FOV clips the
    marker), or when the marker is lost for longer than
    ``descent_marker_lost_hold_sec`` below ``commit_height_m + 0.10``. The step
    then zeroes the translation and flies the touchdown immediately.

A loss longer than the hold above that band returns to ``ALIGN``. During the
hold the height is held and the last height (or ``fallback_height_m``) is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Optional

from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.rtl_guidance import ArucoCenteringController, MarkerObservation

#: Descent deceleration gain toward the commit height, 1/s.
DESCENT_KV: Final[float] = 1.5

#: Floor of the descent speed above the commit height, m/s. Without it the
#: proportional law approaches the commit height asymptotically and never
#: reaches it; 0.03 m/s is three quanta of the driver's 0.01 resolution at unit
#: calibration, still well inside a touchdown speed.
DESCENT_FLOOR_MPS: Final[float] = 0.03

#: Band above the commit height inside which a lost marker commits instead of
#: realigning, m.
COMMIT_LOSS_BAND_M: Final[float] = 0.10


class DescentPhase(Enum):
    """Phase of :class:`PrecisionDescentController`."""

    ALIGN = "ALIGN"
    DESCEND = "DESCEND"
    COMMIT = "COMMIT"


@dataclass(frozen=True)
class DescentCommand:
    """One cycle of the staged descent.

    Parameters
    ----------
    vx, vy : float
        Horizontal command, normalized, body FLU.
    vz_mps : float
        Vertical command, m/s, negative down. Never positive.
    phase : DescentPhase
        Phase after this cycle.
    height_m : Optional[float]
        Height over the marker used this cycle, ``None`` before any sighting.
    radial_error_m : float
        Radial body-frame error of the last valid sighting, m.
    commit : bool
        The touchdown may be handed over.
    note : str
        Human-readable reason.
    ex_body_m, ey_body_m : float
        Body-frame error of the last valid sighting, m (telemetry).
    """

    vx: float
    vy: float
    vz_mps: float
    phase: DescentPhase
    height_m: Optional[float]
    radial_error_m: float
    commit: bool
    note: str
    ex_body_m: float = 0.0
    ey_body_m: float = 0.0


def _positive(cfg: Any, name: str) -> float:
    value = getattr(cfg, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"rtl.{name} must be a number, got {type(value).__name__}")
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"rtl.{name} must be positive and finite, got {value!r}")
    return value


class PrecisionDescentController:
    """Staged descent over the landing marker.

    Parameters
    ----------
    rtl_cfg : ReturnToLaunchConfig
        Source of the ``descent_*``, ``commit_*`` and ``ground_effect_*``
        fields, ``centering_settle_cycles``, ``settle_max_speed_mps``,
        ``camera_tilt_deg`` and the profile limits.
    centering : ArucoCenteringController
        The centring law; this controller drives it every cycle.

    Raises
    ------
    TypeError
        If a parameter is not numeric.
    ValueError
        If ``commit_height_m``, ``descent_speed_mps``, ``descent_gate_radius_m``,
        ``commit_radius_m`` or the lost hold is not positive, or
        ``descent_hysteresis < 1`` or ``ground_effect_gain_scale`` is outside
        ``(0, 1]``.
    """

    def __init__(self, rtl_cfg: Any, centering: ArucoCenteringController) -> None:
        self._commit_height = _positive(rtl_cfg, "commit_height_m")
        self._speed = _positive(rtl_cfg, "descent_speed_mps")
        self._gate = _positive(rtl_cfg, "descent_gate_radius_m")
        self._commit_radius = _positive(rtl_cfg, "commit_radius_m")
        self._hysteresis = _positive(rtl_cfg, "descent_hysteresis")
        if self._hysteresis < 1.0:
            raise ValueError(f"rtl.descent_hysteresis must be >= 1, got {self._hysteresis!r}")
        self._ge_height = _positive(rtl_cfg, "ground_effect_height_m")
        self._ge_scale = _positive(rtl_cfg, "ground_effect_gain_scale")
        if self._ge_scale > 1.0:
            raise ValueError(f"rtl.ground_effect_gain_scale must be in (0, 1], got {self._ge_scale!r}")
        self._lost_hold = _positive(rtl_cfg, "descent_marker_lost_hold_sec")
        self._settle_target = max(1, int(rtl_cfg.centering_settle_cycles))
        self._settle_speed = abs(float(rtl_cfg.settle_max_speed_mps))
        self._default_tilt = float(rtl_cfg.camera_tilt_deg)
        self._centering = centering
        self._profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._speed,
                max_accel=float(rtl_cfg.max_accel_mps2),
                max_jerk=float(rtl_cfg.max_jerk_mps3),
            )
        )
        self.reset()

    @property
    def phase(self) -> DescentPhase:
        """Current phase."""
        return self._phase

    def reset(self) -> None:
        """Back to ``ALIGN`` with no height and no sighting."""
        self._phase: DescentPhase = DescentPhase.ALIGN
        self._height: Optional[float] = None
        self._radial: float = math.inf
        self._ex: float = 0.0
        self._ey: float = 0.0
        self._settle: int = 0
        self._lost_sec: float = 0.0
        self._has_started_descent: bool = False
        self._sighting_count: int = 0
        self._profile.reset(0.0, 0.0)

    def _command(self, vx: float, vy: float, vz: float, note: str) -> DescentCommand:
        return DescentCommand(
            vx=vx,
            vy=vy,
            vz_mps=min(0.0, vz),
            phase=self._phase,
            height_m=self._height,
            radial_error_m=self._radial,
            commit=self._phase is DescentPhase.COMMIT,
            note=note,
            ex_body_m=self._ex,
            ey_body_m=self._ey,
        )

    def _hold(self, vx: float, vy: float, phase: DescentPhase, note: str) -> DescentCommand:
        self._phase = phase
        self._profile.reset(0.0, 0.0)
        return self._command(vx, vy, 0.0, note)

    def update(
        self,
        observation: Optional[MarkerObservation],
        fallback_height_m: Optional[float],
        dt: float,
        tilt_deg: Optional[float] = None,
    ) -> DescentCommand:
        """Advance one control cycle.

        Parameters
        ----------
        observation : Optional[MarkerObservation]
            Validated sighting of the landing marker, or ``None``.
        fallback_height_m : Optional[float]
            Height from another source (odometry), used only while the marker
            is lost and no sighting height exists yet.
        dt : float
            Elapsed interval, s.
        tilt_deg : Optional[float]
            Confirmed gimbal tilt; the configured tilt when ``None`` or not
            finite.

        Returns
        -------
        DescentCommand
            The command for this cycle.
        """
        dt = max(0.0, dt) if math.isfinite(dt) else 0.0
        if self._phase is DescentPhase.COMMIT:
            return self._command(0.0, 0.0, 0.0, "committed: touchdown handed over")

        tilt = tilt_deg if tilt_deg is not None and math.isfinite(tilt_deg) else self._default_tilt
        centring = self._centering.update(observation, dt, tilt_deg=tilt)

        if observation is None:
            self._lost_sec += dt
            if self._height is None and fallback_height_m is not None and math.isfinite(fallback_height_m):
                self._height = float(fallback_height_m)
            # Instant touchdown once marker is lost:
            # Below 0.60 m the marker naturally exceeds camera FOV; commit immediately.
            # Otherwise, commit after the brief lost hold debounce (to reject single frame drops).
            if self._phase is DescentPhase.DESCEND or self._has_started_descent or self._sighting_count > 0:
                is_low_altitude = self._height is not None and self._height <= 0.60
                if is_low_altitude or self._lost_sec >= self._lost_hold:
                    self._phase = DescentPhase.COMMIT
                    h_str = f"{self._height:.2f} m" if self._height is not None else "unknown height"
                    return self._command(
                        0.0,
                        0.0,
                        0.0,
                        f"marker lost during precision descent at {h_str}: committing touchdown immediately",
                    )
                return self._hold(
                    centring.vx, centring.vy, self._phase, f"marker lost {self._lost_sec:.2f} s: holding height"
                )
            if self._lost_sec < self._lost_hold:
                return self._hold(
                    centring.vx, centring.vy, self._phase, f"marker lost {self._lost_sec:.2f} s: holding height"
                )
            return self._hold(centring.vx, centring.vy, DescentPhase.ALIGN, "marker lost before sighting: holding")

        self._lost_sec = 0.0
        self._sighting_count += 1
        self._height = observation.height_above_marker_m(tilt)
        self._radial = centring.radial_error_m
        self._ex, self._ey = centring.ex_body_m, centring.ey_body_m
        vx, vy = centring.vx, centring.vy
        if self._height < self._ge_height:
            vx, vy = vx * self._ge_scale, vy * self._ge_scale

        if self._phase is DescentPhase.ALIGN:
            if self._radial <= self._gate and centring.commanded_speed_mps <= self._settle_speed:
                self._settle += 1
            else:
                self._settle = 0
            if self._settle < self._settle_target:
                return self._hold(vx, vy, DescentPhase.ALIGN, f"aligning: radial {self._radial:.3f} m")
            self._phase = DescentPhase.DESCEND
            self._has_started_descent = True

        if self._radial > self._gate * self._hysteresis:
            self._settle = 0
            return self._hold(vx, vy, DescentPhase.ALIGN, f"error grew to {self._radial:.3f} m: descent paused")

        if self._height <= self._commit_height:
            if self._radial <= self._commit_radius:
                self._phase = DescentPhase.COMMIT
                return self._command(0.0, 0.0, 0.0, f"commit at {self._height:.2f} m, radial {self._radial:.3f} m")
            return self._hold(vx, vy, DescentPhase.DESCEND, f"at commit height, radial {self._radial:.3f} m")

        desired = min(self._speed, DESCENT_KV * (self._height - self._commit_height) + DESCENT_FLOOR_MPS)
        speed = self._profile.step(desired, dt)
        return self._command(vx, vy, -speed, f"descending at {speed:.3f} m/s, h {self._height:.2f} m")
