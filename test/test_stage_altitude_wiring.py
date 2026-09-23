"""Does the corrective climb actually reach the wire, in every stage?

The control law is tested in ``test_altitude_hold``; the window is tested in
``test_failsafe``. Neither proves the thing that actually failed in flight,
which was never a mathematics problem: a governor computed the right answer and
something between it and ``move_velocity`` discarded it. That is exactly the
shape of the original bug -- the descent-only clamp silently zeroing the one
sign that mattered -- and it is the shape a regression would take too, because
opening the window is a line in a step and steps are easy to add without one.

So each stage is driven with a governor that always demands ascent, and the
assertion is on the command stream the actuator received.
"""

from __future__ import annotations

import threading

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

DEMANDED_CLIMB = 0.30


class ClimbingGovernor:
    """Always asks to climb, harder than any window will allow.

    Deliberately over-demanding: a stage that passes this is one where the
    window is open *and* the saturation is the failsafe's rather than an
    accidental zero somewhere upstream.
    """

    engaged = True
    climbing = True
    altitude_error_m = 0.30

    @staticmethod
    def compute_vz(_altitude, _dt=None):
        return DEMANDED_CLIMB

    @staticmethod
    def horizontal_scale():
        return 1.0


class Drone:
    def __init__(self, no_fly=True):
        self.no_fly = no_fly
        self.commands = []
        self.tilts = []

    def camera_control(self, tilt, pan=0.0):
        self.tilts.append(tilt)

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))

    def snapshot(self):
        pass

    def land(self):
        return True


class Odometry:
    """Healthy telemetry, parked below the setpoint."""

    def __init__(self, altitude=1.20):
        self.relative_altitude = altitude
        self.speed = 0.0
        self.vx = self.vy = self.vz = 0.0
        self.horizontal_speed = 0.0
        self.x = self.y = 0.0
        self.takeoff_x = self.takeoff_y = 0.0
        self.has_launch_origin = True

    def snapshot(self):
        return self

    def body_frame_launch_error(self):
        return -2.0, 0.0, 2.0

    @staticmethod
    def telemetry_health():
        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


class Detection:
    def __init__(self, center=(428.0, 240.0), confidence=0.8):
        self.center = center
        self.confidence = confidence
        self.class_name = "bicycle"


class DetectionResult:
    def __init__(self, detections):
        self._detections = detections

    def filter_by_class(self, _classes):
        return self._detections


class Blackboard:
    target_confirmed = True
    approach_finished = True
    rtl_completed = False
    confirmed_tilt_deg = None
    confirmed_target_px = None
    evidence = None


class Ctx:
    """The attribute surface the translating stages touch."""

    def __init__(self, *, detections=True, altitude=1.20):
        from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
        from mvp_mission_bebop.estimation.calibration import SpeedCalibration

        self.params = MissionParameters()
        self.params.kinematics.control_loop_hz = 200.0
        self.params.timeouts.search_timeout_sec = 0.4
        self.params.timeouts.tracking_timeout_sec = 0.4
        self.params.inspection.settle_timeout_sec = 0.2
        self.params.kinematics.hover_duration_sec = 0.2
        self.params.rtl.timeout_sec = 0.4
        self.params.rtl.final_hover_delay_sec = 0.2

        self.drone = Drone()
        self.odom_supervisor = Odometry(altitude)
        self.governor = ClimbingGovernor()
        self.failsafe = FailsafeSupervisor(
            drone_actuator=self.drone,
            odom_supervisor=self.odom_supervisor,
            timeouts_cfg=self.params.timeouts,
            kinematics_cfg=self.params.kinematics,
        )
        self.speed_calibration = SpeedCalibration(1.0)
        self.visual_controller = VisualServoingController(
            gimbal_config=self.params.gimbal,
            gimbal_pid_cfg=self.params.gimbal_pid,
            lateral_pid_cfg=self.params.lateral_pid,
            vision_cfg=self.params.vision,
            kinematics_cfg=self.params.kinematics,
            calibration=self.speed_calibration,
        )
        self.blackboard = Blackboard()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.requested_stage = None
        self.frame_width, self.frame_height = 856, 480
        self.current_tilt_deg = self.params.gimbal.search_tilt_deg
        self.detector = self
        self.handler = None
        self._detections = detections

    def interrupted(self) -> bool:
        """Mirrors MissionContext.interrupted: an abort or a commanded stage jump."""
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def grab_frame(self, timeout_sec=1.0):
        return object()

    def detect(self, _frame, conf=0.5):
        return DetectionResult([Detection()] if self._detections else [])

    def publish_annotated_stream(self, _frame, _result, _text):
        return None

    def record_photographic_evidence(self, **_kwargs):
        from mvp_mission_bebop.blackboard import EvidenceRecord

        return EvidenceRecord()

    # -- assertions ------------------------------------------------------
    @property
    def vertical_commands(self):
        return [vz for _vx, _vy, vz, _vyaw in self.drone.commands]


