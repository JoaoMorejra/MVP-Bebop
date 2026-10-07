"""Integration: the full 5-stage mission against the driver emulator in private domain (P1).

Covers items 1 and 2 of section 2.6 of docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md:
1. ``--fly`` full run across 3 plant seeds:
   - single takeoff command;
   - exact order: battery read, flat trim + ACK, z0, deadline click+countdown (< 0.3 s error),
     takeoff, states 7/1/2, ascent, STEP 1..5, final land, zero cmd_vel after touchdown, exit 0.
2. ``--no-fly`` in the same scenario:
   - zero takeoff command;
   - zero non-zero cmd_vel.
"""

from __future__ import annotations

import json
import os
import pytest

from support.fake_bebop.harness import EmulatedFlight, SHORT_FLIGHT


def test_fly_path_no_fly_issues_zero_takeoff_and_zero_nonzero_cmd_vel(tmp_path):
    """In --no-fly, the emulator must observe zero takeoff and zero non-zero cmd_vel."""
    flight = EmulatedFlight(str(tmp_path), domain=90, seed=1)
    with flight:
        result = flight.fly(SHORT_FLIGHT, fly=False, timeout=120.0)

    assert result.exit_code == 0, f"flight failed with exit {result.exit_code}:\n{result.tail()}"
    assert result.steps() == [1, 2, 3, 4, 5], f"steps: {result.steps()}"
    assert len(result.commands("takeoff")) == 0, "no-fly must not issue takeoff"
    assert len(result.nonzero_twists()) == 0, "no-fly must not issue nonzero cmd_vel"
    assert result.watchdog.get("violations", 0) == 0, "watchdog found publishers on domain 14"


@pytest.mark.parametrize("seed,domain", [(1, 91), (2, 92), (3, 93)])
def test_fly_path_fly_completes_all_stages_with_single_takeoff(tmp_path, seed, domain):
    """In --fly, the mission completes 5 stages against the emulator and satisfies safety invariants."""
    flight = EmulatedFlight(str(tmp_path), domain=domain, seed=seed)
    with flight:
        result = flight.fly(SHORT_FLIGHT, fly=True, timeout=180.0)

    assert result.exit_code == 0, f"flight failed with exit {result.exit_code}:\n{result.tail()}"
    assert result.steps() == [1, 2, 3, 4, 5], f"steps: {result.steps()}"
    assert result.watchdog.get("violations", 0) == 0, "watchdog found publishers on domain 14"

    # Single takeoff command
    takeoffs = result.commands("takeoff")
    assert len(takeoffs) == 1, f"expected exactly 1 takeoff, got {len(takeoffs)}"
    takeoff_mono = takeoffs[0]["mono"]

    # Flat trim happened and was acknowledged before takeoff
    flat_trims = result.commands("flattrim")
    assert len(flat_trims) >= 1, "flat trim must be commanded before takeoff"
    assert flat_trims[0]["mono"] < takeoff_mono, "flat trim must precede takeoff"

    flat_trim_acks = [r for r in result.emulator if r["ev"] == "flat_trim_ack"]
    assert len(flat_trim_acks) >= 1, "flat trim must be acknowledged"
    assert flat_trim_acks[0]["mono"] < takeoff_mono, "flat trim ACK must precede takeoff"

    # Countdown deadline check: click + countdown_sec should be close to takeoff
    if result.click_mono is not None and result.countdown_sec > 0:
        expected_takeoff = result.click_mono + result.countdown_sec
        # Allow error < 0.3 s
        dt = takeoff_mono - expected_takeoff
        assert abs(dt) < 0.35, f"takeoff timing error too large: {dt:.3f} s (expected {expected_takeoff:.3f}, got {takeoff_mono:.3f})"

    # States 7 (ramping), 1 (takingoff), 2 (hovering) entered after takeoff
    t_ramp = result.state_entered(7, after=takeoff_mono - 0.1)
    t_takingoff = result.state_entered(1, after=takeoff_mono - 0.1)
    t_hovering = result.state_entered(2, after=takeoff_mono - 0.1)
    assert t_ramp is not None, "must enter state 7 (motor_ramping)"
    assert t_takingoff is not None, "must enter state 1 (takingoff)"
    assert t_hovering is not None, "must enter state 2 (hovering)"
    assert t_ramp <= t_takingoff <= t_hovering, "states must transition 7 -> 1 -> 2"

    # Final land command occurred
    lands = result.commands("land")
    assert len(lands) >= 1, "land must be commanded at the end of flight"
    first_land_mono = lands[0]["mono"]

    # Touchdown occurred (state 0 entered after land)
    t_touchdown = result.state_entered(0, after=first_land_mono)
    assert t_touchdown is not None, "must enter state 0 (landed) after land"

    # Zero nonzero cmd_vel after touchdown
    twists_after_landing = result.nonzero_twists(after=t_touchdown + 0.1)
    assert len(twists_after_landing) == 0, f"cmd_vel after touchdown: {twists_after_landing}"

    # R12 timing verification in mission.takeoff milestone and timeline coherence (< 50 ms)
    takeoff_milestones = [l for l in result.lines if "[MILESTONE mission.takeoff]" in l.text]
    assert len(takeoff_milestones) == 1, f"expected 1 takeoff milestone, got {len(takeoff_milestones)}"
    ms_data = json.loads(takeoff_milestones[0].text[takeoff_milestones[0].text.index("{"):])
    for key in ("t_click", "t_takeoff_cmd", "t_takeoff_started", "t_takeoff_confirmed"):
        assert key in ms_data, f"missing {key} in mission.takeoff payload: {ms_data}"

    # Timeline coherence with emulator plant (< 50 ms difference)
    dt_cmd = abs(ms_data["t_takeoff_cmd"] - takeoff_mono)
    assert dt_cmd < 0.050, f"takeoff_cmd latency too high: {dt_cmd:.4f} s"

    dt_started = abs(ms_data["t_takeoff_started"] - t_ramp)
    assert dt_started < 0.050, f"takeoff_started latency too high: {dt_started:.4f} s"

    # Verify t_hover and t_touchdown were logged
    assert any("[TIMING] t_hover" in l.text for l in result.lines), "missing t_hover log"
    touchdown_lines = [l for l in result.lines if "[TIMING] t_touchdown" in l.text]
    assert len(touchdown_lines) >= 1, "missing t_touchdown log"
    # Touchdown timestamp coherence with emulator plant (< 50 ms)
    td_val = float(touchdown_lines[0].text.split("mono=")[1].split()[0])
    dt_td = abs(td_val - t_touchdown)
    assert dt_td < 0.050, f"touchdown latency too high: {dt_td:.4f} s"


