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
    A radial error beyond ``descent_gate_radius_m * descent_hysteresis`` pauses
    the descent (``vz = 0``) and returns to ``ALIGN``.
``COMMIT``
    Latched once ``h`` is at or under the effective commit height with the radial error inside
    ``commit_radius_m``, or as soon as the marker is lost after it was sighted:
    on the first missed frame below the effective loss height (the marker
    leaves the field of view by proximity), after ``descent_marker_lost_hold_sec``
    above it. The step then zeroes the translation and flies the touchdown.

Marker size
-----------
The configured heights were tuned on a 0.20 m tag, which stays framed down to
0.30 m. A larger tag leaves the frame higher: no placement keeps a 0.40 m tag
and its quiet zone framed below 0.61 m at -80 deg, 0.68 m for 0.45 m
(:func:`~mvp_mission_bebop.controllers.rtl_guidance.marker_framing_floor_m`).
Tracked below that floor the descent would only ever end by losing the
marker, with a stale radial error. The effective commit height is therefore
``max(commit_height_m, floor)`` and the effective loss height
``max(descent_lost_commit_height_m, floor + COMMIT_LOSS_BAND_M)``: the commit
is taken with the marker still in view and its radial error checked, and a
loss near the floor commits at once instead of after the debounce. For a
0.20 m tag both reduce to the configured values.

Invariants
----------
The vertical command is never positive: ``_command`` saturates it, no phase
asks for a climb, and a lost marker never realigns or searches. A descent
already under way keeps descending through the loss debounce.

The time spent not descending with the marker in view (alignment, pauses, the
wait at the commit height) is bounded by ``descent_pause_max_sec`` in total.
Once spent, the gate, the hysteresis and the commit radius stop applying: the
descent proceeds and commits, because the air never holds an ideal alignment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Optional, Tuple

