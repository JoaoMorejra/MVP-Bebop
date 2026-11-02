"""Stage 4 hover: inference stops once the evidence is on disk.

The hover used to run YOLO on every cycle of ``hover_duration_sec``, including
all of the window left after the capture had been recorded -- CPU inference on
the ground station whose results nothing read. The hover now watches only while
evidence is still missing; after that it is station keeping and nothing else.
"""

from __future__ import annotations

import math
import threading

import pytest

from mvp_mission_bebop.blackboard import EvidenceRecord
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import SynchronousPerception
from mvp_mission_bebop.steps import inspection as inspection_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth
from test_stage3_integration import FakeClock, Scene

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
        self.tilts = []

    def camera_control(self, tilt, pan=0.0):
        self.tilts.append(tilt)

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
        self.confirmed_target_px = None
        self.confirmed_tilt_deg = None


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
        self.frame_width, self.frame_height = 856, 480
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


# ------------------------------------------------------- gimbal-only framing (D3)
#
# Stage 4 holds the airframe in absolute hover and frames the target with the
# gimbal alone. The scene renders the target's pixel from the current tilt, so the
# stage is closed against the same pinhole model the framing law inverts.

SCENE_RANGE_M = 2.0
SCENE_ALTITUDE_M = 1.2


def _framing_ctx(*, targets=True):
    """A Stage 4 entry as Stage 3 leaves it: locked at the search tilt."""
    ctx = Ctx(targets=targets)
    gimbal = ctx.params.gimbal
    ctx.params.inspection.settle_window_sec = 0.05
    ctx.params.inspection.settle_min_samples = 2
    ctx.params.inspection.settle_timeout_sec = 0.2
    ctx.current_tilt_deg = gimbal.search_tilt_deg
    scene = Scene(SCENE_RANGE_M, altitude_m=SCENE_ALTITUDE_M)
    ctx.blackboard.confirmed_target_px = scene.pixel(gimbal.search_tilt_deg)
    ctx.blackboard.confirmed_tilt_deg = gimbal.search_tilt_deg

    def detect(_frame, **_kwargs):
        ctx.detections += 1
        if not ctx.targets:
            return Result([])
        detection = Detection()
        detection.center = scene.pixel(ctx.current_tilt_deg)
        return Result([detection])

    ctx.detect = detect
    return ctx


def _fixed_point_deg():
    return -math.degrees(math.atan(SCENE_ALTITUDE_M / SCENE_RANGE_M))


@pytest.fixture
def simulated_time(monkeypatch):
    """Deterministic loop timing for the step, as in ``test_stage3_integration``."""
    clock = FakeClock()
    monkeypatch.setattr(
        inspection_module,
        "LoopRate",
        lambda hz, **kw: LoopRate(hz, clock=clock, sleeper=clock.sleep),
    )
    monkeypatch.setattr(
        inspection_module, "Deadline", lambda seconds, **kw: Deadline(seconds, clock=clock)
    )
    return clock


def _record_tilt_times(ctx, clock):
    """Timestamp every gimbal command on the simulated clock."""
    stamped = [(clock.now, ctx.current_tilt_deg)]
    original = ctx.drone.camera_control

    def camera_control(tilt, pan=0.0):
        stamped.append((clock.now, tilt))
        original(tilt, pan)

    ctx.drone.camera_control = camera_control
    return stamped


def _assert_slew_respected(ctx, stamped):
    rate = ctx.params.gimbal.max_slew_limit_deg
    for (t0, tilt0), (t1, tilt1) in zip(stamped, stamped[1:]):
        assert abs(tilt1 - tilt0) <= rate * (t1 - t0) + 1e-9, (
            f"{tilt0:.3f} -> {tilt1:.3f} deg in {t1 - t0:.4f} s exceeds {rate} deg/s"
        )


def test_stage4_never_translates():
    ctx = _framing_ctx()

    assert NadirInspectionStep().execute(ctx) is StepStatus.SUCCESS
    assert ctx.captures >= 1
    assert ctx.drone.commands
    assert all(
        vx == 0.0 and vy == 0.0 and vyaw == 0.0 for vx, vy, _vz, vyaw in ctx.drone.commands
    )