def ceiling():
    return MissionParameters().governor.climb_authority


# ------------------------------------------------------------- the stages


def test_the_search_cruise_transmits_the_corrective_climb():
    from mvp_mission_bebop.steps.search import ForwardSearchStep

    ctx = Ctx(detections=False)
    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS

    climbs = [vz for vz in ctx.vertical_commands if vz > 0.0]
    assert climbs, "the cruise suppressed the altitude trim entirely"
    assert max(climbs) == pytest.approx(ceiling()), "saturated somewhere other than the window"


def test_the_visual_approach_transmits_the_corrective_climb():
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    ctx = Ctx()
    assert VisualServoingStep().execute(ctx) is StepStatus.SUCCESS

    climbs = [vz for vz in ctx.vertical_commands if vz > 0.0]
    assert climbs, "the approach suppressed the altitude trim entirely"
    assert max(climbs) == pytest.approx(ceiling())


def test_the_inspection_hover_transmits_the_corrective_climb():
    """Not a translation phase, and it still needs this.

    The airframe arrives having just decelerated out of the approach, and the
    sink that deceleration leaves behind does not stop when the velocity command
    does. Without the window the hover would photograph the scene from wherever
    the transient left it.
    """
    from mvp_mission_bebop.steps.inspection import NadirInspectionStep

    ctx = Ctx()
    assert NadirInspectionStep().execute(ctx) is StepStatus.SUCCESS

    climbs = [vz for vz in ctx.vertical_commands if vz > 0.0]
    assert climbs, "the nadir hover suppressed the altitude trim entirely"
    assert max(climbs) == pytest.approx(ceiling())


def test_the_return_leg_transmits_the_corrective_climb():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    ctx = Ctx()
    step = ClosedLoopRTLStep()
    assert step._navigate(ctx, _guidance(ctx)) is StepStatus.SUCCESS

    climbs = [vz for vz in ctx.vertical_commands if vz > 0.0]
    assert climbs, "the return leg suppressed the altitude trim entirely"
    assert max(climbs) == pytest.approx(ceiling())


def _guidance(ctx):
    from mvp_mission_bebop.controllers.rtl_guidance import RTLGuidanceController

    law = RTLGuidanceController(
        ctx.params.rtl, ctx.params.kinematics, ctx.speed_calibration, ctx.params.governor
    )
    law.reset()
    return law


# ------------------------------------------------------------ the boundaries


def test_the_authority_does_not_survive_the_stage_that_opened_it():
    """Each stage opens its own window and closes it on the way out.

    A window left open is worse than one never opened: the next stage inherits
    ascent authority it never asked for, and the touchdown sequence is a stage.
    """
    from mvp_mission_bebop.steps.search import ForwardSearchStep

    ctx = Ctx(detections=False)
    ForwardSearchStep().execute(ctx)

    assert ctx.failsafe.altitude_hold_ceiling == 0.0
    assert ctx.failsafe.clamp_kinematics(DEMANDED_CLIMB, 0.0)[0] == 0.0


def test_the_touchdown_sequence_can_never_climb():
    """Whatever the governor thinks, a landing descends.

    The touchdown loop runs outside every hold window precisely so this is
    structural rather than a matter of the governor behaving.
    """
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    ctx = Ctx(altitude=0.10)
    ctx.params.rtl.touchdown_timeout_sec = 0.3
    ClosedLoopRTLStep()._touchdown(ctx, None)

    assert ctx.vertical_commands, "the touchdown sent no commands at all"
    assert all(vz <= 0.0 for vz in ctx.vertical_commands)


def test_disabling_the_hold_restores_the_descent_only_behaviour():
    """``hold_enabled = false`` is a configuration change, not a code change."""
    from mvp_mission_bebop.steps.search import ForwardSearchStep

    ctx = Ctx(detections=False)
    ctx.params.governor.hold_enabled = False
    ForwardSearchStep().execute(ctx)

    assert ctx.vertical_commands
    assert all(vz <= 0.0 for vz in ctx.vertical_commands)


@pytest.mark.parametrize(
    "stage",
    ["search", "tracking", "inspection"],
)
def test_the_yaw_invariant_survives_every_hold_window(stage):
    ctx = Ctx(detections=stage != "search")
    if stage == "search":
        from mvp_mission_bebop.steps.search import ForwardSearchStep

        ForwardSearchStep().execute(ctx)
    elif stage == "tracking":
        from mvp_mission_bebop.steps.tracking import VisualServoingStep

        VisualServoingStep().execute(ctx)
    else:
        from mvp_mission_bebop.steps.inspection import NadirInspectionStep

        NadirInspectionStep().execute(ctx)

    assert ctx.drone.commands
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)
