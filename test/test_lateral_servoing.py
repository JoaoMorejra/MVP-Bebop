"""The Stage 3 servo of the lateral flight pattern: CENTERING -> LOCKED (+ REACQUIRING).

The drone does not advance on the target. It nulls the horizontal pixel error
with the body-frame lateral channel alone, holds the gimbal at whatever tilt it
was handed, and freezes -- latched -- once the target has been measured inside
the centring gate for ``vision.alignment_dwell_cycles`` frames with the airframe
at rest. Every property pinned here is a structural guarantee of the law, not a
tuning outcome: ``vx`` is a literal zero, the tilt is a pass-through, predicted
frames earn no credit, and a lost target is recovered by creeping laterally
toward the side it left from.
"""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.visual_servoing import (
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)

DT = 1.0 / 16.0
FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
CENTER_X, CENTER_Y = WIDTH / 2.0, HEIGHT / 2.0
TILT_DEG = -20.0
ALTITUDE_M = 1.23
DWELL = VisionConfig().alignment_dwell_cycles


def build_controller(**vision_overrides):
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=VisionConfig(**vision_overrides),
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def observe(controller, px_x, *, measured=True, at_rest=True, tilt_deg=TILT_DEG):
    return controller.compute(
        (px_x, CENTER_Y),
        FRAME,
        tilt_deg,
        ALTITUDE_M,
        DT,
        measured=measured,
        airframe_at_rest=at_rest,
    )


def lock(controller):
    command = None
    for _ in range(DWELL):
        command = observe(controller, CENTER_X)
    assert command.locked, "the fixture failed to lock"
    return command


def every_phase(controller):
    """A run that visits CENTERING, REACQUIRING and LOCKED, with distinct tilts.

    Yields ``(command, tilt_passed_in)`` so a test can check both the forward
    channel and the tilt pass-through on the same trace.
    """
    tilt = TILT_DEG
    for _ in range(10):
        tilt -= 0.25
        yield observe(controller, 700.0, at_rest=False, tilt_deg=tilt), tilt
    for _ in range(int(3.0 / DT)):
        tilt -= 0.25
        yield controller.note_target_lost(tilt, DT), tilt
    for _ in range(VisionConfig().reacquire_confirm_frames + DWELL + 1):
        tilt -= 0.25
        yield observe(controller, CENTER_X, tilt_deg=tilt), tilt
    for _ in range(10):
        tilt -= 0.25
        yield observe(controller, 820.0, tilt_deg=tilt), tilt
    tilt -= 0.25
    yield controller.note_target_lost(tilt, DT), tilt


# ------------------------------------------------------------ structural law


def test_vx_is_exactly_zero_in_every_phase():
    controller = build_controller()
    trace = list(every_phase(controller))

    phases = {command.phase for command, _ in trace}
    assert phases == {TrackingPhase.CENTERING, TrackingPhase.REACQUIRING, TrackingPhase.LOCKED}
    for command, _ in trace:
        assert command.vx == 0.0
        assert math.copysign(1.0, command.vx) == 1.0, "vx must be a literal +0.0"


def test_the_tilt_is_never_changed():
    """D2: Stage 3 does not move the gimbal; the tilt is a pass-through."""
    controller = build_controller()
    for command, tilt in every_phase(controller):
        assert command.tilt_deg == tilt, f"{command.phase.value} changed the tilt"


def test_lateral_command_opposes_the_horizontal_error():
    controller = build_controller()
    for _ in range(5):
        right = observe(controller, 700.0, at_rest=False)
    controller.reset()
    for _ in range(5):
        left = observe(controller, 150.0, at_rest=False)

    # FLU body frame: +vy is left. A target right of centre needs motion right.
    assert right.vy < 0.0
    assert left.vy > 0.0


# ------------------------------------------------------------------ the lock


def test_the_lock_needs_dwell_cycles_inside_the_gate():
    controller = build_controller()
    for cycle in range(1, DWELL + 1):
        command = observe(controller, CENTER_X)
        assert command.laterally_centered
        assert command.locked is (cycle == DWELL), f"lock state wrong at cycle {cycle}"
    assert controller.locked
    assert controller.phase is TrackingPhase.LOCKED
    assert command.phase is TrackingPhase.LOCKED


