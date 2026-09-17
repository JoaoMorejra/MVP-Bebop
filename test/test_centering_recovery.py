"""Regression tests for the Stage 3 centering deadlock observed in flight.

On 2026-09-12 the airframe confirmed a bicycle at 0.79 confidence, announced the
approach, entered visual servoing, and then hovered at ``vx = 0`` until the
sixty-second stage deadline expired. The gimbal dithered against the shallow
stop of its centering band -- the flight log shows tilt returning to exactly
-15.0 deg, the band's upper bound, three times in under a second -- while the
drone never advanced a metre.

The tests here reconstruct that geometry rather than asserting on the fix's
internals, so they fail against the pre-fix control law for the reason the
aircraft failed and pass against the current one for the reason it now works.

The closed-loop harness matters. A test that feeds a *fixed* pixel coordinate
cannot reproduce the deadlock at all, because the deadlock is a fixed point of
the coupled gimbal/pixel dynamics: the gimbal moves, the target's bearing
relative to the optical axis moves with it, and the pair settles against the
band stop with a residual vertical error that no actuator in the phase can
reduce. So ``target_pixel`` below re-derives the observation from the geometry
every cycle, exactly as the camera would.
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

#: The mission's real cadence and capture size.
DT = 1.0 / 16.0
FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
CENTER_X, CENTER_Y = WIDTH / 2.0, HEIGHT / 2.0

#: Conditions read directly from the failing flight log.
FLIGHT_ALTITUDE_M = 1.23
SEARCH_TILT_DEG = -20.0


def build_controller(**vision_overrides):
    vision = VisionConfig(**vision_overrides)
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=vision,
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def target_pixel(ground_range_m, tilt_deg, altitude_m, lateral_m=0.0):
    """Where a ground target at a given range appears, for a given gimbal tilt.

    The exact inverse of :func:`geometry.project_to_ground`, so the harness and
    the controller agree on the projection without sharing an implementation.
    """
    depression = math.atan2(altitude_m, ground_range_m)
    theta_y = depression - math.radians(-tilt_deg)
    theta_x = math.atan2(lateral_m, math.hypot(ground_range_m, altitude_m))

    px_y = CENTER_Y + CENTER_Y * math.tan(theta_y) / math.tan(math.radians(25.0))
    px_x = CENTER_X + CENTER_X * math.tan(theta_x) / math.tan(math.radians(40.0))
    return px_x, px_y


def fly(controller, ground_range_m, *, seconds, altitude_m=FLIGHT_ALTITUDE_M, lateral_m=0.0):
    """Run the closed gimbal/pixel loop against a stationary ground target."""
    tilt = SEARCH_TILT_DEG
    history = []
    for _ in range(int(seconds / DT)):
        command = controller.compute(
            target_pixel(ground_range_m, tilt, altitude_m, lateral_m),
            FRAME,
            tilt,
            altitude_m,
            DT,
        )
        tilt = command.tilt_deg
        history.append(command)
    return history


# --------------------------------------------------- the deadlock, reproduced


@pytest.mark.parametrize("ground_range_m", [8.0, 10.0, 12.0, 15.0])
def test_forward_cruise_engages_against_a_distant_target(ground_range_m):
    """The flight failure: a target beyond the band's reach froze the approach.

    With the gimbal railed at the shallow stop of the centering band, a target
    at eight metres sits a fixed 58 px above the optical axis at this altitude --
    permanently outside the 52.5 px vertical gate the old law required, and
    unreachable, because the only thing that reduces that error is the forward
    motion the gate was withholding.
    """
    controller = build_controller()
    history = fly(controller, ground_range_m, seconds=12.0)

    assert controller.phase is not TrackingPhase.CENTERING, (
        f"still centering after 12 s against a {ground_range_m:.0f} m target"
    )
    assert any(command.vx > 0.0 for command in history), "forward cruise never engaged"


def test_the_vertical_error_that_deadlocked_the_flight_is_still_present():
    """Guards the premise of the test above.

    If a future change to the band or the field of view made the old vertical
    gate satisfiable at this range, the test above would pass for a reason
    unrelated to the fix and would stop protecting anything.
    """
    controller = build_controller()
    history = fly(controller, 8.0, seconds=4.0)

    old_gate_px = VisionConfig().optical_center_tolerance_px * 1.5
    residual = [abs(c.pixel_error) for c in history[-16:]]
    assert min(residual) > old_gate_px, (
        "the flight's vertical residual no longer exceeds the old gate; "
        "this test's premise has expired"
    )


def test_the_pointing_solution_demands_more_than_the_band_allows():
    """The -15.0 deg readings in the log were the band stop, not a coincidence.

    Asserted on the geometry rather than on the controller, because the fixed
    controller now leaves the phase long before the gimbal has time to walk to
    its stop. The property that made the old law deadlock is a property of the
    scene, and it is still true: at the flown altitude a target eight metres out
    subtends a depression the centering band cannot point at. The old law then
    gated the phase exit on nulling an error it had just forbidden itself to
    null.
    """
    required_tilt = -math.degrees(math.atan2(FLIGHT_ALTITUDE_M, 8.0))
    upper_stop = SEARCH_TILT_DEG + GimbalConstraintsConfig().centering_tilt_band_up_deg

    assert required_tilt > upper_stop, (
        f"pointing at the target needs {required_tilt:.1f} deg, band stops at "
        f"{upper_stop:.1f} deg -- the premise of this suite has expired"
    )


# --------------------------------------------------- the acquisition dwell


def test_acquisition_is_bounded_by_time_not_by_pixel_error():
    """The dwell must not be satisfiable-or-not depending on where the target is.

    Every version of this phase that gated on centring was a hover trap: the
    phase commands no forward velocity, and forward velocity is what closes the
    range the vertical error depends on, so the drone was asked to null an error
    it had just forbidden itself the means to null. The dwell is now bounded by
    elapsed time and a persistent detection, and by nothing else.
    """
    vision = VisionConfig()

    for ground_range_m in (2.5, 5.0, 8.0, 12.0, 20.0):
        controller = build_controller()
        history = fly(controller, ground_range_m, seconds=vision.acquisition_timeout_sec + 1.0)

        handover = next(
            (i for i, c in enumerate(history) if c.phase is not TrackingPhase.CENTERING), None
        )
        assert handover is not None, f"never left acquisition at {ground_range_m:.0f} m"
        assert handover * DT <= vision.acquisition_timeout_sec + 2 * DT, (
            f"acquisition overran its window at {ground_range_m:.0f} m"
        )


def test_acquisition_hands_over_even_from_far_outside_the_corridor():
    """A badly off-axis target still commits to the approach.

    The corridor guard in the approach phase is what withholds forward motion
    while the bearing is wrong -- and it does so continuously, through the
    alignment gain. Refusing to leave acquisition as well would just be the same
    constraint applied twice, with a hover trap as the cost.
    """
    controller = build_controller()
    vision = VisionConfig()

    history = fly(controller, 6.0, seconds=vision.acquisition_timeout_sec + 2.0, lateral_m=6.0)

    assert any(c.phase is TrackingPhase.APPROACHING for c in history)
    approaching = [c for c in history if c.phase is TrackingPhase.APPROACHING]
    assert all(c.vx == 0.0 for c in approaching), (
        "the corridor guard must still hold the forward axis at zero"
    )


# ------------------------------------- continuous alignment, not a switch


def test_forward_speed_scales_continuously_with_cross_track_error():
    """Centring and advancing happen together, not one after the other.

    The approach used to gate forward motion on a boolean: full speed inside the
    corridor, hard zero outside it. Being 50 px off-axis at six metres is a
    bearing error of three degrees, which is a reason to ease off, not to stop.
    """
    controller = build_controller()
    vision = VisionConfig()
    # One cycle first: the tolerance is derived from the frame width, so it is
    # only resolved once the controller has seen a frame.
    fly(controller, 5.0, seconds=DT)
    tolerance = controller._tolerance_px
    corridor = tolerance * vision.approach_corridor_ratio

    gains = [controller._alignment_gain(offset) for offset in (0.0, tolerance, corridor - 1.0)]

    assert gains[0] == 1.0
    assert gains[1] == 1.0
    assert 0.0 < gains[2] < 1.0, "the taper must be graded, not a switch"
    assert gains[2] >= vision.min_alignment_gain
    assert controller._alignment_gain(corridor + 1.0) == 0.0
    assert gains == sorted(gains, reverse=True), "gain must fall monotonically with error"


def test_a_partially_aligned_approach_still_advances_while_centring():
    """The defining behaviour of the refactor, asserted directly."""
    controller = build_controller()
    vision = VisionConfig()
    tolerance = max(
        vision.optical_center_tolerance_px, vision.optical_center_tolerance_ratio * WIDTH
    )
    offset = tolerance * (1.0 + vision.approach_corridor_ratio) / 2.0

    tilt = SEARCH_TILT_DEG
    advancing = []
    lateral = []
    for _ in range(int(12.0 / DT)):
        _, px_y = target_pixel(5.0, tilt, FLIGHT_ALTITUDE_M)
        command = controller.compute((CENTER_X + offset, px_y), FRAME, tilt, 1.23, DT)
        tilt = command.tilt_deg
        if command.phase is TrackingPhase.APPROACHING:
            advancing.append(command.vx)
            lateral.append(command.vy)

    assert advancing, "never reached the approach"
    assert max(advancing) > 0.0, "off-axis inside the corridor must still advance"
    assert 0.0 < min(c for c in advancing if c > 0.0)
    # The cross-track channel is duty-cycled through the sigma-delta shaper, so
    # individual cycles legitimately emit zero; what must hold is that the axis
    # is being driven across the window, in the direction that reduces the error.
    assert any(v != 0.0 for v in lateral), "the cross-track loop must run while advancing"
    assert all(v <= 0.0 for v in lateral), "a target right of centre must drive right"


def test_the_approach_never_falls_back_to_a_zero_velocity_phase():
    """Two phases that both command vx = 0 are a livelock, not a recovery."""
    controller = build_controller()
    history = fly(controller, 6.0, seconds=45.0, lateral_m=6.0)

    reverts = sum(
        1
        for previous, current in zip(history, history[1:])
        if previous.phase is TrackingPhase.APPROACHING
        and current.phase is TrackingPhase.CENTERING
    )
    assert reverts == 0, "the approach must never return to the acquisition phase"
    assert controller.phase is TrackingPhase.APPROACHING
    assert history[-1].vx == 0.0, "the corridor guard must survive"


def test_credit_survives_isolated_detector_jitter():
    """A single bad centroid used to discard every frame of credit before it."""
    controller = build_controller()
    vision = VisionConfig()

    tilt = SEARCH_TILT_DEG
    phases = []
    for index in range(int((vision.acquisition_timeout_sec + 1.0) / DT)):
        # Two clean frames for every jittered one, which is net progress.
        px_x, px_y = target_pixel(3.3, tilt, FLIGHT_ALTITUDE_M)
        if index % 3 == 2:
            px_y = CENTER_Y - 0.95 * CENTER_Y  # near the frame edge, but in frame
        command = controller.compute((px_x, px_y), FRAME, tilt, 1.23, DT)
        tilt = command.tilt_deg
        phases.append(command.phase)

    assert TrackingPhase.APPROACHING in phases, "jitter defeated the acquisition dwell"


# ------------------------------------------------- cross-track actuator floor


def test_lateral_commands_clear_the_driver_truncation_threshold():
    """``min_effective_velocity`` was declared and read by nothing.

    The Bebop driver quantizes to ``int8(v * 100)``. A demand of 0.029 -- what a
    35 px offset produces at the configured gain -- survives as two counts, below
    the airframe's response threshold, so the cross-track error stopped falling
    while still outside the tolerance that gated the phase exit.
    """
    controller = build_controller()
    floor = LateralPIDConfig().min_effective_velocity

    commands = []
    for _ in range(60):
        command = controller.compute((CENTER_X + 8.0, CENTER_Y), FRAME, -20.0, 1.23, DT)
        commands.append(command.vy)

    nonzero = [v for v in commands if v != 0.0]
    assert nonzero, "a sub-floor demand must still be delivered, as pulses"
    assert all(abs(v) >= floor - 1e-9 for v in nonzero), (
        "every emitted command must clear the driver's truncation threshold"
    )
    assert all(v < 0.0 for v in nonzero), "a target right of centre must drive right"


def test_shaped_lateral_mean_tracks_the_demand():
    """The floor is a duty cycle, not an amplitude: the mean must stay faithful."""
    controller = build_controller()

    commands = [
        controller.compute((CENTER_X + 8.0, CENTER_Y), FRAME, -20.0, 1.23, DT).vy
        for _ in range(400)
    ]

    mean = sum(commands) / len(commands)
    demand = -LateralPIDConfig().kp * (8.0 / CENTER_X)
    assert mean == pytest.approx(demand, abs=0.01)
    assert abs(mean) < LateralPIDConfig().min_effective_velocity, (
        "a floor applied as an amplitude would have pinned the mean at 0.06"
    )


# ---------------------------------------------------- the end-to-end promise


def test_the_stage_reaches_the_inspection_attitude_from_the_flight_s_conditions():
    """Closed-loop convergence from where the real flight stalled.

    The range shortens as the drone advances, so this exercises the whole
    coupled chain: centering, the range-braked approach, the gimbal walking down
    to the inspection attitude, and the arrival gate -- against the geometry
    that deadlocked.
    """
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG)
    controller = build_controller()
    altitude = FLIGHT_ALTITUDE_M
    ground_range = 8.0
    tilt = SEARCH_TILT_DEG
    travelled = []

    for _ in range(int(60.0 / DT)):
        command = controller.compute(
            target_pixel(max(ground_range, 1e-3), tilt, altitude), FRAME, tilt, altitude, DT
        )
        tilt = command.tilt_deg
        # Integrate the commanded along-track velocity: closing the range is
        # what the stage exists to do, so the test must let it happen.
        ground_range = max(0.0, ground_range - command.vx * DT)
        travelled.append(command.vx)
        if command.nadir_frozen:
            break

    # The optical axis at the inspection attitude meets the ground here, which
    # is where the target has to be for the gimbal to have arrived at that
    # angle. It is the stopping point, and it is a geometric consequence of the
    # tilt rather than a threshold chosen separately from it.
    standoff = altitude / math.tan(math.radians(abs(gimbal_cfg.nadir_tilt_deg)))

    assert controller.phase is TrackingPhase.NADIR
    assert tilt == pytest.approx(gimbal_cfg.nadir_tilt_deg, abs=0.5)
    assert ground_range == pytest.approx(standoff, abs=0.12), (
        f"stopped at {ground_range:.2f} m against a {standoff:.2f} m standoff"
    )
    assert travelled[-1] == 0.0, "the airframe must be at rest before inspection begins"


def test_the_approach_eases_onto_the_standoff_rather_than_braking_onto_it():
    """The last command before the freeze must be small, not a hard stop.

    Stage 4 opens by verifying motionlessness statistically before it triggers a
    14 MP capture, so velocity the approach leaves behind is time that
    verification spends waiting. The feedforward plans the arrival against a
    deceleration well inside the airframe's envelope precisely so the approach
    arrives slow instead of arriving fast and stopping hard.
    """
    controller = build_controller()
    altitude = FLIGHT_ALTITUDE_M
    ground_range = 8.0
    tilt = SEARCH_TILT_DEG
    peak = 0.0
    before_freeze = 0.0

    for _ in range(int(60.0 / DT)):
        command = controller.compute(
            target_pixel(max(ground_range, 1e-3), tilt, altitude), FRAME, tilt, altitude, DT
        )
        tilt = command.tilt_deg
        ground_range = max(0.0, ground_range - command.vx * DT)
        peak = max(peak, command.vx)
        if command.nadir_committed:
            break
        before_freeze = command.vx

    assert peak > 0.05, "the approach never got up to speed, so this proves nothing"
    assert before_freeze < peak * 0.5, (
        f"arrived at {before_freeze:.3f} against a {peak:.3f} cruise: the approach braked "
        f"onto the standoff instead of easing onto it"
    )
