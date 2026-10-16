"""The parameter sheet must govern the Stage 3 freeze decision.

The dwell that ends the stage used to be a module-level literal in
`mvp_mission_bebop.steps.tracking` (`_ALIGNMENT_DWELL_CYCLES = 4`); an operator
changing it in the GCS changed nothing. In the lateral flight pattern the dwell
lives in the controller and the centring gate is the optical tolerance, so both
are pinned here against the configuration they must follow.
"""

from __future__ import annotations

import inspect

from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.tracking import VisualServoingStep

FRAME = (856, 480)
CENTER_X, CENTER_Y = FRAME[0] / 2.0, FRAME[1] / 2.0
DT = 1.0 / 16.0


def build_controller(params: MissionParameters) -> VisualServoingController:
    return VisualServoingController(
        gimbal_config=params.gimbal,
        gimbal_pid_cfg=params.gimbal_pid,
        lateral_pid_cfg=params.lateral_pid,
        vision_cfg=params.vision,
        kinematics_cfg=params.kinematics,
        calibration=SpeedCalibration(1.0),
    )


def cycles_to_lock(params: MissionParameters, px_x: float = CENTER_X, limit: int = 100) -> int:
    controller = build_controller(params)
    for cycle in range(1, limit + 1):
        command = controller.compute((px_x, CENTER_Y), FRAME, -20.0, 1.2, DT)
        if command.locked:
            return cycle
    return -1


def test_the_lock_dwell_follows_the_configured_cycles():
    params = MissionParameters()
    for dwell in (2, 4, 9):
        params.vision.alignment_dwell_cycles = dwell
        assert cycles_to_lock(params) == dwell


def test_a_zero_dwell_is_clamped_to_one_measured_cycle():
    """A zero in the config cannot latch the freeze on an unmeasured frame."""
    params = MissionParameters()
    params.vision.alignment_dwell_cycles = 0
    assert cycles_to_lock(params) == 1

    controller = build_controller(params)
    predicted = controller.compute(
        (CENTER_X, CENTER_Y), FRAME, -20.0, 1.2, DT, measured=False
    )
    assert not predicted.locked


def test_the_centring_gate_follows_the_configured_tolerance():
    params = MissionParameters()
    params.vision.optical_center_tolerance_ratio = 0.0
    params.vision.optical_center_tolerance_px = 30.0
    offset = CENTER_X + 40.0

    assert cycles_to_lock(params, offset) == -1, "40 px is outside a 30 px gate"

    params.vision.optical_center_tolerance_px = 45.0
    assert cycles_to_lock(params, offset) == params.vision.alignment_dwell_cycles


def test_the_gate_scales_with_the_frame_through_the_configured_ratio():
    params = MissionParameters()
    params.vision.optical_center_tolerance_px = 1.0
    params.vision.optical_center_tolerance_ratio = 0.06
    inside = CENTER_X + 0.06 * FRAME[0] - 1.0

    assert cycles_to_lock(params, inside) == params.vision.alignment_dwell_cycles
    params.vision.optical_center_tolerance_ratio = 0.03
    assert cycles_to_lock(params, inside) == -1


def test_the_stillness_threshold_is_read_from_configuration():
    """`_servo` must gate the freeze on `inspection.settle_max_speed_mps`."""
    source = inspect.getsource(VisualServoingStep._servo)
    assert "inspection.settle_max_speed_mps" in source
    assert "_ALIGNMENT_DWELL_CYCLES" not in source
