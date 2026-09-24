"""Server-side gate on bounding-box rendering (spec 2026-09-24, section 3.3).

The detector runs from the takeoff countdown onwards, but the operator sees no
box until the search confirms a target. The gate lives in ``MissionContext``
and covers both paths a box can reach the ground station by: the annotated
image on ``detection_stream_topic`` and the ``bmg.detections.v1`` overlay the
MJPEG bridge composites onto the raw stream. Neither path switches topic or
resolution when the gate opens.
"""

from __future__ import annotations

import hashlib
import json
import threading
import types

import numpy as np
import pytest
from builtin_interfaces.msg import Time
from cv_bridge import CvBridge
from nectar.ai.detection.core.base import BaseDetectionModel
from nectar.ai.detection.core.types import Detection, DetectionResult

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import PerceptionSample
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

WIDTH, HEIGHT = 856, 480
BBOX = (120, 300, 240, 400)
"""Clear of the crosshair at the frame centre and of the status band."""


class RealDrawingDetector:
    """The SDK's own ``draw_detections``, without loading YOLO weights."""

    def __init__(self) -> None:
        self.draw_calls = 0

    def draw_detections(self, image, result, **kwargs):
        self.draw_calls += 1
        return BaseDetectionModel.draw_detections(None, image, result, **kwargs)


def scene() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)


def target_result() -> DetectionResult:
    return DetectionResult(
        [
            Detection(
                xyxy=np.array(BBOX, dtype=float),
                confidence=0.81,
                class_id=1,
                class_name="bicycle",
            )
        ]
    )


def context(*, revealed: bool) -> MissionContext:
    ctx = MissionContext.__new__(MissionContext)
    ctx.detector = RealDrawingDetector()
    ctx.bridge = CvBridge()
    ctx.frame_width, ctx.frame_height = WIDTH, HEIGHT
    ctx.frame_center_x, ctx.frame_center_y = WIDTH / 2.0, HEIGHT / 2.0
    clock = types.SimpleNamespace(now=lambda: types.SimpleNamespace(to_msg=Time))
    ctx.handler = types.SimpleNamespace(node=types.SimpleNamespace(get_clock=lambda: clock))
    ctx.published = []
    ctx.image_pub = types.SimpleNamespace(publish=ctx.published.append)
    ctx.overlays = []
    ctx.boxes_pub = types.SimpleNamespace(publish=ctx.overlays.append)
    ctx._monotonic_to_ros_sec = 0.0
    ctx.detection_reveal_enabled = revealed
    return ctx


def published_image(ctx: MissionContext) -> np.ndarray:
    assert len(ctx.published) == 1
    return ctx.bridge.imgmsg_to_cv2(ctx.published[0], desired_encoding="bgr8")