def test_predicted_frames_earn_no_credit():
    """Review Focus 4: a coasted tracker estimate must never freeze the airframe."""
    controller = build_controller()
    for _ in range(DWELL * 20):
        command = observe(controller, CENTER_X, measured=False)
        assert not command.locked
    assert controller.phase is TrackingPhase.CENTERING
    assert not controller.locked


def test_the_lock_waits_for_the_airframe_to_be_at_rest():
    controller = build_controller()
    for _ in range(DWELL + 10):
        command = observe(controller, CENTER_X, at_rest=False)
        assert not command.locked
        assert command.laterally_centered

    command = observe(controller, CENTER_X, at_rest=True)
    assert command.locked
    assert command.phase is TrackingPhase.LOCKED


def test_the_lock_is_latched_and_silences_the_loop():
    """No reversion, and no pulse banked by the sigma-delta shaper before the lock.

    The target sits inside the gate but off-centre, so the lateral demand is
    below the actuator floor and the shaper accumulates a residual while the
    credit builds. A loop that zeroed only its output would discharge that
    residual as a pulse after the freeze.
    """
    controller = build_controller()
    inside = CENTER_X + 20.0
    command = None
    for _ in range(DWELL):
        command = observe(controller, inside)
    assert command.locked

    for px_x in [820.0, 30.0, inside, CENTER_X] * 5:
        command = observe(controller, px_x)
        assert command.vy == 0.0
        assert command.vx == 0.0
        assert command.locked is True
        assert command.phase is TrackingPhase.LOCKED

    lost = controller.note_target_lost(TILT_DEG, DT)
    assert lost.locked and lost.phase is TrackingPhase.LOCKED
    assert lost.vy == 0.0 and lost.vx == 0.0


def test_a_jittered_frame_decays_credit_instead_of_resetting_it():
    controller = build_controller(alignment_dwell_cycles=4)
    for _ in range(3):
        assert not observe(controller, CENTER_X).locked
    jittered = observe(controller, 820.0)
    assert not jittered.laterally_centered and not jittered.locked

    # 3 - 1 = 2 credits left: two more centred frames lock. A reset would need four.
    assert not observe(controller, CENTER_X).locked
    assert observe(controller, CENTER_X).locked


def test_a_predicted_frame_costs_credit_outside_the_gate_and_earns_none_inside():
    """Out-of-gate frames decrement whatever their source; predicted in-gate frames are neutral.

    Two measured centred frames bank 2 of 4 credits. A predicted frame outside
    the gate takes one away, and any number of predicted frames inside it leave
    the remaining credit untouched, so exactly three measured frames then lock.
    """
    controller = build_controller(alignment_dwell_cycles=4)
    for _ in range(2):
        assert not observe(controller, CENTER_X).locked

    outside = observe(controller, 820.0, measured=False)
    assert not outside.laterally_centered and not outside.locked
    for _ in range(20):
        predicted = observe(controller, CENTER_X, measured=False)
        assert predicted.laterally_centered and not predicted.locked

    assert not observe(controller, CENTER_X).locked
    assert not observe(controller, CENTER_X).locked
    assert observe(controller, CENTER_X).locked


# ---------------------------------------------------------- reacquisition


