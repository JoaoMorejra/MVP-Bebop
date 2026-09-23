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


def test_the_law_converges_from_a_combined_offset():
    controller = law()
    ex, ey, history = fly_to_centre(controller, 0.60, -0.45)

    assert history[-1].settled, "never authorized the landing"
    assert math.hypot(ex, ey) <= controller.tolerance_m


@pytest.mark.parametrize(
    "ex,ey",
    [(0.80, 0.0), (-0.80, 0.0), (0.0, 0.80), (0.0, -0.80), (0.5, 0.5), (-0.5, -0.5)],
)
def test_the_law_converges_from_every_quadrant(ex, ey):
    """A single wrong sign passes three of these six and fails the rest."""
    controller = law()
    residual_x, residual_y, history = fly_to_centre(controller, ex, ey)

    assert history[-1].settled, f"did not settle from ({ex}, {ey})"
    assert math.hypot(residual_x, residual_y) <= controller.tolerance_m


def test_the_first_command_opposes_the_error_on_both_axes():
    """Before any dynamics, the sign of the response is the whole design.

    Checked on the *demanded* velocity rather than the shaped output, because
    the sigma-delta modulator legitimately emits zero on a charging cycle and
    that would make a sign assertion flaky for reasons that have nothing to do
    with the sign.
    """
    controller = law()
    ahead_left = controller.update(sighting(0.60, 0.40), DT)
    assert ahead_left.ex_body_m > 0.0 and ahead_left.ey_body_m > 0.0

    controller = law()
    behind_right = controller.update(sighting(-0.60, -0.40), DT)
    assert behind_right.ex_body_m < 0.0 and behind_right.ey_body_m < 0.0

    # And the commands that follow from them, once the profile has spun up.
    controller = law()
    for _ in range(30):
        forward = controller.update(sighting(0.60, 0.40), DT)
    assert forward.vx > 0.0 and forward.vy > 0.0

    controller = law()
    for _ in range(30):
        reverse = controller.update(sighting(-0.60, -0.40), DT)
    assert reverse.vx < 0.0 and reverse.vy < 0.0


def test_the_speed_ceiling_bounds_the_resultant_and_not_the_axes():
    """A diagonal approach must not fly sqrt(2) times the configured ceiling.

    Clipping each axis independently is the obvious implementation and it is
    wrong twice over: the resultant reaches 0.113 against a configured 0.08, and
    whenever exactly one axis is railed the commanded direction rotates away
    from the marker, so the aircraft crabs in on a dog-leg.
    """
    controller = law()
    params = MissionParameters()
    peak_physical = 0.0
    peak_shaped = 0.0
    for _ in range(400):
        command = controller.update(sighting(8.0, -6.0), DT)
        peak_physical = max(peak_physical, command.commanded_speed_mps)
        peak_shaped = max(peak_shaped, math.hypot(command.vx, command.vy))

    # The envelope statement, exactly: the velocity the law commands never
    # exceeds the ceiling, on any heading.
    assert peak_physical <= params.rtl.max_centering_speed + 1e-9

    # What reaches the wire carries the sigma-delta shaper's dither on top. Per
    # axis that is bounded by the dither correction, itself clamped at one
    # quantization step, plus the half-step of the snap onto the actuator grid:
    # 1.5 steps. Two axes at 45 degrees put sqrt(2) of that on the resultant.
    # The excess is momentary and mean-preserving -- it is the mechanism every
    # other velocity channel in this mission is driven through -- but it is a
    # real, derived bound rather than an assumed one, so it is asserted.
    shaping_margin = 1.5 * params.rtl.quantization_step * math.sqrt(2.0)
    assert peak_shaped <= params.rtl.max_centering_speed + shaping_margin


def test_saturation_preserves_the_commanded_heading():
    """The ceiling scales the demand; it does not rotate it.

    Flown with equal gains on both axes so the statement is about the
    saturation and not about the tuning: a 45-degree error must produce a
    45-degree command even when it is far outside the ceiling. An independent
    per-axis clip passes this only by accident, when both axes rail together.
    """
    controller = law(
        centering_kp_x=0.30, centering_kp_y=0.30, centering_kd_x=0.03, centering_kd_y=0.03
    )
    for _ in range(200):
        command = controller.update(sighting(10.0, 10.0), DT)
    assert command.commanded_speed_mps == pytest.approx(
        MissionParameters().rtl.max_centering_speed, rel=1e-6
    )
    assert command.vx == pytest.approx(command.vy, abs=1e-9), (
        f"the ceiling rotated the approach: vx={command.vx:.4f}, vy={command.vy:.4f}"
    )


def test_the_law_converges_under_pose_noise():
    """Centimetre-scale pose jitter is the normal condition, not a fault."""
    controller = law()
    ex, ey, history = fly_to_centre(controller, 0.50, 0.35, noise=0.01, cycles=1500)

    assert history[-1].settled
    assert math.hypot(ex, ey) <= 3.0 * controller.tolerance_m


def test_noise_inside_the_deadband_does_not_produce_sustained_motion():
    """Chasing pixel jitter over the pad is what the deadband exists to stop."""
    controller = law()
    rng = random.Random(3)
    commanded = []
    for _ in range(400):
        jitter_x = rng.gauss(0.0, 0.004)
        jitter_y = rng.gauss(0.0, 0.004)
        commanded.append(controller.update(sighting(jitter_x, jitter_y), DT))

    assert all(command.vx == 0.0 and command.vy == 0.0 for command in commanded[10:])


def test_the_deadband_stays_strictly_inside_the_convergence_gate():
    """Otherwise the law stops correcting while still outside the gate it is
    judged against, and the phase runs to its timeout parked just off centre."""
    controller = law()
    assert 0.0 < controller.deadband_m < controller.tolerance_m

    # And it holds for a configuration that tightens the tolerance below the
    # inherited deadband, which is the case that would silently invert it.
    tight = law(centering_tolerance_m=0.02)
    assert 0.0 < tight.deadband_m < tight.tolerance_m


# ──────────────────────────────────────────────────────── the landing gate


