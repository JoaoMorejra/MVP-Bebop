"""Stage 5: the ArUco return leg, from pose tuple to wheels down.

Three layers are exercised here and they fail in different ways, so they are
kept apart.

*The projection.* A sign error in the camera-to-body transform is the one defect
in this stage that cannot be caught by watching the aircraft do roughly the right
thing: it produces a controller that is stable, smooth, well damped, and
converging on the mirror image of the marker. Every sign is pinned individually
against the physical statement it encodes.

*The control law.* Convergence, the speed ceiling, the settle gate, noise, and
what happens when the marker disappears. Driven with an explicit ``dt`` and a
plant model, never a wall clock, so the tests measure the mathematics rather than
the machine they run on.

*The step.* The kinematic invariants of the reverse cruise, the three ways the
phase sequence can end, and the guarantee that every one of them still lands.
"""

from __future__ import annotations

import math
import random
import threading
from typing import List, Optional, Tuple

import pytest

from mvp_mission_bebop.controllers.rtl_guidance import (
    ArucoCenteringController,
    CenteringCommand,
    MarkerObservation,
    project_marker_to_body,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import FlightKinematicsConfig, MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.rtl import ArucoMarkerSensor, ClosedLoopRTLStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

DT = 1.0 / 15.0
TILT = -80.0


# ══════════════════════════════════════════════════════════ geometry helpers


def pad_to_camera(
    ex_body_m: float, ey_body_m: float, altitude_m: float, tilt_deg: float = TILT
) -> Tuple[float, float, float]:
    """Invert :func:`project_marker_to_body` for a marker on flat ground.

    Given where the pad is relative to the airframe -- ``ex`` metres ahead,
    ``ey`` metres to the left, ``altitude`` metres below -- return the camera
    frame translation a correctly calibrated detector would report.

    The forward map is under-determined on its own (two body components out of
    three camera ones), so the third equation is the physical one: the marker
    lies on the ground, ``altitude`` below the airframe. With
    ``s = sin t, c = cos t`` the system is ``[[-s, c], [c, s]] [y; z] = [ex; h]``
    whose matrix is an involution, so the inverse is the matrix itself.
    """
    depression = math.radians(abs(tilt_deg))
    sin_t, cos_t = math.sin(depression), math.cos(depression)
    y_cam = -sin_t * ex_body_m + cos_t * altitude_m
    z_cam = cos_t * ex_body_m + sin_t * altitude_m
    return -ey_body_m, y_cam, z_cam


def sighting(
    ex_body_m: float,
    ey_body_m: float,
    altitude_m: float = 1.0,
    *,
    marker_id: int = 0,
    tilt_deg: float = TILT,
) -> MarkerObservation:
    """A validated observation of a pad at the given body-frame offset."""
    x_cam, y_cam, z_cam = pad_to_camera(ex_body_m, ey_body_m, altitude_m, tilt_deg)
    return MarkerObservation(
        marker_id=marker_id, x_cam_m=x_cam, y_cam_m=y_cam, z_cam_m=z_cam, yaw_deg=0.0
    )


def law(**overrides) -> ArucoCenteringController:
    """A centering controller on the shipped defaults, with overrides applied."""
    params = MissionParameters()
    for key, value in overrides.items():
        setattr(params.rtl, key, value)
    controller = ArucoCenteringController(
        params.rtl, FlightKinematicsConfig(), SpeedCalibration(1.0)
    )
    controller.reset()
    return controller


# ═══════════════════════════════════════════════════════ the projection itself


def test_true_nadir_degenerates_to_the_familiar_mapping():
    """At -90 deg the tilt term vanishes and only the image offsets remain.

    This is the case every nadir-landing implementation gets right by accident;
    it is here because the general form must reduce to it, or the general form is
    not a generalization of anything.
    """
    ex, ey = project_marker_to_body(0.30, 0.20, 2.0, -90.0)
    assert ex == pytest.approx(-0.20, abs=1e-9)
    assert ey == pytest.approx(-0.30, abs=1e-9)


def test_a_marker_to_the_right_is_a_negative_cross_track_error():
    """Camera +X is image right, body +Y is left, so the sign inverts.

    Getting this backwards yields an aircraft that accelerates away from the pad
    along the lateral axis until the frame no longer contains it.
    """
    _ex, ey = project_marker_to_body(0.25, 0.0, 1.0, TILT)
    assert ey < 0.0
    _ex, ey_left = project_marker_to_body(-0.25, 0.0, 1.0, TILT)
    assert ey_left > 0.0


def test_a_marker_low_in_the_frame_is_behind_the_aircraft():
    """Camera +Y is image *down*; with the camera pitched down, that is aft.

    The physical statement: the optical axis meets the ground some distance
    ahead of the airframe, so anything imaged below that point is nearer the
    aircraft -- and past it, behind.
    """
    high = project_marker_to_body(0.0, -0.20, 1.0, TILT)[0]
    low = project_marker_to_body(0.0, +0.20, 1.0, TILT)[0]
    assert low < 0.0 < high


def test_the_projection_round_trips_through_the_inverse():
    """Whatever the tilt, forward then inverse is the identity."""
    for tilt in (-60.0, -75.0, -80.0, -89.0, -90.0):
        for ex, ey, altitude in ((0.0, 0.0, 1.0), (0.7, -0.4, 1.5), (-1.2, 0.9, 0.8)):
            x_cam, y_cam, z_cam = pad_to_camera(ex, ey, altitude, tilt)
            back_ex, back_ey = project_marker_to_body(x_cam, y_cam, z_cam, tilt)
            assert back_ex == pytest.approx(ex, abs=1e-9)
            assert back_ey == pytest.approx(ey, abs=1e-9)


def test_only_the_magnitude_of_the_configured_tilt_matters():
    """A configuration stating the depression as a positive angle flies the same.

    ``camera_tilt_deg`` is negative by convention and nothing enforces that.
    Making the law depend on the sign would turn a harmless configuration style
    into an inverted longitudinal channel.
    """
    assert project_marker_to_body(0.1, 0.2, 1.0, -80.0) == project_marker_to_body(
        0.1, 0.2, 1.0, 80.0
    )


# ═════════════════════════════════════════════ validating the SDK's pose tuple


def test_a_marker_with_the_wrong_identity_is_rejected():
    """The landing pad is an identity, not a shape.

    Stage 5 is the last stage: there is nothing downstream to notice that the
    aircraft settled onto the wrong marker, so a non-matching ID is discarded
    rather than flown on with reduced confidence.
    """
    assert (
        MarkerObservation.from_pose_estimate(3, [0.1, 0.2, 1.0], 0.0, expected_id=0) is None
    )
    accepted = MarkerObservation.from_pose_estimate(0, [0.1, 0.2, 1.0], 0.0, expected_id=0)
    assert accepted is not None and accepted.marker_id == 0


def test_an_absent_detection_is_rejected_whichever_member_is_missing():
    assert MarkerObservation.from_pose_estimate(None, None, None, expected_id=0) is None
    assert MarkerObservation.from_pose_estimate(0, None, None, expected_id=0) is None
    assert MarkerObservation.from_pose_estimate(None, [0.0, 0.0, 1.0], 0.0, expected_id=0) is None


def test_a_non_finite_pose_is_rejected():
    """NaN compares false against every bound, so a clamp cannot contain it."""
    for corrupt in ([float("nan"), 0.0, 1.0], [0.0, float("inf"), 1.0]):
        assert MarkerObservation.from_pose_estimate(0, corrupt, 0.0, expected_id=0) is None


def test_a_pose_behind_the_camera_is_rejected():
    """``solvePnP`` reports a mirrored solution for a near-edge-on marker.

    A non-positive range along the optical axis is that solution announcing
    itself. Both error channels are inverted in it, so flying a PD law on it
    drives the aircraft away from the pad under a stable-looking loop.
    """
    assert MarkerObservation.from_pose_estimate(0, [0.0, 0.0, -1.0], 0.0, expected_id=0) is None
    assert MarkerObservation.from_pose_estimate(0, [0.0, 0.0, 0.0], 0.0, expected_id=0) is None


def test_a_numpy_translation_and_an_unusable_yaw_are_both_tolerated():
    """The SDK returns NumPy arrays, and its yaw is decorative here."""
    numpy = pytest.importorskip("numpy")
    observation = MarkerObservation.from_pose_estimate(
        numpy.int32(0), numpy.array([0.1, 0.2, 1.0]), float("nan"), expected_id=0
    )
    assert observation is not None
    assert observation.yaw_deg is None
    assert observation.slant_range_m == pytest.approx(math.sqrt(0.01 + 0.04 + 1.0))


# ══════════════════════════════════════════════════════ the sensor adapter


class FakeDetector:
    """Stands in for ``nectar.vision.Aruco`` with a scripted return sequence.

    Drives the ``"sdk"`` pose path only, which is why every test using it pins
    that backend explicitly rather than letting the runtime choose: on an
    OpenCV that removed ``estimatePoseSingleMarkers`` the sensor would otherwise
    take the ``solvepnp`` path and never call this at all.
    """

    tag_size = 0.20

    def __init__(self, returns, *, raises: bool = False):
        self._returns = list(returns)
        self._raises = raises
        self.calls = 0
        self.drawn: List[bool] = []

    def pose_estimate(self, img, draw=False):
        self.calls += 1
        self.drawn.append(bool(draw))
        if self._raises:
            raise RuntimeError("degenerate corner set")
        return self._returns[min(self.calls - 1, len(self._returns) - 1)]


def sdk_sensor(detector, target_id):
    return ArucoMarkerSensor(detector, target_id=target_id, backend="sdk")


def test_the_sensor_filters_by_identity():
    detector = FakeDetector([(9, [0.0, 0.0, 1.0], 0.0), (2, [0.0, 0.0, 1.0], 0.0)])
    sensor = sdk_sensor(detector, 2)

    assert sensor.observe(object()) is None
    accepted = sensor.observe(object())
    assert accepted is not None and accepted.marker_id == 2


def test_a_detector_exception_is_absorbed_as_a_missing_observation():
    """Perception runs on a lossy H.264 link and must never end a flight."""
    sensor = sdk_sensor(FakeDetector([], raises=True), 0)
    assert sensor.observe(object()) is None
    assert sensor.observe(object()) is None
    assert sensor.failures == 2


def test_no_frame_and_no_marker_are_the_same_answer():
    detector = FakeDetector([(0, [0.0, 0.0, 1.0], 0.0)])
    sensor = sdk_sensor(detector, 0)
    assert sensor.observe(None) is None
    assert detector.calls == 0, "a missing frame must not reach the detector"


def test_the_sensor_asks_the_detector_to_annotate():
    """The annotated frame is the only in-flight evidence of recognition."""
    detector = FakeDetector([(0, [0.0, 0.0, 1.0], 0.0)])
    sdk_sensor(detector, 0).observe(object())
    assert detector.drawn == [True]


def test_an_unknown_backend_is_rejected_rather_than_silently_disabling_vision():
    with pytest.raises(ValueError):
        ArucoMarkerSensor(FakeDetector([]), target_id=0, backend="guess")


class FakeDetectDetector:
    """Drives the ``"solvepnp"`` path: detection only, plus the SDK intrinsics.

    Corners are a centred square, so the recovered pose must be a marker
    straight ahead down the optical axis: lateral and vertical components at
    zero, range positive.
    """

    tag_size = 0.20
    camera_matrix = [[800.0, 0.0, 428.0], [0.0, 800.0, 240.0], [0.0, 0.0, 1.0]]
    camera_distortion = [0.0, 0.0, 0.0, 0.0, 0.0]

    def __init__(self, marker_id=0, centre=(428.0, 240.0), half_px=60.0):
        import numpy

        self.marker_id = marker_id
        cx, cy = centre
        self._bbox = (
            numpy.array(
                [[
                    [cx - half_px, cy - half_px],
                    [cx + half_px, cy - half_px],
                    [cx + half_px, cy + half_px],
                    [cx - half_px, cy + half_px],
                ]],
                dtype=numpy.float32,
            ),
        )
        self.drawn: List[bool] = []

    def detect(self, img, draw=False):
        self.drawn.append(bool(draw))
        return self._bbox, self.marker_id

    @staticmethod
    def calculateYawFromCorners(_bbox):
        return 0.0


def test_the_solvepnp_backend_reproduces_a_pose_from_detection_alone():
    """The fallback is not a stub: it must return a usable metric pose.

    Everything but the solve still comes from the SDK object -- the dictionary,
    the detection, the intrinsics, the tag size, the yaw -- so what this checks
    is that replacing the one call OpenCV deleted produces the same quantity in
    the same frame.
    """
    import numpy

    detector = FakeDetectDetector(marker_id=4)
    sensor = ArucoMarkerSensor(detector, target_id=4, backend="solvepnp")

    observation = sensor.observe(numpy.zeros((480, 856, 3), dtype=numpy.uint8))
    assert observation is not None
    assert observation.marker_id == 4
    # A centred square is straight down the optical axis.
    assert observation.x_cam_m == pytest.approx(0.0, abs=1e-6)
    assert observation.y_cam_m == pytest.approx(0.0, abs=1e-6)
    # Pinhole: range = focal * tag_size / apparent_size.
    assert observation.z_cam_m == pytest.approx(800.0 * 0.20 / 120.0, rel=1e-3)


def test_the_solvepnp_backend_still_filters_by_identity():
    import numpy

    sensor = ArucoMarkerSensor(FakeDetectDetector(marker_id=9), target_id=4, backend="solvepnp")
    assert sensor.observe(numpy.zeros((480, 856, 3), dtype=numpy.uint8)) is None


def test_the_solvepnp_backend_reports_the_expected_lateral_sign():
    """A marker right of the principal point is right of the aircraft."""
    import numpy

    frame = numpy.zeros((480, 856, 3), dtype=numpy.uint8)
    right = ArucoMarkerSensor(
        FakeDetectDetector(centre=(528.0, 240.0)), target_id=0, backend="solvepnp"
    ).observe(frame)
    assert right is not None and right.x_cam_m > 0.0
    assert project_marker_to_body(right.x_cam_m, right.y_cam_m, right.z_cam_m, TILT)[1] < 0.0


# ════════════════════════════════════════════════════════ the centering law


def fly_to_centre(
    controller: ArucoCenteringController,
    ex: float,
    ey: float,
    *,
    altitude: float = 1.0,
    cycles: int = 900,
    noise: float = 0.0,
    seed: int = 7,
) -> Tuple[float, float, List[CenteringCommand]]:
    """Close the loop against a first-order plant and report where it ended.

    The plant is the honest one for this airframe: the normalized command *is*
    the velocity, under the identity speed calibration, and the offset integrates
    it. Noise is injected on the observation rather than on the state, because
    that is where it actually lives -- the pad does not jitter, the pose estimate
    does.
    """
    rng = random.Random(seed)
    history: List[CenteringCommand] = []
    for _ in range(cycles):
        observed_ex = ex + (rng.gauss(0.0, noise) if noise else 0.0)
        observed_ey = ey + (rng.gauss(0.0, noise) if noise else 0.0)
        command = controller.update(sighting(observed_ex, observed_ey, altitude), DT)
        history.append(command)
        ex -= command.vx * DT
        ey -= command.vy * DT
        if command.settled:
            break
    return ex, ey, history


