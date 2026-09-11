"""Return-to-launch guidance law.

Replaces the previous RTL, which had no control law at all. Its ``kp`` and
``kd`` fields were declared and never read; what actually flew was a three-regime
piecewise function of displacement with no acceleration limit, a lateral channel
that was bang-bang over most of its range, and an anti-stall floor that made
deceleration impossible -- the drone reached the arrival radius still moving,
because the floor forbade anything gentler than 3.5% throttle.

The law here is feedforward plus feedback in physical units:

*Feedforward* is the braking profile ``v = sqrt(2 a d)``: the fastest the drone
may travel and still stop within the distance remaining. This is a statement
about whether the vehicle *can* stop, which a ramp defined on displacement is
not.

*Feedback* is a filtered PD on the along-track and cross-track errors. It
requests a velocity; the braking profile caps it. Near the origin the PD
dominates and the approach is gentle; far away the cap saturates at cruise.

*Shaping* applies a jerk limit, then converts to the actuator's normalized
domain, then renders sub-threshold demands through a sigma-delta modulator so
slow motion survives the driver's int8 quantization without a hard floor.

Two invariants are enforced structurally rather than by assertion: ``vyaw`` is
never assigned anything but zero, and ``vx`` is clamped to be non-positive, so
the drone can only ever fly backward along its own axis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Final, Optional

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.convergence import (
    SettlementCriteria,
    SettlementDetector,
    SettlementReport,
)
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot

logger = logging.getLogger("RTLGuidance")

#: Speed cap during station keeping, as a fraction of the cruise cap. The hold
#: only has to cancel residual drift, and a slow cap keeps the correction from
#: exciting the airframe just before touchdown.
STATION_KEEPING_SPEED_RATIO: Final[float] = 0.40


class RTLPhase(str, Enum):
    """Stage of the return sequence.

    String-valued so it can be logged and compared without conversion.
    """

    NAVIGATING = "navigating"
    STATION_KEEPING = "station_keeping"
    TOUCHDOWN = "touchdown"
    COMPLETE = "complete"


@dataclass(frozen=True)
class GuidanceCommand:
    """One cycle's output: normalized actuator commands plus the reasoning."""

    vx: float
    vy: float
    vz: float
    vyaw: float
    #: Planar range to the launch origin, metres.
    distance_m: float
    #: Along-track error in body FLU: negative when the origin lies behind.
    ex_body_m: float
    #: Cross-track error in body FLU: positive when the origin lies to the left.
    ey_body_m: float
    #: Physical along-track speed the profile settled on, m/s.
    target_speed_mps: float
    phase: RTLPhase
    arrived: bool
    note: str

    @property
    def is_stationary(self) -> bool:
        """True when no translation is being commanded this cycle."""
        return self.vx == 0.0 and self.vy == 0.0