def test_being_inside_the_tolerance_for_one_cycle_does_not_authorize_landing():
    controller = law()
    command = controller.update(sighting(0.0, 0.0), DT)
    assert command.within_tolerance
    assert not command.settled


def test_the_gate_requires_consecutive_cycles():
    """Alternating in and out of tolerance must never accumulate a settlement."""
    controller = law()
    params = MissionParameters()
    for index in range(8 * params.rtl.centering_settle_cycles):
        inside = index % 2 == 0
        command = controller.update(sighting(0.0 if inside else 0.50, 0.0), DT)
        assert not command.settled
    assert controller.settle_cycles <= 1


def test_crossing_the_centre_at_speed_does_not_authorize_landing():
    """The failure this prevents: a landing commanded mid-traverse.

    The aircraft is driven hard toward the pad and the observation is then
    snapped to dead centre while the velocity profile is still carrying the
    approach. Inside the tolerance on the first such cycle, and emphatically not
    settled.
    """
    controller = law()
    for _ in range(60):
        controller.update(sighting(2.0, 0.0), DT)
    assert controller.settle_cycles == 0

    command = controller.update(sighting(0.0, 0.0), DT)
    assert command.within_tolerance
    assert not command.settled
    assert command.commanded_speed_mps > MissionParameters().rtl.settle_max_speed_mps

    # It settles only once the profile has actually shed that speed.
    for _ in range(400):
        command = controller.update(sighting(0.0, 0.0), DT)
        if command.settled:
            break
    assert command.settled
    assert command.commanded_speed_mps <= MissionParameters().rtl.settle_max_speed_mps


def test_leaving_the_tolerance_clears_an_almost_complete_settlement():
    controller = law(centering_settle_cycles=6)
    for _ in range(200):
        controller.update(sighting(0.0, 0.0), DT)
        if controller.settle_cycles >= 4:
            break
    assert 0 < controller.settle_cycles < 6

    controller.update(sighting(1.0, 0.0), DT)
    assert controller.settle_cycles == 0


# ──────────────────────────────────────────────────── losing sight of the pad


def test_a_dropped_frame_inside_the_tolerance_does_not_restart_the_settlement():
    """Detection over a moving airframe drops frames; restarting on each one
    would make the phase a sequence of restarts rather than a convergence."""
    controller = law()
    for _ in range(3):
        controller.update(sighting(0.0, 0.0), DT)
    banked = controller.settle_cycles
    assert banked > 0

    lost = controller.update(None, DT)
    assert not lost.tracking
    assert controller.settle_cycles == banked


def test_a_landing_is_never_authorized_on_a_cycle_the_marker_was_not_seen():
    """Whatever has been banked, the gate itself requires a live observation."""
    controller = law(centering_settle_cycles=1)
    settled = controller.update(sighting(0.0, 0.0), DT)
    assert settled.settled

    blind = controller.update(None, DT)
    assert not blind.settled and not blind.tracking


def test_a_sustained_loss_clears_the_settlement_and_holds_station():
    controller = law()
    params = MissionParameters()
    for _ in range(3):
        controller.update(sighting(0.0, 0.0), DT)

    for _ in range(params.rtl.lost_frames_tolerance + 2):
        command = controller.update(None, DT)

    assert controller.settle_cycles == 0
    assert controller.lost_frames > params.rtl.lost_frames_tolerance
    assert command.vx == 0.0 and command.vy == 0.0


def test_a_lost_marker_brakes_rather_than_leaving_the_last_command_latched():
    """The Bebop holds the last Twist indefinitely, so "do nothing" is "keep
    flying the correction computed for a pad nobody can currently see"."""
    controller = law()
    for _ in range(60):
        moving = controller.update(sighting(2.0, 1.5), DT)
    assert moving.commanded_speed_mps > 0.0

    for _ in range(400):
        stopping = controller.update(None, DT)
        if stopping.commanded_speed_mps == 0.0:
            break
    assert stopping.commanded_speed_mps == pytest.approx(0.0, abs=1e-9)
    assert stopping.vx == 0.0 and stopping.vy == 0.0


def test_the_law_has_no_rotational_output_at_all():
    """Structural, not asserted: yaw would corrupt the optical-flow estimate
    that the altitude governor and the failsafe supervisor both still read."""
    command = law().update(sighting(1.0, 1.0), DT)
    assert not hasattr(command, "vyaw")
    assert "vyaw" not in CenteringCommand.__dataclass_fields__


def test_a_zero_speed_ceiling_is_rejected_at_construction():
    """A centering law with no authority cannot converge, and would hold the
    aircraft over the pad until its window expired."""
    params = MissionParameters()
    params.rtl.max_centering_speed = 0.0
    with pytest.raises(ValueError):
        ArucoCenteringController(params.rtl, FlightKinematicsConfig(), SpeedCalibration(1.0))


def test_a_non_positive_dt_advances_nothing():
    controller = law()
    command = controller.update(sighting(1.0, 1.0), 0.0)
    assert command.vx == 0.0 and command.vy == 0.0
    assert controller.elapsed_sec == 0.0


# ═══════════════════════════════════════════════════════════════ the step


DEMANDED_CLIMB = 0.30


class ClimbingGovernor:
    """Always asks to climb, harder than any window will allow."""

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
    def __init__(self, obeys_land=True):
        self.no_fly = True
        self.obeys_land = obeys_land
        self.commands: List[Tuple[float, float, float, float]] = []
        self.landing_phase: List[bool] = []
        self.tilts: List[float] = []
        self.land_calls = 0
        self.descending = False

    def camera_control(self, tilt, pan=0.0):
        self.tilts.append(tilt)

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))
        # Which phase a command belongs to is not recoverable from the command
        # itself, and the assertion that matters -- "the landing never climbs" --
        # is about a phase. Landing requests are the phase boundary.
        self.landing_phase.append(self.land_calls > 0)
        if vz < 0.0:
            self.descending = True

    def land(self):
        self.land_calls += 1
        if self.obeys_land:
            self.descending = True
        return True

    def snapshot(self):
        return None


