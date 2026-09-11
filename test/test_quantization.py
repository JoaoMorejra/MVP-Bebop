"""Unit tests for actuator quantization compensation.

The property under test is the one the old hard floor could not provide: the
time-average of the emitted commands tracks the demand all the way down to
zero, while no individual command lands in the band the Bebop driver truncates
to zero.
"""

import pytest

from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper

DT = 1.0 / 15.0
FLOOR = 0.035
STEP = 0.01


def shaper():
    return QuantizedCommandShaper(min_effective=FLOOR, quantization_step=STEP)


def driver_truncation(command):
    """Reproduce the Bebop C++ driver's int8(v * 100) quantization."""
    return int(command * 100.0)


def test_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        QuantizedCommandShaper(min_effective=0.0, quantization_step=0.01)
    with pytest.raises(ValueError):
        QuantizedCommandShaper(min_effective=0.05, quantization_step=0.0)
    with pytest.raises(ValueError):
        QuantizedCommandShaper(min_effective=0.005, quantization_step=0.01)


def test_floor_is_snapped_onto_the_actuator_grid():
    # 0.035 is not representable against a 0.01 step; leaving it unsnapped
    # makes every pulse differ from the value the accumulator is debited by.
    assert shaper().floor == pytest.approx(0.04)


@pytest.mark.parametrize("demand", [0.10, 0.095, 0.072, 0.055, 0.04, 0.031, 0.02, 0.008, 0.002])
def test_mean_tracks_demand_within_one_pulse(demand):
    shaper_ = shaper()
    cycles = 600
    emitted = [shaper_.shape(demand, DT) for _ in range(cycles)]
    mean = sum(emitted) / cycles

    # The accumulator holds at most one pulse of undischarged displacement, so
    # the mean error is bounded by floor / cycles.
    assert abs(mean - demand) <= shaper_.floor / cycles + 1e-9


@pytest.mark.parametrize("demand", [0.10, 0.055, 0.031, 0.02, 0.008, 0.002, -0.015, -0.055])
def test_no_command_is_truncated_to_zero_by_the_driver(demand):
    shaper_ = shaper()
    for _ in range(600):
        command = shaper_.shape(demand, DT)
        if command != 0.0:
            assert abs(driver_truncation(command)) >= 1, (
                f"command {command!r} truncates to zero at the driver"
            )


def test_preserves_the_historical_lateral_quantization_guarantee():
    # The original suite pinned this: a lateral pulse must survive int8(v * 100)
    # with at least three units of authority.
    shaper_ = shaper()
    pulses = [c for c in (shaper_.shape(0.004, DT) for _ in range(400)) if c != 0.0]
    assert pulses, "a sub-threshold demand must still produce pulses"
    assert all(driver_truncation(abs(c)) >= 3 for c in pulses)


def test_sign_is_preserved():
    shaper_ = shaper()
    emitted = [shaper_.shape(-0.02, DT) for _ in range(200)]
    assert all(c <= 0.0 for c in emitted)
    assert sum(emitted) < 0.0


def test_zero_demand_emits_nothing_and_drains_the_accumulator():
    shaper_ = shaper()
    for _ in range(5):
        shaper_.shape(0.006, DT)      # charge without reaching a pulse
    assert shaper_.shape(0.0, DT) == 0.0
    assert shaper_.residual == 0.0


def test_deceleration_sweep_is_monotonic_in_the_mean():
    """The case a hard floor makes impossible: commanding progressively slower."""
    shaper_ = shaper()
    means = []
    for demand in (0.10, 0.08, 0.06, 0.04, 0.025, 0.015, 0.008, 0.003):
        emitted = [shaper_.shape(demand, DT) for _ in range(200)]
        means.append(sum(emitted) / len(emitted))

    for earlier, later in zip(means, means[1:]):
        assert later < earlier, "mean emitted velocity must keep decreasing"


def test_non_positive_dt_emits_nothing():
    assert shaper().shape(0.1, 0.0) == 0.0
