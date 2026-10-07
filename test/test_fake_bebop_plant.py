"""Pure physics of the Bebop 2 driver emulator (``test/support/fake_bebop/plant.py``).

The emulator stands where ``ros2_bebop_driver`` would so that ``mission.py
--fly`` can run without motors. Its plant is deliberately independent of the
mission's ``KinematicSimulator``: a mission validated against its own model
proves nothing. These tests pin the behaviour the mission depends on --
ARSDK flying states, command latency and lag, the driver's int8 quantization,
odometry integrated from speed samples -- without rclpy.
"""

from __future__ import annotations

import math

import pytest

from support.fake_bebop.plant import (
    BebopPlant,
    FlyingState,
    OdometrySample,
    PictureEvent,
    PlantConfig,
    StateChange,
)

QUIET = dict(speed_noise_mps=0.0, speed_bias_walk_mps=0.0)


def run(plant, until, step=0.01):
    events = []
    t = plant.time
    while t < until - 1e-9:
        t = min(until, t + step)
        events.extend(plant.advance(t))
    return events


def airborne(config=None):
    plant = BebopPlant(config or PlantConfig(**QUIET))
    plant.takeoff(0.0)
    run(plant, 6.0)
    assert plant.state is FlyingState.HOVERING
    return plant


def states(events):
    return [(e.old, e.new) for e in events if isinstance(e, StateChange)]


# ------------------------------------------------------------------ takeoff


def test_a_takeoff_from_the_ground_ramps_takes_off_and_hovers_near_one_metre():
    plant = BebopPlant(PlantConfig(**QUIET))
    plant.takeoff(0.0)
    events = run(plant, 6.0)
    assert states(events) == [
        (FlyingState.LANDED, FlyingState.MOTOR_RAMPING),
        (FlyingState.MOTOR_RAMPING, FlyingState.TAKINGOFF),
        (FlyingState.TAKINGOFF, FlyingState.HOVERING),
    ]
    hover_at = [e.t for e in events if isinstance(e, StateChange) and e.new is FlyingState.HOVERING][0]
    assert 3.0 <= hover_at <= 4.6
    assert plant.true_altitude == pytest.approx(1.0, abs=0.05)


def test_a_takeoff_while_airborne_is_ignored():
    plant = airborne()
    plant.takeoff(plant.time)
    assert states(run(plant, plant.time + 2.0)) == []


# ------------------------------------------------------------------ PCMD


def test_a_velocity_command_on_the_ground_moves_nothing():
    plant = BebopPlant(PlantConfig(**QUIET))
    plant.pcmd(0.0, 0.5, 0.0, 0.0, 0.0)
    events = run(plant, 3.0)
    assert plant.state is FlyingState.LANDED
    assert states(events) == []
    assert plant.true_position == (0.0, 0.0, 0.0)


def test_steady_speed_is_the_command_times_gain_times_efficiency():
    plant = airborne(PlantConfig(efficiency=0.8, **QUIET))
    plant.pcmd(plant.time, 0.5, 0.0, 0.0, 0.0)
    run(plant, plant.time + 5.0)
    assert plant.true_velocity[0] == pytest.approx(0.5 * 1.0 * 0.8, rel=0.02)
    assert plant.state is FlyingState.FLYING


def test_the_command_reaches_the_airframe_only_after_the_latency():
    plant = airborne(PlantConfig(latency_sec=0.15, tau_sec=0.3, **QUIET))
    t0 = plant.time
    plant.pcmd(t0, 0.5, 0.0, 0.0, 0.0)
    run(plant, t0 + 0.14)
    assert plant.true_velocity[0] == pytest.approx(0.0, abs=1e-9)
    run(plant, t0 + 0.15 + 0.3)
    assert plant.true_velocity[0] == pytest.approx(0.5 * (1.0 - math.exp(-1.0)), rel=0.05)


def test_a_command_below_one_percent_is_quantized_to_zero_like_the_driver():
    plant = airborne()
    plant.pcmd(plant.time, 0.008, -0.009, 0.0, 0.0)
    run(plant, plant.time + 3.0)
    assert plant.true_velocity[:2] == (0.0, 0.0)
    assert plant.state is FlyingState.HOVERING


def test_a_zero_command_returns_flying_to_hovering():
    plant = airborne()
    plant.pcmd(plant.time, 0.3, 0.0, 0.0, 0.0)
    run(plant, plant.time + 1.0)
    plant.pcmd(plant.time, 0.0, 0.0, 0.0, 0.0)
    events = run(plant, plant.time + 1.0)
    assert (FlyingState.FLYING, FlyingState.HOVERING) in states(events)


def test_a_vertical_only_command_keeps_hovering_and_climbs():
    plant = airborne()
    z0 = plant.true_altitude
    plant.pcmd(plant.time, 0.0, 0.0, 0.2, 0.0)
    events = run(plant, plant.time + 3.0)
    assert states(events) == []
    assert plant.true_altitude > z0 + 0.3


def test_the_airframe_never_goes_below_the_ground():
    plant = airborne()
    plant.pcmd(plant.time, 0.0, 0.0, -1.0, 0.0)
    run(plant, plant.time + 5.0)
    assert plant.true_altitude >= 0.0


# ------------------------------------------------------------------ landing


def test_a_land_from_hover_descends_at_the_landing_rate_and_ends_landed():
    plant = airborne()
    t0 = plant.time
    plant.land(t0)
    events = run(plant, t0 + 6.0)
    assert states(events) == [
        (FlyingState.HOVERING, FlyingState.LANDING),
        (FlyingState.LANDING, FlyingState.LANDED),
    ]
    landed_at = [e.t for e in events if isinstance(e, StateChange) and e.new is FlyingState.LANDED][0]
    assert landed_at - t0 == pytest.approx(1.0 / 0.4 + 0.15, abs=0.3)
    assert plant.true_altitude == 0.0