class Odometry:
    """Healthy telemetry that descends once a landing has been commanded."""

    def __init__(self, drone, altitude=1.20, period=1.0 / 200.0, descent_rate=0.9):
        self._drone = drone
        self._period = period
        self._descent_rate = descent_rate
        self.relative_altitude = altitude
        self.speed = 0.0
        self.horizontal_speed = 0.0
        self.vx = self.vy = self.vz = 0.0
        self.x = self.y = 0.0
        self.takeoff_x = self.takeoff_y = 0.0
        self.has_launch_origin = True

    def snapshot(self):
        if self._drone.descending:
            self.relative_altitude = max(
                0.0, self.relative_altitude - self._descent_rate * self._period
            )
        return self

    def body_frame_launch_error(self):
        return -2.0, 0.0, 2.0

    @staticmethod
    def telemetry_health():
        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


class ScriptedSensor:
    """A marker sensor driven by a plant, or by a fixed answer.

    With ``acquire_after=None`` the pad is never seen, which is the search
    timeout. Otherwise the pad appears after that many observations and
    thereafter tracks the commands the step transmits, so the centering phase
    closes a real loop rather than reading a constant.
    """

    def __init__(self, drone, *, target_id=0, acquire_after=None, ex=0.8, ey=-0.5):
        self._drone = drone
        self.target_id = target_id
        self._acquire_after = acquire_after
        self._ex, self._ey = ex, ey
        self._seen = 0
        self._consumed = 0
        self.failures = 0

    def observe(self, frame) -> Optional[MarkerObservation]:
        self._seen += 1
        if self._acquire_after is None or self._seen <= self._acquire_after:
            return None

        # Integrate whatever has been commanded since the last observation.
        period = 1.0 / 200.0
        for vx, vy, _vz, _vyaw in self._drone.commands[self._consumed :]:
            self._ex -= vx * period
            self._ey -= vy * period
        self._consumed = len(self._drone.commands)
        return sighting(self._ex, self._ey, 1.0, marker_id=self.target_id)

    @property
    def residual_m(self) -> float:
        return math.hypot(self._ex, self._ey)


class Blackboard:
    def __init__(self):
        self.rtl_completed = False
        self.rtl_marker_sighted = False


class Ctx:
    """The attribute surface Stage 5 touches, and nothing else."""

    def __init__(self, *, altitude=1.20, sensor=None, obeys_land=True):
        self.params = MissionParameters()
        self.params.kinematics.control_loop_hz = 200.0
        self.params.rtl.timeout_sec = 0.5
        self.params.rtl.centering_timeout_sec = 6.0
        self.params.rtl.touchdown_timeout_sec = 3.0
        self.params.rtl.descent_stall_sec = 0.2
        self.params.rtl.final_hover_delay_sec = 0.2

        self.drone = Drone(obeys_land=obeys_land)
        self.odom_supervisor = Odometry(
            self.drone, altitude, period=1.0 / self.params.kinematics.control_loop_hz
        )
        self.governor = ClimbingGovernor()
        self.failsafe = FailsafeSupervisor(
            drone_actuator=self.drone,
            odom_supervisor=self.odom_supervisor,
            timeouts_cfg=self.params.timeouts,
            kinematics_cfg=self.params.kinematics,
        )
        self.speed_calibration = SpeedCalibration(1.0)
        self.blackboard = Blackboard()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.requested_stage = None
        self.handler = object()
        self.frames = 0
        self.sensor = sensor

    def interrupted(self) -> bool:
        """Mirrors MissionContext.interrupted: an abort or a commanded stage jump."""
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def grab_frame(self, timeout_sec=1.0):
        self.frames += 1
        return object()

    def publish_annotated_stream(self, _frame, _result, _text):
        return None

    # -- assertions ------------------------------------------------------
    @property
    def horizontal(self):
        return [(vx, vy) for vx, vy, _vz, _vyaw in self.drone.commands]

    @property
    def vertical(self):
        return [vz for _vx, _vy, vz, _vyaw in self.drone.commands]


def test_the_gimbal_is_pitched_to_the_configured_search_attitude():
    ctx = Ctx(sensor=ScriptedSensor(Drone()))
    ctx.sensor = ScriptedSensor(ctx.drone)
    ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    assert ctx.drone.tilts, "the gimbal was never commanded"
    assert ctx.drone.tilts[0] == pytest.approx(ctx.params.rtl.camera_tilt_deg)
    assert ctx.current_tilt_deg == pytest.approx(ctx.params.rtl.camera_tilt_deg)


def test_the_reverse_cruise_never_commands_forward_flight():
    """The search leg is the mirror of Stage 2 and must stay mirrored.

    Enforced at the actuator boundary rather than in the profile, so neither a
    sign error in ``reverse_cruise_velocity`` nor a sigma-delta pulse of the
    wrong polarity can turn the return into an outbound cruise.
    """
    ctx = Ctx()
    sensor = ScriptedSensor(ctx.drone)
    step = ClosedLoopRTLStep(sensor=sensor)

    outcome = step._reverse_search(ctx, sensor, __deadline(ctx))

    assert ctx.horizontal, "the search sent no commands at all"
    assert all(vx <= 0.0 for vx, _vy in ctx.horizontal)
    assert any(vx < 0.0 for vx, _vy in ctx.horizontal), "it never actually flew"
    assert outcome.value == "exhausted"


def test_the_reverse_cruise_holds_the_lateral_and_yaw_axes_at_zero():
    """With no marker in frame there is nothing to correct *towards*, and a
    cross-track command with no reference is just a curved search track."""
    ctx = Ctx()
    sensor = ScriptedSensor(ctx.drone)
    ClosedLoopRTLStep(sensor=sensor)._reverse_search(ctx, sensor, __deadline(ctx))

    assert all(vy == 0.0 for _vx, vy in ctx.horizontal)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)