from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.rtl_guidance import (
    ArucoCenteringController,
    MarkerObservation,
    marker_framing_floor_m,
)

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
        ``camera_tilt_deg``, ``tag_size`` and the profile limits.
    centering : ArucoCenteringController
        The centring law; this controller drives it every cycle. Its tilt and
        tag size set the framing floor.

    Raises
    ------
    TypeError
        If a parameter is not numeric.
    ValueError
        If ``commit_height_m``, ``descent_speed_mps``, ``descent_gate_radius_m``,
        ``commit_radius_m``, the lost hold or the tag size is not positive, or
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
        self._lost_commit_height = _positive(rtl_cfg, "descent_lost_commit_height_m")
        self._pause_max = _positive(rtl_cfg, "descent_pause_max_sec")
        self._settle_target = max(1, int(rtl_cfg.centering_settle_cycles))
        self._settle_speed = abs(float(rtl_cfg.settle_max_speed_mps))
        # The centring law's tilt, not rtl.camera_tilt_deg: the Advanced return
        # flies its own fixed depression, and the height over the marker is a
        # projection through that angle.
        self._default_tilt = float(getattr(centering, "tilt_deg", rtl_cfg.camera_tilt_deg))
        tag_size = float(getattr(centering, "tag_size_m", getattr(rtl_cfg, "tag_size", 0.20)))
        self._framing_floor = marker_framing_floor_m(tag_size, self._default_tilt)
        self._commit_height = max(self._commit_height, self._framing_floor)
        self._lost_commit_height = max(self._lost_commit_height, self._framing_floor + COMMIT_LOSS_BAND_M)
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
    def framing_floor_m(self) -> float:
        """Lowest height at which the marker can be framed, metres."""
        return self._framing_floor

    @property
    def commit_height_m(self) -> float:
        """Effective commit height, metres: the configured one or the framing floor."""
        return self._commit_height

    @property
    def lost_commit_height_m(self) -> float:
        """Effective height under which a lost marker commits at once, metres."""
        return self._lost_commit_height

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
        self._wait_sec: float = 0.0
        self._last_desired: Optional[float] = None
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
        body_velocity_mps: Optional[Tuple[float, float]] = None,
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
        body_velocity_mps : Optional[Tuple[float, float]]
            Measured ``(forward, left)`` velocity, m/s, handed to the centring
            law for its overspeed brake.

        Returns
        -------
        DescentCommand
            The command for this cycle.
        """
        dt = max(0.0, dt) if math.isfinite(dt) else 0.0
        if self._phase is DescentPhase.COMMIT:
            return self._command(0.0, 0.0, 0.0, "committed: touchdown handed over")

        tilt = tilt_deg if tilt_deg is not None and math.isfinite(tilt_deg) else self._default_tilt
        centring = self._centering.update(observation, dt, tilt_deg=tilt, body_velocity_mps=body_velocity_mps)

        if observation is None:
            return self._lost(dt, fallback_height_m)

        self._lost_sec = 0.0
        self._sighting_count += 1
        self._height = observation.height_above_marker_m(tilt)
        self._radial = centring.radial_error_m
        self._ex, self._ey = centring.ex_body_m, centring.ey_body_m
        vx, vy = centring.vx, centring.vy
        if self._height < self._ge_height:
            vx, vy = vx * self._ge_scale, vy * self._ge_scale

        forced = self._wait_sec >= self._pause_max

        if self._phase is DescentPhase.ALIGN:
            if self._radial <= self._gate and centring.commanded_speed_mps <= self._settle_speed:
                self._settle += 1
            else:
                self._settle = 0
            if self._settle < self._settle_target and not forced:
                self._wait_sec += dt
                return self._hold(vx, vy, DescentPhase.ALIGN, f"aligning: radial {self._radial:.3f} m")
            self._phase = DescentPhase.DESCEND
            self._has_started_descent = True

        if not forced and self._radial > self._gate * self._hysteresis:
            self._settle = 0
            self._wait_sec += dt
            return self._hold(vx, vy, DescentPhase.ALIGN, f"error grew to {self._radial:.3f} m: descent paused")

        if self._height <= self._commit_height:
            if self._radial <= self._commit_radius or forced:
                self._phase = DescentPhase.COMMIT
                return self._command(0.0, 0.0, 0.0, f"commit at {self._height:.2f} m, radial {self._radial:.3f} m")
            self._wait_sec += dt
            return self._hold(vx, vy, DescentPhase.DESCEND, f"at commit height, radial {self._radial:.3f} m")

        self._last_desired = min(self._speed, DESCENT_KV * (self._height - self._commit_height) + DESCENT_FLOOR_MPS)
        speed = self._profile.step(self._last_desired, dt)
        return self._command(vx, vy, -speed, f"descending at {speed:.3f} m/s, h {self._height:.2f} m")

    def _lost(self, dt: float, fallback_height_m: Optional[float]) -> DescentCommand:
        """One cycle without a validated sighting.

        Horizontal command is zero: no creep, no recovery manoeuvre. After a
        sighting the loss commits (immediately when low, after the debounce
        otherwise); a descent under way keeps its descent through the debounce.
        Before any sighting the height is held until the pause budget runs out,
        then the touchdown is committed from here.
        """
        self._lost_sec += dt
        if self._height is None and fallback_height_m is not None and math.isfinite(fallback_height_m):
            self._height = float(fallback_height_m)

        if self._sighting_count > 0:
            low = self._height is not None and self._height <= self._lost_commit_height
            if low or self._lost_sec >= self._lost_hold:
                self._phase = DescentPhase.COMMIT
                h_str = f"{self._height:.2f} m" if self._height is not None else "unknown height"
                return self._command(0.0, 0.0, 0.0, f"marker lost at {h_str}: committing touchdown immediately")
            if self._phase is DescentPhase.DESCEND and self._last_desired is not None:
                speed = self._profile.step(self._last_desired, dt)
                return self._command(0.0, 0.0, -speed, f"marker lost {self._lost_sec:.2f} s: descent continues")
            return self._hold(0.0, 0.0, self._phase, f"marker lost {self._lost_sec:.2f} s: holding height")

        if self._lost_sec >= max(self._lost_hold, self._pause_max):
            self._phase = DescentPhase.COMMIT
            return self._command(0.0, 0.0, 0.0, "marker never sighted in the descent: committing touchdown")
        return self._hold(0.0, 0.0, DescentPhase.ALIGN, "marker not yet sighted: holding height")