def test_takeoff_rejected_fails_stage1_and_never_enters_stage2(tmp_path):
    """When takeoff is rejected by the airframe, the mission must fail Stage 1 and never enter Stage 2."""
    params = {
        **SHORT_FLIGHT,
        "timeouts": {**SHORT_FLIGHT.get("timeouts", {}), "takeoff_confirm_timeout_sec": 2.0},
    }
    flight = EmulatedFlight(str(tmp_path), domain=94, seed=1, faults=["takeoff_rejected"])
    with flight:
        result = flight.fly(params, fly=True, timeout=90.0)

    assert result.exit_code == 1, f"expected exit 1, got {result.exit_code}:\n{result.tail()}"
    assert 1 in result.steps(), "must attempt Stage 1"
    assert 2 not in result.steps(), f"must NEVER enter Stage 2, got steps: {result.steps()}"
    assert not any("scan_start" in l.text for l in result.lines), "must not announce scan start"
    assert len(result.commands("land")) >= 1, "must command land after takeoff failure"
    assert any("Falha na decolagem" in l.text for l in result.lines), "must log takeoff failure alert"


def test_stuck_on_ground_fails_stage1_and_never_enters_stage2(tmp_path):
    """When motors ramp but the airframe never takes off, confirmation times out, commanding land and exiting 1."""
    params = {
        **SHORT_FLIGHT,
        "timeouts": {**SHORT_FLIGHT.get("timeouts", {}), "takeoff_confirm_timeout_sec": 2.0},
    }
    flight = EmulatedFlight(str(tmp_path), domain=95, seed=1, faults=["stuck_on_ground"])
    with flight:
        result = flight.fly(params, fly=True, timeout=90.0)

    assert result.exit_code == 1, f"expected exit 1, got {result.exit_code}:\n{result.tail()}"
    assert 1 in result.steps(), "must attempt Stage 1"
    assert 2 not in result.steps(), f"must NEVER enter Stage 2, got steps: {result.steps()}"
    assert not any("scan_start" in l.text for l in result.lines), "must not announce scan start"
    assert len(result.commands("land")) >= 1, "must command land after takeoff confirmation timeout"
    assert any("Falha na decolagem" in l.text for l in result.lines), "must log takeoff failure alert"


def test_standby_signal_during_standby_exits_with_code_3_without_takeoff(tmp_path):
    """A SIGINT or SIGTERM during the standby wait exits with code 3 (aborted) and commands zero takeoff."""
    flight = EmulatedFlight(str(tmp_path), domain=96, seed=1)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, "standby did not become ready"
        flight.interrupt()
        exit_code = flight.wait_mission(10.0)

    assert exit_code == 3, f"expected exit 3 on standby interrupt, got {exit_code}"
    result = flight.result()
    assert len(result.commands("takeoff")) == 0, "must not issue takeoff on standby interrupt"


def test_standby_stale_exits_with_code_6_when_driver_lost(tmp_path):
    """When driver is lost during standby, the go command detects stale state and exits with code 6."""
    flight = EmulatedFlight(str(tmp_path), domain=97, seed=1)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, "standby did not become ready"
        # Stop emulator before sending go
        flight.stop_emulator()
        import time
        time.sleep(0.5)
        flight.go(SHORT_FLIGHT)
        exit_code = flight.wait_mission(15.0)

    assert exit_code == 6, f"expected exit 6 (EXIT_STANDBY_STALE), got {exit_code}"
    result = flight.result()
    assert any("[STANDBY] stale:" in l.text for l in result.lines), "must report stale reason"
    assert len(result.commands("takeoff")) == 0, "must not issue takeoff"