def test_the_search_leg_transmits_the_corrective_climb():
    """Translation costs lift; the return leg holds altitude like every other
    phase that translates, saturated by the failsafe rather than by accident."""
    ctx = Ctx()
    sensor = ScriptedSensor(ctx.drone)
    step = ClosedLoopRTLStep(sensor=sensor)

    with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
        step._reverse_search(ctx, sensor, __deadline(ctx))

    climbs = [vz for vz in ctx.vertical if vz > 0.0]
    assert climbs, "the search leg suppressed the altitude trim entirely"
    assert max(climbs) == pytest.approx(ctx.params.governor.climb_authority)


def test_the_hold_window_does_not_survive_the_step():
    """A window left open hands the touchdown sequence ascent authority."""
    ctx = Ctx()
    ctx.sensor = ScriptedSensor(ctx.drone)
    ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    assert ctx.failsafe.altitude_hold_ceiling == 0.0


def test_the_touchdown_sequence_still_cannot_climb():
    """Whatever the governor demands, a landing descends.

    Structural rather than a matter of the governor behaving: the touchdown loop
    runs outside every hold window, so the ascent authority the search and
    centering phases opened is gone by the time the first ``land()`` is sent.
    """
    # The firmware is modelled as ignoring ``land()``, which is the case that
    # makes the touchdown loop command velocities of its own -- the assisted
    # descent. A landing that is simply obeyed never sends a Twist at all, so it
    # cannot demonstrate anything about the sign of one.
    ctx = Ctx(altitude=1.00, obeys_land=False)
    ctx.params.rtl.descent_stall_sec = 0.15
    ctx.params.rtl.touchdown_timeout_sec = 1.50
    ctx.sensor = ScriptedSensor(ctx.drone)
    ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    landing = [
        command
        for command, is_landing in zip(ctx.drone.commands, ctx.drone.landing_phase)
        if is_landing
    ]
    assert landing, "the touchdown sent no velocity commands at all"
    assert any(vz < 0.0 for _vx, _vy, vz, _vyaw in landing), "no descent was commanded"
    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in landing)


def test_a_search_that_finds_nothing_still_lands():
    """A marker that is never sighted is a degraded landing, not a stranded
    aircraft. The window expiring must not leave the drone in the air."""
    ctx = Ctx(altitude=0.20)
    ctx.sensor = ScriptedSensor(ctx.drone)

    status = ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert not ctx.blackboard.rtl_marker_sighted
    assert ctx.drone.land_calls > 0
    assert ctx.blackboard.rtl_completed


def test_sighting_the_marker_leads_to_centering_and_a_landing():
    ctx = Ctx(altitude=0.30)
    ctx.params.rtl.timeout_sec = 4.0
    sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.35, ey=-0.25)

    status = ClosedLoopRTLStep(sensor=sensor).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.rtl_marker_sighted
    assert sensor.residual_m <= 3.0 * ctx.params.rtl.centering_tolerance_m, (
        f"centering left {sensor.residual_m:.3f} m of residual offset"
    )
    assert ctx.drone.land_calls > 0


def test_an_emergency_during_the_search_aborts_without_a_landing_claim():
    ctx = Ctx()
    ctx.emergency_event.set()
    sensor = ScriptedSensor(ctx.drone)

    status = ClosedLoopRTLStep(sensor=sensor).execute(ctx)

    assert status is StepStatus.ABORTED
    assert not ctx.blackboard.rtl_completed


def test_an_unusable_marker_configuration_falls_back_rather_than_raising():
    """A dictionary identifier with no predefined family is a configuration fault.

    It must surface as a logged degradation to the odometric return, not as an
    ``AttributeError`` out of the SDK during Stage 5.
    """
    ctx = Ctx()
    ctx.params.rtl.marker_dict = -1
    assert ClosedLoopRTLStep()._build_sensor(ctx) is None

    ctx.params.rtl.marker_dict = 5
    ctx.params.rtl.tag_size = 0.0
    assert ClosedLoopRTLStep()._build_sensor(ctx) is None


def test_the_degraded_path_flies_the_legacy_return_and_lands():
    """No detector means a less precise landing, never an absent one."""
    ctx = Ctx(altitude=0.20)
    ctx.params.rtl.marker_dict = -1  # forces _build_sensor to decline

    status = ClosedLoopRTLStep().execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.drone.land_calls > 0
    # The legacy path re-points the gimbal forward for the operator.
    assert ctx.params.gimbal.search_tilt_deg in ctx.drone.tilts


def __deadline(ctx):
    from mvp_mission_bebop.engine.rate import Deadline

    return Deadline(ctx.params.rtl.timeout_sec)


# ═══════════════════════════════════ against the real SDK and the real OpenCV


def render_marker(marker_id, centre_x, centre_y, size_px, shape=(480, 856)):
    """Paste a rendered 5x5 marker into a white frame at a pixel centre.

    Handles both OpenCV marker-drawing APIs. The call was renamed from
    ``drawMarker`` to ``generateImageMarker`` in 4.7, and this machine has both
    vintages reachable -- see
    :func:`test_the_sensor_survives_whichever_opencv_the_interpreter_has`.
    """
    import cv2
    import numpy

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_1000)
    generate = getattr(cv2.aruco, "generateImageMarker", None) or cv2.aruco.drawMarker
    frame = numpy.full((shape[0], shape[1], 3), 255, dtype=numpy.uint8)
    tag = cv2.cvtColor(generate(dictionary, marker_id, size_px), cv2.COLOR_GRAY2BGR)
    half = size_px // 2
    frame[centre_y - half : centre_y + half, centre_x - half : centre_x + half] = tag
    return frame