@pytest.mark.parametrize(
    "last_seen_px, direction",
    [(800.0, -1.0), (50.0, 1.0), (CENTER_X, 0.0)],
    ids=["exited-right", "exited-left", "lost-at-centre"],
)
def test_reacquisition_creeps_toward_the_last_seen_side(last_seen_px, direction):
    """Review Focus 3: the target left the frame sideways; follow it, never forward."""
    vision = VisionConfig()
    floor = LateralPIDConfig().min_effective_velocity
    controller = build_controller()
    observe(controller, last_seen_px, at_rest=False)

    creep = []
    elapsed = 0.0
    tilt = TILT_DEG - 3.0
    while elapsed + DT <= vision.reacquire_reverse_sec:
        command = controller.note_target_lost(tilt, DT)
        elapsed += DT
        assert command.phase is TrackingPhase.REACQUIRING
        assert command.vx == 0.0
        assert command.tilt_deg == tilt
        creep.append(command.vy)

    if direction == 0.0:
        assert all(vy == 0.0 for vy in creep), "a loss at the centre authorises no motion"
    else:
        assert all(direction * vy >= 0.0 for vy in creep), "the creep reversed direction"
        assert all(abs(vy) >= floor - 1e-9 for vy in creep if vy != 0.0)
        mean = sum(creep) / len(creep)
        assert mean == pytest.approx(direction * abs(vision.reacquire_reverse_speed), abs=0.015)

    for _ in range(int(1.0 / DT)):
        late = controller.note_target_lost(tilt, DT)
        assert late.vy == 0.0, "the creep must stop once its window has elapsed"
        assert late.vx == 0.0


def test_reacquisition_exhausts_and_reports_it():
    vision = VisionConfig()
    controller = build_controller()
    observe(controller, 800.0, at_rest=False)

    elapsed = 0.0
    command = None
    while elapsed < vision.reacquire_timeout_sec + 1.0:
        command = controller.note_target_lost(TILT_DEG, DT)
        elapsed += DT

    assert command.recovery_exhausted is True
    assert command.vy == 0.0
    assert command.vx == 0.0
    assert command.phase is TrackingPhase.REACQUIRING


def test_predicted_frames_do_not_postpone_the_exhaustion_of_reacquisition():
    """Unconfirmed observations keep the reacquisition clock running to its timeout."""
    vision = VisionConfig()
    controller = build_controller()
    observe(controller, 800.0, at_rest=False)
    controller.note_target_lost(TILT_DEG, DT)
    assert controller.phase is TrackingPhase.REACQUIRING

    elapsed = DT
    trace = []
    while elapsed < vision.reacquire_timeout_sec + 1.0:
        trace.append((elapsed, observe(controller, CENTER_X, measured=False)))
        elapsed += DT

    early = [cmd for t, cmd in trace if t < vision.reacquire_timeout_sec - DT]
    late = [cmd for t, cmd in trace if t > vision.reacquire_timeout_sec + DT]
    assert early and not any(cmd.recovery_exhausted for cmd in early)
    assert late and all(cmd.recovery_exhausted for cmd in late)
    for cmd in late:
        assert cmd.phase is TrackingPhase.REACQUIRING
        assert cmd.vy == 0.0 and cmd.vx == 0.0


def test_reset_returns_to_centering():
    controller = build_controller()
    lock(controller)

    controller.reset()

    assert controller.phase is TrackingPhase.CENTERING
    assert not controller.locked
    command = observe(controller, CENTER_X)
    assert not command.locked, "reset must also clear the centring credit"
    assert command.phase is TrackingPhase.CENTERING


def test_alternating_confidences_do_not_flip_the_servoed_target():
    """Two accident boxes trade the higher confidence every frame for 60 frames.

    Per-frame ``max(confidence)`` would swing the servo between them; the
    persistent selector keeps the first one and the centre handed to the law
    neither changes side nor jumps.
    """
    import numpy as np
    from nectar.ai.detection.core.types import Detection

    from mvp_mission_bebop.estimation.target_selector import TargetSelector
    from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    def box(cx, conf):
        return Detection(xyxy=np.array([cx - 40, 200, cx + 40, 280], dtype=float), confidence=conf, class_id=3, class_name="motorcycle")

    selector = TargetSelector(VisionConfig())
    tracker = ConstantVelocityTracker()
    centres = []
    for k in range(60):
        high, low = (0.80, 0.60) if k % 2 else (0.60, 0.80)
        centre, measured = VisualServoingStep._resolve_target(
            [box(250.0, high), box(620.0, low)], tracker, 1.0 / 15.0, (856, 480), selector=selector
        )
        assert measured
        centres.append(centre[0])
    sides = [c < 428.0 for c in centres]
    assert sum(1 for a, b in zip(sides, sides[1:]) if a != b) == 0
    assert max(abs(b - a) for a, b in zip(centres, centres[1:])) <= 15.0