class RTLGuidanceController:
    """Closed-loop backward navigation to the launch origin.

    All internal computation is in metres and metres per second. Conversion to
    the Bebop's normalized command domain happens once, at the output, through
    :class:`~mvp_mission_bebop.estimation.calibration.SpeedCalibration`.
    """

    def __init__(
        self,
        rtl_cfg: ReturnToLaunchConfig,
        kinematics_cfg: FlightKinematicsConfig,
        calibration: Optional[SpeedCalibration] = None,
    ) -> None:
        self.rtl_cfg = rtl_cfg
        self.kinematics_cfg = kinematics_cfg
        self.calibration = calibration or SpeedCalibration(kinematics_cfg.normalized_to_mps)

        # Configuration arrives in normalized units for historical reasons (the
        # names are part of the GCS contract); the law works in m/s.
        self._cruise_mps = self.calibration.to_mps(rtl_cfg.max_speed)
        self._max_lateral_mps = self.calibration.to_mps(rtl_cfg.max_lateral_speed)

        self._longitudinal = FilteredPID(
            PIDGains(
                kp=rtl_cfg.kp,
                ki=0.0,
                kd=rtl_cfg.kd,
                output_limits=(-self._cruise_mps, self._cruise_mps),
                error_deadband=rtl_cfg.deadband_m,
            )
        )
        self._lateral = FilteredPID(
            PIDGains(
                kp=rtl_cfg.lateral_kp,
                ki=0.0,
                kd=rtl_cfg.lateral_kd,
                output_limits=(-self._max_lateral_mps, self._max_lateral_mps),
                error_deadband=rtl_cfg.deadband_m,
            )
        )

        self._longitudinal_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._cruise_mps,
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )
        self._lateral_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._max_lateral_mps,
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )

        self._shaper_x = QuantizedCommandShaper(
            rtl_cfg.min_effective_speed, rtl_cfg.quantization_step
        )
        self._shaper_y = QuantizedCommandShaper(
            rtl_cfg.min_effective_speed, rtl_cfg.quantization_step
        )

        self._settlement = SettlementDetector(
            SettlementCriteria(
                window_sec=rtl_cfg.settle_window_sec,
                min_samples=rtl_cfg.settle_min_samples,
                max_speed=rtl_cfg.settle_max_speed_mps,
                max_position_sigma=rtl_cfg.settle_max_position_sigma_m,
                max_distance=rtl_cfg.arrival_radius_m,
            )
        )
        self._last_report: Optional[SettlementReport] = None
        self._overshoot_logged = False
        # Mission time accumulated from the supplied dt. The controller must not
        # read a wall clock: doing so is what makes a control law untestable and
        # couples it to how fast the loop happens to run, which is the same
        # defect that rules out the SDK's PIDController for this path.
        self._elapsed: float = 0.0

    # ------------------------------------------------------------- properties

    @property
    def cruise_speed_mps(self) -> float:
        """Cruise speed cap in physical units."""
        return self._cruise_mps

    @property
    def settlement(self) -> Optional[SettlementReport]:
        """Most recent settlement evaluation, or ``None`` before the first cycle."""
        return self._last_report

    @property
    def elapsed_sec(self) -> float:
        """Mission time accumulated from the supplied ``dt`` values."""
        return self._elapsed

    # ------------------------------------------------------------------ cycle

    def reset(self) -> None:
        """Clear all controller state. Call once before engaging the return leg."""
        self._longitudinal.reset()
        self._lateral.reset()
        self._longitudinal_profile.reset()
        self._lateral_profile.reset()
        self._shaper_x.reset()
        self._shaper_y.reset()
        self._settlement.reset()
        self._last_report = None
        self._overshoot_logged = False
        self._elapsed = 0.0

    def compute(
        self,
        snapshot: OdometrySnapshot,
        vz_command: float,
        dt: float,
        *,
        phase: RTLPhase = RTLPhase.NAVIGATING,
    ) -> GuidanceCommand:
        """Produce one cycle of guidance.

        Parameters
        ----------
        snapshot : OdometrySnapshot
            Consistent odometric state, captured under a single lock.
        vz_command : float
            Vertical command from the anti-climb governor, in normalized units.
            Passed through rather than recomputed, and clamped non-positive here
            so the climb invariant holds even if the governor misbehaves.
        dt : float
            True elapsed interval from :meth:`LoopRate.tick`.
        phase : RTLPhase
            ``NAVIGATING`` for the return leg, ``STATION_KEEPING`` for the
            damping hold over the origin.
        """
        self._elapsed += dt
        ex, ey, distance = snapshot.body_frame_launch_error()

        speed_cap = self._cruise_mps
        if phase is RTLPhase.STATION_KEEPING:
            speed_cap *= STATION_KEEPING_SPEED_RATIO

        longitudinal_mps, note = self._compute_longitudinal(ex, dt, speed_cap)
        lateral_mps = self._compute_lateral(ey, dt, speed_cap)

        # Convert once, at the boundary, then render onto the quantized channel.
        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)

        report = self._settlement.update(
            x=snapshot.x,
            y=snapshot.y,
            speed=snapshot.horizontal_speed,
            distance=distance,
            timestamp=self._elapsed,
        )
        self._last_report = report

        return GuidanceCommand(
            # Structural invariant: backward-only along the body axis.
            vx=min(0.0, vx),
            vy=vy,
            # Structural invariant: the governor may descend, never climb.
            vz=min(0.0, vz_command),
            vyaw=0.0,
            distance_m=distance,
            ex_body_m=ex,
            ey_body_m=ey,
            target_speed_mps=longitudinal_mps,
            phase=phase,
            arrived=report.settled,
            note=note if note else report.reason,
        )

    # ---------------------------------------------------------------- channels

    def _compute_longitudinal(self, ex: float, dt: float, speed_cap: float) -> tuple[float, str]:
        """Along-track law: PD demand, capped by what can still be stopped."""
        if abs(ex) < self.rtl_cfg.deadband_m:
            return self._longitudinal_profile.step(0.0, dt), ""

        # The controlled variable is the drone's displacement from the origin
        # along body-x, which is the negation of the error vector pointing at it.
        demand = self._longitudinal.update(-ex, dt)

        # Feedforward ceiling: the fastest speed from which the remaining
        # distance is still enough to come to rest.
        ceiling = braking_velocity(
            abs(ex),
            self.rtl_cfg.max_accel_mps2,
            cruise_velocity=speed_cap,
            arrival_tolerance=self.rtl_cfg.deadband_m,
        )
        target = max(-ceiling, min(ceiling, demand))

        note = ""
        if target > 0.0:
            # The origin is ahead: the drone overshot it. Flying forward would
            # violate the backward-only invariant, so hold station on this axis
            # and let the arrival window absorb the residual.
            target = 0.0
            if not self._overshoot_logged:
                logger.warning(
                    "Launch origin is %.2f m ahead of the nose. Forward flight is prohibited "
                    "during RTL; holding the along-track axis and relying on the arrival window.",
                    ex,
                )
                self._overshoot_logged = True
            note = "overshoot: along-track axis held"

        return self._longitudinal_profile.step(target, dt), note

    def _compute_lateral(self, ey: float, dt: float, speed_cap: float) -> float:
        """Cross-track law: filtered PD, jerk-limited."""
        if abs(ey) < self.rtl_cfg.deadband_m:
            return self._lateral_profile.step(0.0, dt)

        lateral_cap = min(self._max_lateral_mps, speed_cap)
        demand = self._lateral.update(-ey, dt)
        target = max(-lateral_cap, min(lateral_cap, demand))
        return self._lateral_profile.step(target, dt)