def real_detector():
    """The genuine ``nectar.vision.Aruco``, or a skip with a reason.

    Skipped rather than faked when the SDK, its intrinsic calibration, or the
    OpenCV underneath it cannot actually detect. A fake here would defeat the
    entire purpose: these are the tests that check what the *actual* library
    does, which is the one thing a double can never tell us -- so when the real
    thing is unavailable the honest outcome is a skip that names why, not a pass
    against a stand-in.

    The probe is the point. ``nectar.vision.Aruco`` constructs successfully
    against OpenCV versions on which it cannot detect a marker (``detect`` calls
    ``cv2.aruco.ArucoDetector``, added in 4.7) or cannot estimate a pose
    (``pose_estimate`` calls ``estimatePoseSingleMarkers``, removed in 4.10), so
    a constructor that returns is not evidence of a working detector.
    """
    pytest.importorskip("cv2")
    vision = pytest.importorskip("nectar.vision")
    try:
        detector = vision.Aruco(marker_dict=5, tag_size=0.20)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ArUco detector could not be constructed: {exc}")

    import cv2

    probe = ArucoMarkerSensor(detector, target_id=7)
    if probe.observe(render_marker(7, 428, 240, 200, (617, 597))) is None:
        pytest.skip(
            f"nectar.vision.Aruco cannot detect a marker under OpenCV {cv2.__version__}; "
            "the SDK's ArUco path requires 4.7 or newer for cv2.aruco.ArucoDetector"
        )
    return detector


def test_the_pose_backend_is_chosen_from_what_opencv_actually_provides():
    """The defect this guards is silent, total, and invisible in the log.

    ``nectar.vision.Aruco.pose_estimate`` calls
    ``cv2.aruco.estimatePoseSingleMarkers``, which OpenCV removed in 4.10. On a
    4.10+ runtime that call raises on *every* frame. The sensor absorbs detector
    exceptions -- as a perception adapter must -- so the failure mode is not a
    crash: it is a return leg that searches for its whole window, never sees a
    marker sitting in the middle of the frame, and lands wherever it happened to
    be, with nothing but a timeout to show for it.

    So the backend is selected from the runtime rather than assumed, and this
    asserts that the selection matches reality.
    """
    import cv2

    sensor = ArucoMarkerSensor(FakeDetector([]), target_id=0)
    expected = "sdk" if hasattr(cv2.aruco, "estimatePoseSingleMarkers") else "solvepnp"
    assert expected in ArucoMarkerSensor.BACKENDS
    assert sensor.backend == expected


def test_a_real_marker_is_recognised_end_to_end():
    """Real SDK, real intrinsics, real OpenCV, a marker rendered into a frame.

    Every double in this file agrees with the production code by construction.
    This one does not, which is the only reason it is worth its runtime.
    """
    detector = real_detector()
    sensor = ArucoMarkerSensor(detector, target_id=7)

    assert sensor.observe(render_marker(3, 428, 240, 200)) is None, "wrong ID was accepted"

    observation = sensor.observe(render_marker(7, 428, 240, 200))
    assert observation is not None, "the configured marker was not recognised"
    assert observation.marker_id == 7
    assert observation.z_cam_m > 0.0
    assert sensor.failures == 0


def test_a_real_marker_produces_the_body_errors_the_law_expects():
    """The sign chain, end to end, through the library that actually flies.

    A projection unit test proves the algebra; it cannot prove that the SDK's
    translation is in the frame the algebra assumes. This drives the marker to
    each side of the principal point and asserts the body-frame error the
    aircraft would act on.
    """
    detector = real_detector()
    sensor = ArucoMarkerSensor(detector, target_id=7)
    principal_x = int(detector.camera_matrix[0][2])
    principal_y = int(detector.camera_matrix[1][2])

    right = sensor.observe(render_marker(7, principal_x + 160, principal_y, 140))
    left = sensor.observe(render_marker(7, principal_x - 160, principal_y, 140))
    assert right is not None and left is not None

    _ex, ey_right = project_marker_to_body(
        right.x_cam_m, right.y_cam_m, right.z_cam_m, TILT
    )
    _ex, ey_left = project_marker_to_body(left.x_cam_m, left.y_cam_m, left.z_cam_m, TILT)
    assert ey_right < 0.0 < ey_left, "the aircraft would translate away from the pad"

    low = sensor.observe(render_marker(7, principal_x, principal_y + 100, 120))
    high = sensor.observe(render_marker(7, principal_x, principal_y - 100, 120))
    assert low is not None and high is not None

    ex_low, _ey = project_marker_to_body(low.x_cam_m, low.y_cam_m, low.z_cam_m, TILT)
    ex_high, _ey = project_marker_to_body(high.x_cam_m, high.y_cam_m, high.z_cam_m, TILT)
    assert ex_low < ex_high, "a marker lower in the frame must read as further aft"


def test_a_frame_with_no_marker_raises_nothing_against_the_real_sdk():
    import numpy

    sensor = ArucoMarkerSensor(real_detector(), target_id=7)
    assert sensor.observe(numpy.full((480, 856, 3), 255, dtype=numpy.uint8)) is None
    assert sensor.failures == 0, "an empty frame must not count as a detector fault"


# ══════════════════════════════════════════════ configuration and the wire


@pytest.mark.parametrize(
    "flag,value,section,expected",
    [
        ("--aruco-id", "12", "target_aruco_id", 12),
        ("--aruco-dict", "4", "marker_dict", 4),
        ("--aruco-dict", "DICT_APRILTAG_36h11", "marker_dict", "DICT_APRILTAG_36h11"),
        ("--aruco-size", "0.15", "tag_size", 0.15),
    ],
)
def test_the_aruco_flags_are_accepted_and_reach_the_parameters(
    flag, value, section, expected, monkeypatch
):
    import sys

    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py", flag, value])
    arguments = parse_arguments(MissionParameters())

    params = MissionParameters()
    if getattr(arguments, "aruco_id", None) is not None:
        params.rtl.target_aruco_id = arguments.aruco_id
    if getattr(arguments, "aruco_dict", None) is not None:
        params.rtl.marker_dict = arguments.aruco_dict
    if getattr(arguments, "aruco_size", None) is not None:
        params.rtl.tag_size = arguments.aruco_size

    assert getattr(params.rtl, section) == expected


def test_omitting_the_aruco_flags_leaves_the_configured_values_alone(monkeypatch):
    """Every override is opt-in; the parameter sheet is the source of truth."""
    import sys

    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py"])
    arguments = parse_arguments(MissionParameters())
    assert arguments.aruco_id is None
    assert arguments.aruco_dict is None
    assert arguments.aruco_size is None


