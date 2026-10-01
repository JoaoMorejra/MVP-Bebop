"""Stage 4 hover: inference stops once the evidence is on disk.

The hover used to run YOLO on every cycle of ``hover_duration_sec``, including
all of the window left after the capture had been recorded -- CPU inference on
the ground station whose results nothing read. The hover now watches only while
evidence is still missing; after that it is station keeping and nothing else.
"""

from __future__ import annotations

import threading

from mvp_mission_bebop.blackboard import EvidenceRecord
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import SynchronousPerception
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

HOVER_SEC = 0.3
LOOP_HZ = 100.0


class Detection:
    center = (428.0, 240.0)
    confidence = 0.9
    class_name = "bicycle"
    class_id = 1
    bbox = (400, 220, 456, 260)
    area = 2240


class Result:
    def __init__(self, detections):
        self._detections = list(detections)

    def filter_by_class(self, _classes):
        return list(self._detections)


class Drone:
    no_fly = True

    def __init__(self):
        self.commands = []
        self.snapshots = 0

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))

    def snapshot(self):
        self.snapshots += 1


class Odometry:
    relative_altitude = 1.0
    speed = vx = vy = vz = x = y = 0.0

    def snapshot(self):
        return self

    @staticmethod
    def telemetry_health():
        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


class Governor:
    @staticmethod
    def compute_vz(_altitude, _dt=None, vx_commanded=0.0):
        return 0.0


class Blackboard:
    def __init__(self):
        self.evidence = EvidenceRecord()
        self.approach_finished = True
        self.target_confirmed = True


class Ctx:
    """The attribute surface ``NadirInspectionStep._hover`` touches."""

    def __init__(self, *, targets=True, capture_succeeds=True):
        self.params = MissionParameters()
        self.params.kinematics.hover_duration_sec = HOVER_SEC
        self.params.kinematics.control_loop_hz = LOOP_HZ
        self.drone = Drone()
        self.odom_supervisor = Odometry()
        self.governor = Governor()
        self.failsafe = FailsafeSupervisor(
            drone_actuator=self.drone,
            odom_supervisor=self.odom_supervisor,
            timeouts_cfg=self.params.timeouts,
            kinematics_cfg=self.params.kinematics,
        )
        self.blackboard = Blackboard()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.current_tilt_deg = self.params.gimbal.nadir_tilt_deg
        self.detector = self
        self.targets = targets
        self.capture_succeeds = capture_succeeds
        self.grabs = 0
        self.detections = 0
        self.captures = 0
        self.perception = SynchronousPerception(self)
        self.picture_ack = None

    def interrupted(self):
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def grab_frame(self, timeout_sec=1.0):
        self.grabs += 1
        return object()

    def detect(self, _frame, **_kwargs):
        self.detections += 1
        return Result([Detection()] if self.targets else [])

    def publish_annotated_stream(self, frame, _result, _text):
        return frame

    def record_photographic_evidence(self, **_kwargs):
        self.captures += 1
        if self.capture_succeeds:
            return EvidenceRecord(raw_path="accident_raw_x.png")
        return EvidenceRecord()


def hover_cycles():
    return HOVER_SEC * LOOP_HZ


def test_a_hover_entered_with_evidence_runs_no_vision_at_all():
    ctx = Ctx()
    status = NadirInspectionStep()._hover(ctx, already_captured=True)

    assert status is StepStatus.SUCCESS
    assert ctx.detections == 0, "the detector ran after the evidence was already recorded"
    assert ctx.grabs == 0, "the camera was polled after the evidence was already recorded"
    assert not ctx.perception.engaged


def test_station_keeping_continues_for_the_whole_window_without_vision():
    """Suspending vision must not suspend the hover command stream."""
    ctx = Ctx()
    NadirInspectionStep()._hover(ctx, already_captured=True)

    assert len(ctx.drone.commands) >= 0.8 * hover_cycles()
    assert all(vx == 0.0 and vy == 0.0 and vyaw == 0.0 for vx, vy, _vz, vyaw in ctx.drone.commands)


def test_inference_stops_at_the_capture():
    """One detection triggers the capture; nothing is inferred after it."""
    ctx = Ctx()
    NadirInspectionStep()._hover(ctx, already_captured=False)

    assert ctx.captures == 1
    # One inference in the hover found the target, one more ran inside the
    # capture itself for the annotated evidence frame; nothing after that.
    assert ctx.detections == 2, f"{ctx.detections} inferences for a single capture"
    assert len(ctx.drone.commands) >= 0.8 * hover_cycles()
    assert not ctx.perception.engaged


def test_a_hover_with_nothing_in_view_keeps_watching():
    ctx = Ctx(targets=False)
    NadirInspectionStep()._hover(ctx, already_captured=False)

    assert ctx.captures == 0
    assert ctx.detections >= 0.8 * hover_cycles()


def test_a_failed_capture_resumes_the_watch():
    """No evidence on disk means the hover is still the last chance to get it."""
    ctx = Ctx(capture_succeeds=False)
    NadirInspectionStep()._hover(ctx, already_captured=False)

    assert ctx.captures >= 2, "the hover stopped looking after a capture that recorded nothing"
    assert not ctx.perception.engaged


def test_an_abort_leaves_the_pipeline_disengaged():
    ctx = Ctx(targets=False)
    ctx.emergency_event.set()

    assert NadirInspectionStep()._hover(ctx, already_captured=False) is StepStatus.ABORTED
    assert not ctx.perception.engaged