def test_a_land_during_the_motor_ramp_stops_the_motors():
    plant = BebopPlant(PlantConfig(**QUIET))
    plant.takeoff(0.0)
    run(plant, 0.5)
    assert plant.state is FlyingState.MOTOR_RAMPING
    plant.land(plant.time)
    run(plant, 2.0)
    assert plant.state is FlyingState.LANDED


def test_a_velocity_command_during_the_landing_is_ignored():
    plant = airborne()
    plant.land(plant.time)
    run(plant, plant.time + 0.5)
    plant.pcmd(plant.time, 0.5, 0.0, 0.0, 0.0)
    run(plant, plant.time + 1.0)
    assert plant.state is FlyingState.LANDING
    assert abs(plant.true_velocity[0]) < 1e-6


# ------------------------------------------------------------------ flat trim / photo


def test_a_flat_trim_on_the_ground_is_acknowledged_after_its_delay():
    plant = BebopPlant(PlantConfig(flat_trim_ack_sec=0.3, **QUIET))
    plant.flat_trim(0.0)
    assert plant.flat_trim_sequence == 0
    run(plant, 0.29)
    assert plant.flat_trim_sequence == 0
    run(plant, 0.35)
    assert plant.flat_trim_sequence == 1


def test_a_flat_trim_in_flight_is_never_acknowledged():
    plant = airborne()
    plant.flat_trim(plant.time)
    run(plant, plant.time + 2.0)
    assert plant.flat_trim_sequence == 0


def test_a_photo_reports_busy_then_taken_then_ready_with_one_sequence():
    plant = BebopPlant(PlantConfig(photo_taken_sec=2.5, **QUIET))
    plant.photo(0.0)
    events = [e for e in run(plant, 4.0) if isinstance(e, PictureEvent)]
    assert [(e.kind, e.value, e.error) for e in events] == [("state", 1, 0), ("event", 0, 0), ("state", 0, 0)]
    assert [e.sequence for e in events] == [1, 2, 3]
    assert events[1].t == pytest.approx(2.5, abs=0.02)


# ------------------------------------------------------------------ odometry


def test_odometry_comes_at_five_hertz_and_only_with_a_new_sample():
    plant = BebopPlant(PlantConfig(**QUIET))
    samples = [e for e in run(plant, 2.0) if isinstance(e, OdometrySample)]
    assert len(samples) == pytest.approx(10, abs=1)
    stamps = [s.t for s in samples]
    assert all(b > a for a, b in zip(stamps, stamps[1:]))


def test_odometric_pose_is_the_integral_of_the_reported_speed_not_the_truth():
    plant = airborne(PlantConfig(speed_noise_mps=0.0, speed_bias_walk_mps=0.0, speed_bias_mps=(0.02, 0.0, 0.0)))
    x_true0 = plant.true_position[0]
    odom0 = plant.last_odometry.x
    run(plant, plant.time + 10.0)
    assert plant.true_position[0] - x_true0 == pytest.approx(0.0, abs=1e-6)
    assert plant.last_odometry.x - odom0 == pytest.approx(0.2, abs=0.03)


def test_odometry_is_published_in_the_ground_frame_with_vertical_speed():
    plant = BebopPlant(PlantConfig(**QUIET))
    plant.takeoff(0.0)
    run(plant, 2.5)
    sample = plant.last_odometry
    assert plant.state is FlyingState.TAKINGOFF
    assert sample.vz == pytest.approx(0.4, abs=0.01)
    assert sample.z > 0.0


# ------------------------------------------------------------------ battery


def test_the_battery_drains_faster_in_flight_than_on_the_ground():
    ground = BebopPlant(PlantConfig(battery_start_pct=80.0, **QUIET))
    run(ground, 60.0, step=0.05)
    flying = airborne(PlantConfig(battery_start_pct=80.0, **QUIET))
    start = flying.battery_pct
    run(flying, flying.time + 60.0, step=0.05)
    assert 80.0 - ground.battery_pct < start - flying.battery_pct
    assert start - flying.battery_pct == pytest.approx(5.0, abs=0.5)


# ------------------------------------------------------------------ seeds and validation


def test_seeds_draw_distinct_reproducible_plants_inside_the_documented_bands():
    plants = [PlantConfig.from_seed(seed) for seed in (1, 2, 3)]
    assert PlantConfig.from_seed(2) == plants[1]
    assert len({p.efficiency for p in plants}) == 3
    for p in plants:
        assert 0.8 <= p.efficiency <= 1.2
        assert 0.25 <= p.tau_sec <= 0.5
        assert 0.10 <= p.latency_sec <= 0.20


@pytest.mark.parametrize(
    "field, value",
    [("efficiency", 0.0), ("tau_sec", -0.1), ("latency_sec", -1.0), ("odom_rate_hz", 0.0), ("battery_start_pct", 101.0)],
)
def test_an_out_of_range_config_is_refused(field, value):
    with pytest.raises(ValueError):
        PlantConfig(**{field: value})


def test_a_non_numeric_config_is_refused():
    with pytest.raises(TypeError):
        PlantConfig(tau_sec="0.3")


def test_time_never_runs_backwards():
    plant = BebopPlant(PlantConfig(**QUIET))
    run(plant, 1.0)
    with pytest.raises(ValueError):
        plant.advance(0.5)