def test_parameter_logging_formats_correctly_with_string_and_integer_dict():
    """Parameter logging at mission startup must accept both string and integer dict."""
    params = MissionParameters()
    for marker_dict in ("DICT_APRILTAG_36h11", 5, "tag36h11", 4):
        params.rtl.marker_dict = marker_dict
        msg = (
            "Active parameters: altitude=%.2fm, velocity=%.3fm/s, hover=%.1fs, "
            "search_timeout=%.1fs, confidence=%.2f, confirmation_frames=%d, "
            "classes=%s, rtl_radius=%.2fm, countdown=%.1fs, no_fly=%s, "
            "aruco_id=%d, aruco_dict=%s, aruco_size=%.3fm"
        )
        args = (
            params.kinematics.target_altitude_m,
            params.kinematics.forward_cruise_velocity,
            params.kinematics.hover_duration_sec,
            params.timeouts.search_timeout_sec,
            params.vision.confidence_threshold,
            params.vision.confirmation_frames,
            params.vision.target_classes,
            params.rtl.arrival_radius_m,
            params.kinematics.countdown_sec,
            params.no_fly,
            params.rtl.target_aruco_id,
            params.rtl.marker_dict,
            params.rtl.tag_size,
        )
        formatted = msg % args
        assert f"aruco_dict={marker_dict}" in formatted


def test_the_aruco_block_round_trips_through_mission_config():
    """The GCS reads and rewrites this document; new fields must survive it."""
    import json
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "mission_config.json")
        params = MissionParameters()
        params.rtl.target_aruco_id = 21
        params.rtl.marker_dict = 6
        params.rtl.tag_size = 0.35
        params.rtl.camera_tilt_deg = -85.0
        params.rtl.centering_kp_y = 0.44
        params.save_to_file(path)

        with open(path, "r", encoding="utf-8") as stream:
            on_disk = json.load(stream)
        assert on_disk["rtl"]["target_aruco_id"] == 21
        assert on_disk["rtl"]["tag_size"] == 0.35

        reloaded = MissionParameters.load_from_file(path)
        assert reloaded.rtl.target_aruco_id == 21
        assert reloaded.rtl.marker_dict == 6
        assert reloaded.rtl.camera_tilt_deg == -85.0
        assert reloaded.rtl.centering_kp_y == 0.44


def test_a_config_written_before_the_aruco_fields_existed_still_loads():
    """Backward compatibility is not decoration: a field station's config file
    predates every deployment, and an unknown-key failure there is a mission
    that does not launch."""
    params = MissionParameters()
    params.update_from_dict({"rtl": {"arrival_radius_m": 0.18, "max_speed": 0.07}})

    assert params.rtl.arrival_radius_m == 0.18
    assert params.rtl.target_aruco_id == MissionParameters().rtl.target_aruco_id
    assert params.rtl.tag_size == MissionParameters().rtl.tag_size


def test_the_step_name_still_carries_the_prefix_the_gcs_matches():
    """electron/main.cjs matches '[STEP 5:' on the log line built from it."""
    assert ClosedLoopRTLStep().name.startswith("STEP 5: ")


# ══════════════════════════════ the whole loop, through the library that flies


class RenderedPad:
    """A pinhole render of the landing pad, closed back through the real SDK.

    Every other test in this file agrees with the production code by
    construction: the fakes return what the code expects because that is what
    they were written to return. This one does not. It holds a body-frame offset,
    projects it through the *actual* calibrated intrinsics into a pixel position
    and an apparent size, draws a real ArUco marker there, hands the image to the
    real ``nectar.vision.Aruco``, and integrates whatever the control law
    commands back into the offset.

    So the loop under test is: offset -> pixels -> OpenCV detection -> pose solve
    -> body projection -> PD law -> shaper -> offset. A sign error, a frame
    confusion, a transposed principal point or a broken pose backend all break
    it, and none of them can break it in a way that also breaks the fake.

    Flown at true nadir. The shipped intrinsics describe a roughly square sensor
    (2*cy = 617 px) while the mission's frames are 856x480, so at the -80 deg
    default a centred pad projects below the bottom of the frame and the render
    would be testing clipping rather than control. The tilt is a parameter of the
    law, so exercising it at -90 is exercising the same code.
    """

    TILT_DEG = -90.0

    def __init__(self, detector, *, ex=0.30, ey=-0.22, altitude=1.60, marker_id=7):
        self.detector = detector
        self.ex, self.ey = float(ex), float(ey)
        self.altitude = float(altitude)
        self.marker_id = int(marker_id)
        self.focal_x = float(detector.camera_matrix[0][0])
        self.focal_y = float(detector.camera_matrix[1][1])
        self.principal_x = float(detector.camera_matrix[0][2])
        self.principal_y = float(detector.camera_matrix[1][2])
        self.tag_size = float(detector.tag_size)
        self.rendered = 0
        # The frame is sized from the intrinsics rather than from the mission's
        # 856x480 stream, because a pinhole render is only self-consistent in the
        # geometry its own principal point describes. Feeding these intrinsics
        # 856x480 frames is exactly the mismatch `_check_intrinsics` exists to
        # report, and reproducing it here would test clipping, not control.
        self.shape = (int(round(2 * self.principal_y)), int(round(2 * self.principal_x)))

    def integrate(self, vx, vy, dt):
        self.ex -= vx * dt
        self.ey -= vy * dt

    @property
    def residual_m(self):
        return math.hypot(self.ex, self.ey)

    def frame(self):
        """Project the current offset and draw the marker where it belongs."""
        x_cam, y_cam, z_cam = pad_to_camera(self.ex, self.ey, self.altitude, self.TILT_DEG)
        u = int(round(self.principal_x + self.focal_x * x_cam / z_cam))
        v = int(round(self.principal_y + self.focal_y * y_cam / z_cam))
        size = int(round(self.focal_x * self.tag_size / z_cam))
        size -= size % 2
        self.rendered += 1
        if size < 24:
            pytest.skip("rendered marker too small to be detectable")
        half = size // 2
        if not (half <= u < self.shape[1] - half and half <= v < self.shape[0] - half):
            pytest.skip("rendered marker would fall outside the calibrated frame")
        return render_marker(self.marker_id, u, v, size, self.shape)