def test_victim_detections_are_never_centered() -> None:
    """Victim classes (person, pessoa, vitima) must never be centered by the servo."""
    import numpy as np
    from nectar.ai.detection.core.types import Detection
    from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    tracker = ConstantVelocityTracker()
    victim_det = Detection(
        xyxy=np.array([200, 200, 280, 280], dtype=float),
        confidence=0.92,
        class_id=0,
        class_name="person",
    )
    centre, measured = VisualServoingStep._resolve_target(
        [victim_det], tracker, dt=0.1, frame_dimensions=(856, 480), selector=None
    )
    assert centre is None
    assert not measured


def test_accident_is_strictly_prioritized_over_victim() -> None:
    """Accident classes (motorcycle, bicycle, acidente) outrank any victim detection."""
    import numpy as np
    from nectar.ai.detection.core.types import Detection
    from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    tracker = ConstantVelocityTracker()
    victim_det = Detection(
        xyxy=np.array([100, 200, 180, 280], dtype=float),
        confidence=0.95,
        class_id=0,
        class_name="person",
    )
    accident_det = Detection(
        xyxy=np.array([500, 200, 580, 280], dtype=float),
        confidence=0.45,
        class_id=3,
        class_name="motorcycle",
    )
    centre, measured = VisualServoingStep._resolve_target(
        [victim_det, accident_det], tracker, dt=0.1, frame_dimensions=(856, 480), selector=None
    )
    assert measured
    assert centre is not None
    # Must follow the accident at x=540, not the victim at x=140
    assert centre[0] == pytest.approx(540.0, abs=15.0)


def test_visual_servoing_slew_rate_limits_demand_jerk() -> None:
    """Step change in target centroid is ramped smoothly, eliminating sudden kicks."""
    from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
    from mvp_mission_bebop.parameters import (
        FlightKinematicsConfig,
        GimbalConstraintsConfig,
        GimbalPIDConfig,
        LateralPIDConfig,
        VisionConfig,
    )

    ctrl = VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(kp=0.18, output_limits=(-0.04, 0.04)),
        vision_cfg=VisionConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
    )
    dt = 0.05
    # First cycle with large offset (err_x = 200 px)
    cmd1 = ctrl.compute(
        target_center=(628.0, 240.0),
        frame_dimensions=(856, 480),
        current_tilt_deg=-18.0,
        relative_altitude_m=1.0,
        dt=dt,
        measured=True,
        airframe_at_rest=True,
    )
    # The max demand change in 0.05s at 0.15/s slew is 0.0075.
    # It must not jump immediately to the -0.04 limit!
    assert abs(ctrl._last_vy_demand) <= 0.15 * dt + 1e-6
    assert abs(cmd1.vy) <= 0.04


@pytest.mark.parametrize("travel", [0.04, 0.15])
def test_the_lateral_transit_flies_at_the_single_travel_speed(travel) -> None:
    """The leftward approach saturates at ``forward_cruise_velocity``, as the return does."""
    from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
    from mvp_mission_bebop.parameters import (
        FlightKinematicsConfig,
        GimbalConstraintsConfig,
        GimbalPIDConfig,
        LateralPIDConfig,
        VisionConfig,
    )

    ctrl = VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(kp=0.35, output_limits=(-0.04, 0.04)),
        vision_cfg=VisionConfig(),
        kinematics_cfg=FlightKinematicsConfig(forward_cruise_velocity=travel),
    )
    commands = [
        ctrl.compute(
            target_center=(90.0, 340.0),
            frame_dimensions=(856, 480),
            current_tilt_deg=-28.0,
            relative_altitude_m=1.2,
            dt=1.0 / 15.0,
            measured=True,
            airframe_at_rest=False,
        ).vy
        for _ in range(90)
    ]
    assert max(commands) == pytest.approx(travel, abs=0.0051)
    assert all(vy >= 0.0 for vy in commands)