from mvp_mission_bebop.steps.inspection import NadirInspectionStep as _Step


class SinkingGovernor:
    """Reports a sustained descent-side correction, as during a real sink."""

    def compute_vz(self, _altitude, _dt=None):
        return -0.06


def test_settlement_waits_out_an_active_altitude_correction():
    """The Ponto 3 defect: horizontally still, but the governor is still
    fighting a sink, so `do_hover` cannot actually be engaged yet."""
    ctx = Ctx()
    ctx.governor = SinkingGovernor()
    ctx.params.kinematics.control_loop_hz = LOOP_HZ
    # Same short window as the idle case below, so the vertical criterion is
    # the only thing that can keep the gate open. A timeout shorter than the
    # default 1.0 s window would leave it open regardless of vz.
    ctx.params.inspection.settle_window_sec = 0.05
    ctx.params.inspection.settle_min_samples = 2
    ctx.params.inspection.settle_timeout_sec = 0.5

    settled = _Step()._await_stillness(ctx)

    assert not settled, "settled while the governor was still actively correcting vz"


def test_settlement_holds_once_the_governor_is_idle():
    ctx = Ctx()
    ctx.params.kinematics.control_loop_hz = LOOP_HZ
    ctx.params.inspection.settle_window_sec = 0.05
    ctx.params.inspection.settle_min_samples = 2
    ctx.params.inspection.settle_timeout_sec = 0.5

    settled = _Step()._await_stillness(ctx)

    assert settled


# ------------------------------------------------------------ native photo (4.7)


def test_the_native_photo_is_requested_before_the_inference_and_its_ack_recorded():
    from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker

    ctx = Ctx()
    order = []
    ctx.picture_ack = PictureAckTracker()
    original_detect = ctx.detect

    def detect(frame, **kwargs):
        order.append("detect")
        return original_detect(frame, **kwargs)

    def snapshot():
        order.append("snapshot")
        ctx.picture_ack.update('{"sequence": 1, "kind": "event", "value": 0, "error": 0}')

    ctx.detect = detect
    ctx.drone.snapshot = snapshot
    recorded = {}

    def record(**kwargs):
        recorded.update(kwargs)
        return EvidenceRecord(raw_path="accident_raw_x.png", metadata_path="accident_metadata_x.json")

    ctx.record_photographic_evidence = record

    assert NadirInspectionStep()._capture(ctx, settled=True) is True
    assert order[:2] == ["snapshot", "detect"]
    assert recorded["native_photo"]["acknowledged"] is True
    assert recorded["native_photo"]["event"] == "taken"
    assert recorded["native_photo"]["requested_at_utc"]


def test_a_missing_native_ack_is_recorded_as_such():
    from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker

    ctx = Ctx()
    ctx.picture_ack = PictureAckTracker()
    recorded = {}

    def record(**kwargs):
        recorded.update(kwargs)
        return EvidenceRecord(raw_path="accident_raw_x.png")

    ctx.record_photographic_evidence = record
    ctx.params.inspection.native_photo_ack_timeout_sec = 0.1
    NadirInspectionStep()._capture(ctx, settled=True)
    assert recorded["native_photo"]["acknowledged"] is False
    assert recorded["native_photo"]["event"] is None


# ------------------------------------------------- asynchronous native ACK (4.7, live)
#
# Measured on the aircraft: PictureEventChanged (TAKEN) arrives 2.5 s after the
# request, longer than the 2 s the capture used to block for; blocking for it
# would also hold the last Twist open-loop on the Bebop for that long.


def _ack_ctx(timeout):
    from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker

    ctx = Ctx()
    ctx.picture_ack = PictureAckTracker()
    ctx.params.inspection.native_photo_ack_timeout_sec = timeout
    ctx.updates = []
    ctx.record_photographic_evidence = lambda **kw: EvidenceRecord(
        raw_path="accident_raw_x.png", metadata_path="accident_metadata_x.json"
    )
    ctx.update_evidence_metadata = lambda path, fields: ctx.updates.append((path, fields))
    return ctx


def test_the_capture_does_not_wait_for_the_aircraft():
    import time

    ctx = _ack_ctx(timeout=4.0)
    step = NadirInspectionStep()
    started = time.monotonic()
    assert step._capture(ctx, settled=True) is True
    assert time.monotonic() - started < 0.5


def test_an_acknowledgement_arriving_during_the_hover_updates_the_sidecar():
    import threading

    ctx = _ack_ctx(timeout=4.0)
    step = NadirInspectionStep()
    step._capture(ctx, settled=True)
    threading.Timer(0.1, lambda: ctx.picture_ack.update('{"sequence": 9, "kind": "event", "value": 0, "error": 0}')).start()
    step._hover(ctx, already_captured=True)
    assert ctx.updates, "the sidecar was never updated"
    path, fields = ctx.updates[-1]
    assert path == "accident_metadata_x.json"
    assert fields["native_photo"]["acknowledged"] is True
    assert fields["native_photo"]["event"] == "taken"


def test_no_acknowledgement_is_settled_by_the_end_of_the_stage_while_holding():
    ctx = _ack_ctx(timeout=0.2)
    step = NadirInspectionStep()
    step._capture(ctx, settled=True)
    commands = len(ctx.drone.commands)
    step._settle_native_ack(ctx)
    assert ctx.updates and ctx.updates[-1][1]["native_photo"]["acknowledged"] is False
    assert len(ctx.drone.commands) > commands, "station keeping stopped while waiting"