def test_stage4_moves_only_the_gimbal(simulated_time):
    """The target lies below the optical axis at the search tilt, so the framing
    law can only pitch the camera down: every command is at or below the last."""
    ctx = _framing_ctx()
    gimbal = ctx.params.gimbal
    stamped = _record_tilt_times(ctx, simulated_time)
    assert ctx.blackboard.confirmed_target_px[1] > ctx.frame_height / 2.0

    assert NadirInspectionStep().execute(ctx) is StepStatus.SUCCESS

    tilts = ctx.drone.tilts
    assert tilts, "the gimbal was never commanded"
    assert all(later <= earlier + 1e-9 for earlier, later in zip(tilts, tilts[1:]))
    assert all(gimbal.nadir_tilt_deg <= tilt <= gimbal.search_tilt_deg for tilt in tilts)
    assert tilts[-1] < gimbal.search_tilt_deg
    assert ctx.current_tilt_deg == tilts[-1]
    tolerance = max(
        ctx.params.vision.optical_center_tolerance_px,
        ctx.params.vision.optical_center_tolerance_ratio * ctx.frame_width,
    )
    scene = Scene(SCENE_RANGE_M, altitude_m=SCENE_ALTITUDE_M)
    assert abs(scene.pixel(tilts[-1])[1] - ctx.frame_height / 2.0) <= tolerance
    _assert_slew_respected(ctx, stamped)


def test_a_single_detection_then_loss_rests_on_its_open_loop_solution(
    simulated_time, monkeypatch
):
    """Review Focus 7 with one detection: seen in the first frame, never again.

    The gimbal must end on the open-loop solution of that pixel -- neither the
    tilt reached by the one closed-loop step nor the floor -- and the capture is
    flagged as unsettled.
    """
    ctx = _framing_ctx()
    gimbal = ctx.params.gimbal
    gimbal.max_slew_limit_deg = 450.0
    ctx.blackboard.confirmed_target_px = None
    ctx.blackboard.confirmed_tilt_deg = None
    framing_detect = ctx.detect

    def detect(frame, **kwargs):
        result = framing_detect(frame, **kwargs)
        ctx.targets = False
        return result

    ctx.detect = detect
    stamped = _record_tilt_times(ctx, simulated_time)
    milestones = []
    monkeypatch.setattr(
        inspection_module, "emit_milestone", lambda key, payload: milestones.append((key, payload))
    )

    assert NadirInspectionStep().execute(ctx) is StepStatus.SUCCESS

    first_step = ctx.drone.tilts[0]
    assert first_step > _fixed_point_deg() + 1.0, "the scenario never left an intermediate tilt"
    assert ctx.current_tilt_deg == pytest.approx(_fixed_point_deg(), abs=0.5)
    assert ctx.current_tilt_deg > gimbal.nadir_tilt_deg + gimbal.nadir_tilt_tolerance_deg
    captures = [payload for key, payload in milestones if key == "mission.capture_done"]
    assert captures and captures[0]["settled"] is False
    _assert_slew_respected(ctx, stamped)


def test_framing_failure_degrades_the_capture_without_aborting(monkeypatch):
    """Review Focus 7: the target is never seen while the gimbal frames.

    The stage keeps the open-loop pointing solution from Stage 3's last measured
    pixel -- never the floor -- and still captures, flagged as unsettled.
    """
    ctx = _framing_ctx(targets=False)
    gimbal = ctx.params.gimbal
    # Shortens the derived framing window, 2 * (search - nadir) / slew, so the
    # timeout path runs in a fraction of a second.
    gimbal.max_slew_limit_deg = 450.0
    milestones = []
    monkeypatch.setattr(
        inspection_module, "emit_milestone", lambda key, payload: milestones.append((key, payload))
    )

    assert NadirInspectionStep().execute(ctx) is StepStatus.SUCCESS

    assert ctx.captures >= 1
    assert ctx.current_tilt_deg == pytest.approx(_fixed_point_deg(), abs=1.0)
    assert ctx.current_tilt_deg > gimbal.nadir_tilt_deg + gimbal.nadir_tilt_tolerance_deg
    captures = [payload for key, payload in milestones if key == "mission.capture_done"]
    assert captures and captures[0]["settled"] is False
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in ctx.drone.commands)


def test_an_abort_during_framing_returns_aborted_without_capturing():
    ctx = _framing_ctx()
    framing_detect = ctx.detect

    def detect(frame, **kwargs):
        ctx.emergency_event.set()
        return framing_detect(frame, **kwargs)

    ctx.detect = detect

    assert NadirInspectionStep().execute(ctx) is StepStatus.ABORTED
    assert ctx.captures == 0
    assert ctx.drone.snapshots == 0
    assert not ctx.perception.engaged


# --------------------------------------------- background victim assessment


class _BlockingDetector:
    """The inspection worker's dedicated detector, 300 ms per inference."""

    def __init__(self):
        self.calls = 0

    def load(self):
        return True

    def detect(self, _frame, **_kwargs):
        import time

        time.sleep(0.3)
        self.calls += 1
        return Result([])


def _assessment_ctx():
    import numpy as np

    ctx = Ctx()
    frame = np.zeros((480, 856, 3), dtype=np.uint8)
    ctx.grab_frame = lambda timeout_sec=1.0: frame
    ctx.record_photographic_evidence = lambda **kw: EvidenceRecord(
        raw_path="accident_raw_20261020_101500.png",
        metadata_path="accident_metadata_20261020_101500.json",
        timestamp="20261020_101500",
    )
    return ctx


