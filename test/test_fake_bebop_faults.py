"""Declarative fault injection of the driver emulator (``test/support/fake_bebop/faults.py``).

The faults are the scope of this round (``docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md``
section 5): ``takeoff_rejected``, ``stuck_on_ground``, ``ignore_first_n_lands``,
``flat_trim_no_ack``, ``photo_no_ack``, ``battery_step`` and ``forced_landing``.
Each one is checked through the plant, which is what the mission sees.
"""

from __future__ import annotations

import json

import pytest

from support.fake_bebop.faults import FAULT_KINDS, FaultSchedule, parse_fault
from support.fake_bebop.plant import BebopPlant, FaultFired, FlatTrimAck, FlyingState, PictureEvent, PlantConfig

QUIET = dict(speed_noise_mps=0.0, speed_bias_walk_mps=0.0)


def run(plant, until, step=0.01):
    events = []
    t = plant.time
    while t < until - 1e-9:
        t = min(until, t + step)
        events.extend(plant.advance(t))
    return events


def plant_with(*faults):
    return BebopPlant(PlantConfig(**QUIET), faults=FaultSchedule([parse_fault(f) for f in faults]))


def fired(events, kind):
    return [e for e in events if isinstance(e, FaultFired) and e.kind == kind]


def test_the_scope_of_this_round_is_exactly_the_seven_faults():
    assert set(FAULT_KINDS) == {
        "takeoff_rejected",
        "stuck_on_ground",
        "ignore_first_n_lands",
        "flat_trim_no_ack",
        "photo_no_ack",
        "battery_step",
        "forced_landing",
    }


def test_takeoff_rejected_leaves_the_aircraft_landed_and_records_the_instant():
    plant = plant_with("takeoff_rejected")
    plant.takeoff(0.0)
    events = run(plant, 5.0)
    assert plant.state is FlyingState.LANDED
    assert plant.true_altitude == 0.0
    assert [round(e.t, 2) for e in fired(events, "takeoff_rejected")] == [0.15]


def test_stuck_on_ground_ramps_the_motors_and_never_leaves_the_ground():
    plant = plant_with("stuck_on_ground")
    plant.takeoff(0.0)
    run(plant, 8.0)
    assert plant.state is FlyingState.MOTOR_RAMPING
    assert plant.true_altitude == 0.0
    plant.land(plant.time)
    run(plant, plant.time + 1.0)
    assert plant.state is FlyingState.LANDED


def test_ignore_first_n_lands_ignores_exactly_n_then_lands():
    plant = plant_with("ignore_first_n_lands:n=3")
    plant.takeoff(0.0)
    run(plant, 6.0)
    events = []
    for _ in range(3):
        plant.land(plant.time)
        events += run(plant, plant.time + 0.5)
        assert plant.state is FlyingState.HOVERING
    plant.land(plant.time)
    events += run(plant, plant.time + 4.0)
    assert plant.state is FlyingState.LANDED
    assert len(fired(events, "ignore_first_n_lands")) == 3


def test_flat_trim_no_ack_never_advances_the_count():
    plant = plant_with("flat_trim_no_ack")
    plant.flat_trim(0.0)
    events = run(plant, 3.0)
    assert plant.flat_trim_sequence == 0
    assert not [e for e in events if isinstance(e, FlatTrimAck)]
    assert fired(events, "flat_trim_no_ack")


def test_photo_no_ack_reports_busy_and_ready_but_never_taken():
    plant = plant_with("photo_no_ack")
    plant.photo(0.0)
    events = [e for e in run(plant, 4.0) if isinstance(e, PictureEvent)]
    assert [(e.kind, e.value) for e in events] == [("state", 1), ("state", 0)]


def test_battery_step_after_takeoff_sets_the_charge_once():
    plant = plant_with('{"kind": "battery_step", "to_pct": 15, "after": "takeoff", "delay_sec": 2.0}')
    plant.takeoff(0.0)
    events = run(plant, 6.0)
    steps = fired(events, "battery_step")
    assert len(steps) == 1
    assert steps[0].t == pytest.approx(2.15, abs=0.01)
    assert plant.battery_pct == pytest.approx(15.0, abs=0.5)


def test_battery_step_at_an_absolute_instant():
    plant = plant_with("battery_step:to_pct=10,at_sec=1.0")
    events = run(plant, 2.0)
    assert fired(events, "battery_step")[0].t == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize("state", [4, 5, 8])
def test_forced_landing_puts_the_aircraft_in_the_state_and_down(state):
    plant = plant_with(f"forced_landing:state={state},after=hover,delay_sec=1.0")
    plant.takeoff(0.0)
    events = run(plant, 12.0)
    assert FlyingState(state) in [e.new for e in events if hasattr(e, "new")]
    assert plant.state is FlyingState.LANDED
    assert plant.true_altitude == 0.0


def test_a_json_list_parses_into_a_schedule():
    schedule = FaultSchedule.from_json(json.dumps([{"kind": "takeoff_rejected"}, {"kind": "photo_no_ack"}]))
    assert schedule.rejects_takeoff()
    assert not schedule.acknowledges_photo()


@pytest.mark.parametrize(
    "text, error",
    [
        ("link_drop", ValueError),
        ("ignore_first_n_lands:n=0", ValueError),
        ("ignore_first_n_lands:n=two", ValueError),
        ("battery_step:to_pct=150,at_sec=1", ValueError),
        ("battery_step:to_pct=10", ValueError),
        ("forced_landing:state=2,at_sec=1", ValueError),
        ("forced_landing:state=4,after=cruise,delay_sec=1", ValueError),
        ("battery_step:to_pct=10,at_sec=-1", ValueError),
        ('{"kind": "battery_step", "to_pct": true, "at_sec": 1}', TypeError),
        ("{not json", ValueError),
    ],
)
def test_a_malformed_fault_is_refused(text, error):
    with pytest.raises(error):
        parse_fault(text)


def test_a_non_string_fault_is_refused():
    with pytest.raises(TypeError):
        parse_fault(3)