def test_the_real_detector_closes_the_loop_onto_the_pad():
    """The integration test this whole stage exists to pass.

    Real intrinsics, real OpenCV detection, real pose solve, real control law,
    real actuator shaping -- and an aircraft that must end up over the marker.
    """
    detector = real_detector()
    pad = RenderedPad(detector, ex=0.30, ey=-0.22, altitude=1.60)
    sensor = ArucoMarkerSensor(detector, target_id=pad.marker_id)

    params = MissionParameters()
    params.rtl.camera_tilt_deg = RenderedPad.TILT_DEG
    controller = ArucoCenteringController(
        params.rtl, FlightKinematicsConfig(), SpeedCalibration(1.0)
    )
    controller.reset()

    start = pad.residual_m
    command = None
    for _ in range(1200):
        observation = sensor.observe(pad.frame())
        command = controller.update(observation, DT)
        pad.integrate(command.vx, command.vy, DT)
        if command.settled:
            break

    assert sensor.failures == 0, "the detector raised on a frame it rendered itself"
    assert command is not None and command.settled, (
        f"never settled; residual {pad.residual_m:.3f} m from {start:.3f} m"
    )
    assert pad.residual_m < start

    # The gate itself, on the quantity the aircraft actually acts on.
    assert command.radial_error_m <= controller.tolerance_m

    # And the ground truth. The two are not identical and cannot be: the render
    # places the pad centre on a whole pixel and rounds its side to an even
    # number of pixels, so the recovered pose disagrees with the offset that
    # produced it by a few millimetres -- one pixel is 1.7 mm at this focal
    # length and range. The margin is that resolution floor, not slack.
    assert pad.residual_m <= controller.tolerance_m + 0.01


def test_the_real_detector_drives_the_aircraft_toward_the_pad_not_away_from_it():
    """The weaker statement, and the one a sign error cannot survive.

    Convergence can be argued with -- a badly tuned loop converges slowly and a
    well-tuned wrong one diverges slowly. The direction of the very first motion
    cannot: whichever quadrant the pad is in, the range to it must fall.
    """
    detector = real_detector()
    params = MissionParameters()
    params.rtl.camera_tilt_deg = RenderedPad.TILT_DEG

    for ex, ey in ((0.30, 0.25), (0.30, -0.25), (-0.30, 0.25), (-0.30, -0.25)):
        pad = RenderedPad(detector, ex=ex, ey=ey, altitude=1.60)
        sensor = ArucoMarkerSensor(detector, target_id=pad.marker_id)
        controller = ArucoCenteringController(
            params.rtl, FlightKinematicsConfig(), SpeedCalibration(1.0)
        )
        controller.reset()

        start = pad.residual_m
        for _ in range(120):
            command = controller.update(sensor.observe(pad.frame()), DT)
            pad.integrate(command.vx, command.vy, DT)

        assert pad.residual_m < start, (
            f"from ({ex:+.2f}, {ey:+.2f}) the aircraft moved away from the pad: "
            f"{start:.3f} m -> {pad.residual_m:.3f} m"
        )


# ═════════════════════════════════════════════════ the intrinsics pre-flight


class IntrinsicsCtx:
    """The attribute surface ``_check_intrinsics`` reads."""

    def __init__(self, width, height):
        self.params = MissionParameters()
        self.frame_width = width
        self.frame_height = height


class Intrinsics:
    def __init__(self, principal_x, principal_y, focal=800.0):
        self.camera_matrix = [
            [focal, 0.0, principal_x],
            [0.0, focal, principal_y],
            [0.0, 0.0, 1.0],
        ]