def _timed_capture(ctx, repeats=5):
    import time

    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        assert NadirInspectionStep()._capture(ctx, settled=True) is True
        best = min(best, time.perf_counter() - started)
    return best


def test_the_capture_submits_to_the_worker_without_waiting_for_it():
    import statistics
    import time

    from mvp_mission_bebop.perception.inspection_worker import InspectionInferenceWorker

    baseline = _timed_capture(_assessment_ctx())

    ctx = _assessment_ctx()
    detector = _BlockingDetector()
    worker = InspectionInferenceWorker(lambda: detector, ())
    submit_ms = []
    original_submit = worker.submit

    def timed_submit(job):
        started = time.perf_counter()
        try:
            return original_submit(job)
        finally:
            submit_ms.append((time.perf_counter() - started) * 1000.0)

    worker.submit = timed_submit
    ctx.inspection_worker = worker
    worker.start()
    try:
        with_worker = _timed_capture(ctx)
        # Median, not maximum: one sample can absorb a GIL hand-off to another
        # runnable thread (sys.getswitchinterval() is 5 ms), which is not the
        # capture waiting for the detector.
        assert submit_ms and statistics.median(submit_ms) < 5.0, f"submit took {submit_ms} ms"
        assert with_worker - baseline < 0.02, (
            f"capture went from {baseline * 1000:.1f} to {with_worker * 1000:.1f} ms with the worker"
        )
        assessment = worker.slot.wait("20261020_101500", 3.0)
        assert assessment is not None and assessment.victim_status.value == "not_detected"
    finally:
        worker.stop(1.0)


def test_capture_done_is_emitted_before_the_job_is_submitted(monkeypatch):
    order = []
    ctx = _assessment_ctx()

    class Worker:
        def submit(self, job):
            order.append(("submit", job.capture_stamp, job.metadata_path))
            return True

    ctx.inspection_worker = Worker()
    monkeypatch.setattr(inspection_module, "emit_milestone", lambda key, payload=None: order.append(key))

    NadirInspectionStep()._capture(ctx, settled=True)

    assert order == [
        "mission.capture_done",
        ("submit", "20261020_101500", "accident_metadata_20261020_101500.json"),
    ]


def test_a_capture_that_recorded_nothing_submits_nothing():
    ctx = _assessment_ctx()
    ctx.record_photographic_evidence = lambda **kw: EvidenceRecord()
    submitted = []

    class Worker:
        def submit(self, job):
            submitted.append(job)
            return True

    ctx.inspection_worker = Worker()
    assert NadirInspectionStep()._capture(ctx, settled=True) is False
    assert submitted == []


def test_a_failing_submission_never_fails_the_capture():
    ctx = _assessment_ctx()

    class Worker:
        def submit(self, job):
            raise RuntimeError("queue gone")

    ctx.inspection_worker = Worker()
    assert NadirInspectionStep()._capture(ctx, settled=True) is True


def test_stage4_skips_idle_hover_when_capture_confirmed(monkeypatch):
    """When evidence is confirmed on the initial capture, idle hover is skipped."""
    ctx = _framing_ctx()
    step = NadirInspectionStep()
    hover_called = []
    original_hover = step._hover

    def tracking_hover(*args, **kwargs):
        hover_called.append(True)
        return original_hover(*args, **kwargs)

    monkeypatch.setattr(step, "_hover", tracking_hover)
    status = step.execute(ctx)
    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.evidence.captured is True
    assert not hover_called, "idle hover was called even though capture was confirmed"


def test_stage4_engages_hover_when_initial_capture_fails(monkeypatch):
    """If initial capture fails, hover watch is engaged to ensure evidence is captured."""
    ctx = _framing_ctx()
    step = NadirInspectionStep()
    hover_called = []
    original_capture = step._capture
    attempt = [0]

    def failing_first_capture(context, settled, forced=False):
        attempt[0] += 1
        if attempt[0] == 1:
            return False
        return original_capture(context, settled, forced=forced)

    monkeypatch.setattr(step, "_capture", failing_first_capture)
    monkeypatch.setattr(step, "_hover", lambda *a, **k: hover_called.append(True) or StepStatus.SUCCESS)

    status = step.execute(ctx)
    assert status is StepStatus.SUCCESS
    assert hover_called, "hover fallback was not engaged when initial capture failed"


def test_stage4_background_ack_does_not_block_flight(monkeypatch):
    """Pending native photo ACK is resolved in background without blocking execution."""
    ctx = _ack_ctx(timeout=5.0)
    step = NadirInspectionStep()
    step._capture(ctx, settled=True)
    assert step._pending_ack is not None
    step._start_background_native_ack(ctx)
    # Flight thread continues immediately without waiting for the 5.0 s timeout
    assert step._pending_ack is not None or ctx.updates