def digest(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def box_region(image: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = BBOX
    return image[y1 - 4 : y2 + 4, x1 - 4 : x2 + 4]


def hud_only_reference(frame: np.ndarray) -> np.ndarray:
    """What the stream shows with nothing detected: crosshair and status band."""
    ctx = context(revealed=True)
    ctx.publish_annotated_stream(frame, DetectionResult([]), "STEP 1: TAKEOFF")
    return published_image(ctx)


# ------------------------------------------------------- annotated image path


def test_a_fresh_context_starts_with_the_reveal_closed():
    clock = types.SimpleNamespace(
        now=lambda: types.SimpleNamespace(nanoseconds=0, to_msg=Time)
    )
    node = types.SimpleNamespace(
        create_publisher=lambda *_args: types.SimpleNamespace(publish=lambda _msg: None),
        get_clock=lambda: clock,
    )
    ctx = MissionContext(
        drone=None,
        detector=RealDrawingDetector(),
        handler=types.SimpleNamespace(node=node),
        odom_supervisor=None,
        governor=None,
        failsafe=None,
        visual_controller=None,
        parameters=MissionParameters(),
        frame_width=WIDTH,
        frame_height=HEIGHT,
    )

    assert ctx.detection_reveal_enabled is False


def test_a_closed_reveal_publishes_the_raw_pixels_where_the_box_would_be():
    frame = scene()
    ctx = context(revealed=False)

    ctx.publish_annotated_stream(frame, target_result(), "STEP 1: TAKEOFF")
    image = published_image(ctx)

    assert digest(box_region(image)) == digest(box_region(frame))
    assert digest(image) == digest(hud_only_reference(frame))
    assert ctx.detector.draw_calls == 0, "the gate must skip the drawing pass entirely"


def test_an_open_reveal_draws_the_box_on_the_same_topic_and_geometry():
    frame = scene()
    hidden, shown = context(revealed=False), context(revealed=True)

    hidden.publish_annotated_stream(frame, target_result(), "STEP 3: TRACKING")
    shown.publish_annotated_stream(frame, target_result(), "STEP 3: TRACKING")
    hidden_image, shown_image = published_image(hidden), published_image(shown)

    assert digest(box_region(shown_image)) != digest(box_region(frame))
    assert digest(shown_image) != digest(hidden_image)
    assert shown_image.shape == hidden_image.shape == frame.shape
    assert shown.published[0].encoding == hidden.published[0].encoding == "bgr8"


def test_a_closed_reveal_never_draws_onto_the_evidence_buffer():
    """Stage 4 records the input frame as the raw evidence after publishing."""
    frame = scene()
    pristine = frame.copy()
    ctx = context(revealed=False)

    returned = ctx.publish_annotated_stream(frame, target_result(), "STEP 4: EVIDENCE")

    assert digest(frame) == digest(pristine)
    assert returned is not frame


# ----------------------------------------------------------- overlay path


def overlay_sample() -> PerceptionSample:
    class Described:
        class_name = "bicycle"
        class_id = 1
        confidence = 0.81
        bbox = BBOX
        center = (180.0, 350.0)
        area = 120 * 100

    return PerceptionSample(
        frame=np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8),
        result=[Described()],
        stamp=1.0,
        generation=1,
    )


@pytest.mark.parametrize("revealed, boxes", [(False, 0), (True, 1)])
def test_the_bridge_overlay_obeys_the_same_gate(revealed, boxes):
    ctx = context(revealed=revealed)

    ctx.publish_detection_summary(overlay_sample(), "STEP 2: SEARCH")

    assert len(ctx.overlays) == 1
    payload = json.loads(ctx.overlays[0].data)
    assert len(payload["detections"]) == boxes
    assert payload["status"] == "STEP 2: SEARCH"
    assert payload["frame"] == {"width": WIDTH, "height": HEIGHT}


# ------------------------------------------------------------------ runner


class Recorder(BaseStep):
    def __init__(self, seen):
        super().__init__("recorder")
        self.seen = seen

    def execute(self, ctx):
        self.seen.append(ctx.detection_reveal_enabled)
        return StepStatus.SUCCESS


def runner_context():
    return types.SimpleNamespace(
        emergency_event=threading.Event(),
        stage_jump_event=threading.Event(),
        requested_stage=None,
        detection_reveal_enabled=False,
    )


def test_a_run_that_starts_past_the_search_opens_the_reveal():
    """``--stages 3,4,5`` never confirms a target, yet Stage 4 evidence needs boxes."""
    seen = []
    ctx = runner_context()
    runner = MissionRunner(
        ctx, [Recorder(seen), Recorder(seen), Recorder(seen)], stage_numbers=[3, 4, 5]
    )

    assert runner.run() is True
    assert seen == [True, True, True]


def test_takeoff_and_search_run_behind_a_closed_reveal():
    seen = []
    ctx = runner_context()
    runner = MissionRunner(ctx, [Recorder(seen), Recorder(seen)], stage_numbers=[1, 2])

    runner.run()

    assert seen == [False, False]