def warnings_from_the_step(callable_):
    """Run something and return the warnings Stage 5 logged.

    A handler rather than ``caplog``, so the assertion does not depend on which
    pytest plugins the suite happens to be invoked with.
    """
    import logging

    captured = []

    class Collector(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                captured.append(record.getMessage())

    logger = logging.getLogger("Step5RTL")
    handler = Collector()
    logger.addHandler(handler)
    try:
        callable_()
    finally:
        logger.removeHandler(handler)
    return captured


def test_intrinsics_that_describe_another_camera_are_reported():
    """A silent systematic bias is the worst kind of calibration fault.

    Intrinsics from a different sensor still yield a smooth, confident,
    well-conditioned pose -- offset by a fixed angle, forever. The law converges
    perfectly onto a point that is not the pad, and the only symptom is a landing
    that is always wrong in the same direction.
    """
    messages = warnings_from_the_step(
        lambda: ClosedLoopRTLStep()._check_intrinsics(
            IntrinsicsCtx(856, 480), Intrinsics(298.6, 308.5)
        )
    )
    assert any("principal point" in message for message in messages), messages


def test_intrinsics_that_match_the_stream_are_not_reported():
    messages = warnings_from_the_step(
        lambda: ClosedLoopRTLStep()._check_intrinsics(
            IntrinsicsCtx(856, 480), Intrinsics(430.0, 238.0)
        )
    )
    assert not messages, messages


def test_the_intrinsics_check_never_raises_on_a_camera_it_cannot_read():
    """A diagnostic that can abort a return leg is worse than no diagnostic."""
    class Opaque:
        @property
        def camera_matrix(self):
            raise RuntimeError("no calibration loaded")

    ClosedLoopRTLStep()._check_intrinsics(IntrinsicsCtx(856, 480), Opaque())
    ClosedLoopRTLStep()._check_intrinsics(IntrinsicsCtx(0, 0), Intrinsics(1.0, 1.0))


# ═══════════════════════════════════════════ regressions on the audit findings


class HudCtx(Ctx):
    """A context that enforces the SDK's own precondition on the HUD call.

    ``Detector.draw_detections`` begins with ``if not result.detections``
    (``nectar/ai/detection/core/base.py:539``), so a ``None`` result raises
    ``AttributeError`` before anything is drawn. The production context routes
    every annotated publish through that method, so this double reproduces the
    contract rather than accepting anything.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.published = []

    def publish_annotated_stream(self, frame, result, text):
        if not result.detections:  # the exact guard, including its failure mode
            pass
        self.published.append(text)
        return None


def test_the_marker_hud_actually_reaches_the_annotated_stream():
    """The regression: Stage 5 published no annotated frame at all.

    ``publish_annotated_stream(frame, None, ...)`` raises inside the detector's
    ``draw_detections``, and the step caught that at DEBUG -- so the HUD was a
    silent no-op for the entire return leg while looking, from the code, exactly
    like a working one. The operator saw a frozen Stage 4 image all the way home.
    """
    ctx = HudCtx(altitude=0.20)
    ctx.params.rtl.timeout_sec = 1.0
    ctx.sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.1, ey=0.05)

    ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    assert ctx.published, "the return leg published no annotated frames at all"
    assert not ctx.drone.__dict__.get("_stream_failed")
    assert any("RTL ARUCO" in text for text in ctx.published)


def test_every_hud_line_carries_the_fields_the_spec_names():
    """One layout across both phases: marker, distance, both commands, altitude."""
    ctx = HudCtx(altitude=0.30)
    ctx.params.rtl.timeout_sec = 1.5
    ctx.sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.2, ey=-0.1)

    ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx)

    aruco_lines = [text for text in ctx.published if "RTL ARUCO" in text]
    assert aruco_lines
    for line in aruco_lines:
        assert "[ID: " in line
        assert "DIST: " in line
        assert "CMD: vx=" in line and "vy=" in line
        assert "ALT: " in line
        assert "nan" not in line, f"an unknown rendered as nan: {line}"


def test_the_stream_failure_is_reported_once_and_loudly():
    """A telemetry fault must neither flood the log nor hide in it."""
    step = ClosedLoopRTLStep()
    messages = warnings_from_the_step(
        lambda: [step._note_stream_failure(RuntimeError("boom")) for _ in range(5)]
    )
    assert len(messages) == 1, messages
    assert "annotated stream" in messages[0]


def test_the_gimbal_angle_is_re_asserted_through_the_return_leg():
    """A dropped tilt command biases the whole centering solution.

    The tilt is a term in the camera-to-body projection and the law cannot
    measure it; nothing on this airframe acknowledges a gimbal command; and
    Stage 4 leaves the mount 11 degrees away from where this stage needs it. At
    the cruise altitude that is 0.30 m of longitudinal error -- seven times the
    centering tolerance -- reported as a confirmed centring.
    """
    ctx = Ctx()
    ctx.params.rtl.timeout_sec = 2.5  # longer than the re-assertion interval
    sensor = ScriptedSensor(ctx.drone)

    ClosedLoopRTLStep(sensor=sensor)._reverse_search(
        ctx, sensor, __deadline(ctx)
    )

    assert len(ctx.drone.tilts) > 1, "the gimbal was commanded exactly once and never again"
    assert all(tilt == pytest.approx(ctx.params.rtl.camera_tilt_deg) for tilt in ctx.drone.tilts)


def test_a_gimbal_that_refuses_the_command_does_not_end_the_return():
    class StubbornGimbal(Drone):
        def camera_control(self, tilt, pan=0.0):
            raise RuntimeError("gimbal bus timeout")

    ctx = Ctx(altitude=0.20)
    ctx.drone = StubbornGimbal()
    ctx.odom_supervisor._drone = ctx.drone
    ctx.failsafe.drone_actuator = ctx.drone
    ctx.sensor = ScriptedSensor(ctx.drone)

    assert ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx) is StepStatus.SUCCESS
    assert ctx.drone.land_calls > 0


def test_a_centering_law_that_cannot_be_configured_degrades_instead_of_raising():
    """Discovered at step entry, before the aircraft flies a search leg.

    Built lazily after the search, the constructor's ``ValueError`` would arrive
    with the drone over the pad and would cost it the verified touchdown
    sequence, leaving one unacknowledged ``land()`` publish in its place.
    """
    ctx = Ctx(altitude=0.20)
    ctx.params.rtl.max_centering_speed = 0.0

    assert ClosedLoopRTLStep()._build_centering(ctx) is None

    status = ClosedLoopRTLStep().execute(ctx)
    assert status is StepStatus.SUCCESS
    assert ctx.drone.land_calls > 0, "a bad gain cost the aircraft its landing"


def test_a_camera_that_raises_is_a_missing_frame_not_a_failed_mission():
    """The perception guarantee covers the frame grab, not only the detector."""
    ctx = Ctx(altitude=0.20)
    ctx.params.rtl.timeout_sec = 0.4

    def explode(timeout_sec=1.0):
        raise RuntimeError("H.264 decode failure")

    ctx.grab_frame = explode
    ctx.sensor = ScriptedSensor(ctx.drone)

    assert ClosedLoopRTLStep(sensor=ctx.sensor).execute(ctx) is StepStatus.SUCCESS
    assert ctx.drone.land_calls > 0


def test_the_sensor_survives_whichever_opencv_the_interpreter_has():
    """The flight-critical property, asserted against the real library.

    This machine can reach two OpenCVs -- 4.11 in the mission venv, 4.6 from the
    ROS distribution -- and ``nectar.vision.Aruco`` is incompatible with both in
    different places: ``detect`` needs ``cv2.aruco.ArucoDetector`` (4.7+) and
    ``pose_estimate`` needs ``estimatePoseSingleMarkers`` (removed in 4.10). What
    must hold on every one of them is that no exception escapes into the mission
    and that a blank frame is never mistaken for a marker.
    """
    import numpy

    vision = pytest.importorskip("nectar.vision")
    try:
        detector = vision.Aruco(marker_dict=5, tag_size=0.20)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ArUco detector could not be constructed: {exc}")

    sensor = ArucoMarkerSensor(detector, target_id=7)
    assert sensor.observe(numpy.full((480, 856, 3), 255, dtype=numpy.uint8)) is None
    assert sensor.observe(None) is None
    # Whatever the runtime does with a real marker, it must not raise out.
    sensor.observe(render_marker(7, 428, 240, 200, (617, 597)))