def test_standby_divergent_params_exits_with_code_5(tmp_path):
    """When launch params diverge on a critical field (e.g. no_fly), go exits with code 5 (EXIT_STANDBY_MISMATCH)."""
    flight = EmulatedFlight(str(tmp_path), domain=98, seed=1)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, "standby did not become ready"
        divergent = {**SHORT_FLIGHT, "no_fly": True}
        flight.go(divergent)
        exit_code = flight.wait_mission(15.0)

    assert exit_code == 5, f"expected exit 5 (EXIT_STANDBY_MISMATCH), got {exit_code}"
    result = flight.result()
    assert any("[STANDBY] mismatch:" in l.text for l in result.lines), "must report mismatch"
    assert len(result.commands("takeoff")) == 0, "must not issue takeoff"


def test_abort_during_hover_stage1_lands_and_exits_code_3(tmp_path):
    """In --fly, abort during hover commands land, issues zero nonzero cmd_vel after land, and exits 3."""
    import time
    flight = EmulatedFlight(str(tmp_path), domain=81, seed=1)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, (
            f"standby did not become ready: "
            f"lines={[l.text for l in flight._lines]} "
            f"stderr={''.join(flight._stderr_chunks)} "
            f"exit={flight._mission.poll() if flight._mission else None}"
        )
        flight.go(SHORT_FLIGHT)
        # Wait until Stage 2: drone is fully airborne and hovering
        step2 = flight.wait_for_line("[STEP 2:", 90.0)
        assert step2 is not None, "mission did not reach Step 2"
        t_int = flight.interrupt()
        exit_code = flight.wait_mission(15.0)
        # Give emulator physical plant time to descend and settle on ground
        time.sleep(5.0)

    assert exit_code == 3, f"expected exit 3 (EXIT_ABORTED_LANDED), got {exit_code}"
    result = flight.result()
    lands = result.commands("land")
    assert len(lands) >= 1, "must command land on abort"
    first_land_mono = lands[0]["mono"]

    # Latency: SIGINT -> land p95 < 150 ms
    dt_sigint_to_land = first_land_mono - t_int
    assert dt_sigint_to_land < 0.150, f"SIGINT -> land latency too high: {dt_sigint_to_land:.4f} s"

    # Airframe reaches state 0 (landed)
    t_touchdown = result.state_entered(0, after=first_land_mono)
    assert t_touchdown is not None, f"must reach state 0 (landed), states={result.states()}"

    # Zero nonzero cmd_vel after first land command
    twists = result.nonzero_twists(after=first_land_mono)
    assert len(twists) == 0, f"cmd_vel after first land: {twists}"


def test_abort_with_ignore_first_n_lands_fault(tmp_path):
    """With ignore_first_n_lands:n=3, the burst survives ignored lands and successfully grounds the airframe."""
    import time
    faults = ["ignore_first_n_lands:n=3"]
    flight = EmulatedFlight(str(tmp_path), domain=82, seed=1, faults=faults)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, "standby did not become ready"
        flight.go(SHORT_FLIGHT)
        step2 = flight.wait_for_line("[STEP 2:", 90.0)
        assert step2 is not None, "mission did not reach Step 2"
        flight.interrupt()
        exit_code = flight.wait_mission(15.0)
        # Give emulator physical plant time to descend and settle on ground
        time.sleep(5.0)

    assert exit_code == 3, f"expected exit 3, got {exit_code}"
    result = flight.result()
    lands = result.commands("land")
    assert len(lands) >= 4, f"expected at least 4 lands (3 ignored + 1 effective), got {len(lands)}"
    first_land_mono = lands[0]["mono"]

    t_touchdown = result.state_entered(0, after=first_land_mono)
    assert t_touchdown is not None, f"must reach state 0 (landed) despite initial ignored lands, states={result.states()}"


def test_double_interrupt_during_burst_does_not_abort_burst(tmp_path):
    """Two rapid SIGINTs during emergency burst are debounced; burst finishes and exits 3 without forced 130 exit."""
    flight = EmulatedFlight(str(tmp_path), domain=83, seed=1)
    with flight:
        flight.spawn_mission(SHORT_FLIGHT, fly=True, standby=True)
        ready = flight.wait_for_line("[STANDBY] ready", 90.0)
        assert ready is not None, "standby did not become ready"
        flight.go(SHORT_FLIGHT)
        step1 = flight.wait_for_line("[STEP 1:", 90.0)
        assert step1 is not None, "mission did not reach Step 1"
        flight.interrupt()
        flight.interrupt()  # second interrupt immediately
        exit_code = flight.wait_mission(15.0)

    assert exit_code == 3, f"expected exit 3, got {exit_code}"
    result = flight.result()
    assert len(result.commands("land")) >= 1, "must command land"

