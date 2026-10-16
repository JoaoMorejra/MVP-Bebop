"""Integration: the three mission profiles against the driver emulator.

One run per profile, shared by the assertions on it. Checks sequence, command
signs and safety; the emulator's fixed scene does not let Stage 3 converge.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Final, List, Tuple

import pytest

from support.fake_bebop.harness import EmulatedFlight, FlightResult, SHORT_FLIGHT

#: 80, 89 and 99 are the IDs of the emulator range 80-99 on which no other test in test/ joins a
#: DDS graph; 89 appears only as a refused ``--domain`` value in test_fake_bebop_interlocks.py.
PROFILES: Final[List[Tuple[str, int]]] = [("advanced", 80), ("intermediate", 89), ("simple", 99)]


def document(mode: str) -> Dict[str, Any]:
    doc = copy.deepcopy(SHORT_FLIGHT)
    doc["mission_mode"] = mode
    if mode == "simple":
        doc["kinematics"]["target_altitude_m"] = 1.0
    return doc


def step_start(result: FlightResult, step: int) -> float:
    line = result.first_line(f"[STEP {step}:")
    assert line is not None, f"no STEP {step} marker:\n{result.tail()}"
    return line.mono


@pytest.fixture(scope="module", params=PROFILES, ids=[mode for mode, _ in PROFILES])
def run(request, tmp_path_factory) -> Tuple[str, FlightResult]:
    mode, domain = request.param
    flight = EmulatedFlight(str(tmp_path_factory.mktemp(f"mode_{mode}")), domain=domain, seed=1)
    with flight:
        return mode, flight.fly(document(mode), fly=True, timeout=240.0)


def test_every_profile_runs_the_five_stage_markers_in_order(run):
    mode, result = run
    assert result.exit_code == 0, f"{mode} exited {result.exit_code}:\n{result.tail()}"
    assert result.steps() == [1, 2, 3, 4, 5]
    assert "new_publishers" in result.watchdog, f"watchdog report missing: {result.watchdog}"
    assert not any(count > 0 for count in result.watchdog["new_publishers"].values()), \
        "watchdog found publishers on domain 14"


def test_every_profile_echoes_the_mode_it_flies(run):
    mode, result = run
    echoed = [
        line.text for line in result.lines
        if "[MILESTONE mission.mode]" in line.text
    ]
    assert len(echoed) == 1, f"{mode}: expected one mission.mode milestone, got {echoed}"
    assert f'"mode": "{mode}"' in echoed[0].replace('":"', '": "')
    order = [line.text for line in result.lines]
    mode_at = next(i for i, t in enumerate(order) if "[MILESTONE mission.mode]" in t)
    params_at = next(i for i, t in enumerate(order) if "[MILESTONE mission.parameters]" in t)
    assert mode_at < params_at, "the mode must be echoed before the parameters (the cache keys on it)"


def test_every_profile_takes_off_once_and_ends_on_the_ground(run):
    mode, result = run
    assert len(result.commands("takeoff")) == 1, mode
    assert result.commands("land"), f"{mode} never requested a landing"


def test_the_intermediate_profile_never_flies_the_return_leg(run):
    mode, result = run
    if mode != "intermediate":
        pytest.skip("intermediate only")
    text = "\n".join(line.text for line in result.lines)
    assert "Executing terminal landing" in text
    assert "Centering on marker" not in text
    assert "rtl_start" not in text


def test_the_simple_profile_never_translates_during_stages_two_to_four(run):
    mode, result = run
    if mode != "simple":
        pytest.skip("simple only")
    start, end = step_start(result, 2), step_start(result, 5)
    # Stage 5 of the Simple profile centres on the pad marker, which translates by design.
    flown = [record["v"] for record in result.nonzero_twists(after=start) if record["mono"] < end]
    assert any(v[3] != 0.0 for v in flown), "the simple profile never rotated"
    assert all(v[0] == 0.0 and v[1] == 0.0 for v in flown)


def test_yaw_appears_only_in_the_simple_profile(run):
    mode, result = run
    yawed = [r for r in result.nonzero_twists() if r["v"][3] != 0.0]
    if mode == "simple":
        assert yawed
    else:
        assert not yawed, f"{mode} transmitted a yaw command"


def test_every_simple_stage_boundary_is_crossed_on_a_zero_yaw_twist(run):
    mode, result = run
    if mode != "simple":
        pytest.skip("simple only")
    # The Bebop latches the last Twist it received: a stage that ends on a nonzero
    # yaw keeps the airframe turning into the next one.
    twists = result.commands("cmd_vel")
    for step in (3, 4, 5):
        start = step_start(result, step)
        before = [record for record in twists if record["mono"] < start]
        assert before, f"no Twist before the STEP {step} marker"
        assert before[-1]["v"][3] == 0.0, f"STEP {step} entered on yaw {before[-1]['v']}"


def test_the_simple_landing_stage_never_yaws(run):
    mode, result = run
    if mode != "simple":
        pytest.skip("simple only")
    yawed = [r["v"] for r in result.commands("cmd_vel") if r["mono"] >= step_start(result, 5) and r["v"][3] != 0.0]
    assert not yawed, f"Stage 5 transmitted yaw: {yawed[:5]}"
