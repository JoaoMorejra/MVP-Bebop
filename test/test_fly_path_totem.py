"""Integration: the full mission from a 1.0 m pad against the emulator's sonar model (section 5.1).

The emulator models the pad and a nearest-echo sonar whose firmware hold moves
the airframe when the pad leaves or enters the beam while ``gaz`` is exactly
zero (outcome M1 of section 1.5). The flight is flown twice, with the platform
edge guard on and off, against the same plant.

The pattern is lateral: Stage 2 leaves the pad to the LEFT (``+y``, REP-103)
and Stage 5 returns to the RIGHT, re-entering it from its left side. The pad is
square and centred on the takeoff point, so ``EDGE_M`` is the distance to the
edge along ``y`` as it was along ``x`` in the forward pattern.

What this validates is the mission against a plant with a *known* sonar hold
gain. It is not the firmware: the real reaction to the pad edge is measured in
field test F-2.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import pytest

from mvp_mission_bebop.telemetry.milestones import MILESTONE_KEYS
from support.fake_bebop.harness import EmulatedFlight, FlightResult

PAD_HEIGHT_M = 1.0
EDGE_M = 1.0
RELATIVE_TARGET_M = 0.8
VENUE_AGL_M = 2.8
ALLOWANCE_M = 0.4
#: Target revealed to the left past the far end of the outbound guard window
#: (``s* + edge_uncertainty_m + guard_extent_m``, about 2.45 m), so the pad edge
#: is crossed and cleared in Stage 2.
REVEAL_Y_M = 3.0
#: The marker comes into view this close to the pad, so Stage 5 settles near the
#: takeoff point, well inside the pad, as it does over the real marker.
MARKER_RADIUS_M = 0.3
#: Acceptance target of field test F-2, applied here to the plant of known gain.
MAX_EXIT_LOSS_M = 0.25

EMULATOR_ARGS = (
    "--platform-height", str(PAD_HEIGHT_M),
    "--platform-edge", str(EDGE_M),
    "--sonar-hold-gain", "2.0",
    "--reveal-y", str(REVEAL_Y_M),
    "--marker-radius", str(MARKER_RADIUS_M),
    # The platform monitor works on the odometric along-track position. The
    # seeded plant's speed bias walks it about a metre off over a full mission,
    # which is the drift field test F-3 measures; this flight isolates the
    # state machine and the edge guard from it.
    "--no-odometry-drift",
)


def totem_params(guard: bool) -> Dict:
    return {
        "kinematics": {
            "countdown_sec": 3.0,
            "target_altitude_m": 1.8,
            "takeoff_stabilize_duration_sec": 3.0,
            "hover_duration_sec": 1.0,
        },
        "timeouts": {"search_timeout_sec": 30.0, "tracking_timeout_sec": 6.0},
        "rtl": {"timeout_sec": 60.0, "centering_timeout_sec": 6.0, "touchdown_timeout_sec": 8.0},
        "battery": {"land_pct": 20.0},
        "platform": {
            "height_m": PAD_HEIGHT_M,
            "edge_ahead_m": EDGE_M if guard else None,
            "venue_ceiling_agl_m": VENUE_AGL_M,
            "ceiling_allowance_m": ALLOWANCE_M,
        },
    }


def fly(tmp_path_factory, guard: bool, domain: int) -> FlightResult:
    workdir = tmp_path_factory.mktemp("totem_guard" if guard else "totem_bare")
    flight = EmulatedFlight(str(workdir), domain=domain, seed=1, emulator_args=EMULATOR_ARGS)
    with flight:
        return flight.fly(totem_params(guard), fly=True, timeout=300.0)


@pytest.fixture(scope="module")
def guarded(tmp_path_factory) -> FlightResult:
    return fly(tmp_path_factory, guard=True, domain=84)


@pytest.fixture(scope="module")
def unguarded(tmp_path_factory) -> FlightResult:
    return fly(tmp_path_factory, guard=False, domain=85)


# ------------------------------------------------------------------ helpers


def stage_window(result: FlightResult, stage: int) -> Tuple[float, float]:
    """Monotonic bounds of a stage, from its ``[STEP N:`` line to the next one."""
    starts = [line.mono for line in result.lines if f"[STEP {stage}:" in line.text]
    assert starts, f"stage {stage} never started"
    later = [line.mono for line in result.lines if line.mono > starts[0] and "[STEP " in line.text
             and f"[STEP {stage}:" not in line.text]
    return starts[0], (min(later) if later else math.inf)


def trajectory(result: FlightResult, window: Optional[Tuple[float, float]] = None) -> List[Dict]:
    records = [r for r in result.emulator if r["ev"] == "traj"]
    if window is None:
        return records
    low, high = window
    return [r for r in records if low <= r["mono"] < high]


def platform_transitions(result: FlightResult) -> List[Tuple[float, str]]:
    """``[PLATFORM state=...]`` transitions after the first classification.

    The first line is the monitor placing the airframe on the pad when the
    hover origin is frozen at the end of Stage 1, not a crossing.
    """
    found = []
    for line in result.lines:
        marker = line.text.find("[PLATFORM state=")
        if marker >= 0:
            found.append((line.mono, line.text[marker:].split()[1].split("=")[1]))
    assert found and found[0][1] == "ON", found
    return found[1:]


def exit_loss(result: FlightResult) -> float:
    """Largest drop below the relative target once off the pad to the left, in Stage 2, metres."""
    off_pad = [r["z"] for r in trajectory(result, stage_window(result, 2)) if r["y"] > EDGE_M]
    assert off_pad, "Stage 2 never left the pad"
    return max(0.0, RELATIVE_TARGET_M - min(off_pad))


def re_entry_peak(result: FlightResult) -> float:
    """Highest relative altitude from the ``ENTERING`` transition to the end of Stage 5, metres."""
    entering = [mono for mono, state in platform_transitions(result) if state == "ENTERING"]
    assert entering, "the return never passed through ENTERING"
    low5, high5 = stage_window(result, 5)
    window = [r["z"] for r in trajectory(result, (max(low5, entering[0]), high5))]
    assert window, "no trajectory after ENTERING"
    return max(window)


# ------------------------------------------------------------------ guarded flight


def test_the_pad_flight_completes_every_stage_without_new_milestones(guarded):
    assert guarded.exit_code == 0, f"flight failed with exit {guarded.exit_code}:\n{guarded.tail()}"
    assert guarded.steps() == [1, 2, 3, 4, 5]
    assert {key for _mono, key, _payload in guarded.milestones()} <= set(MILESTONE_KEYS)


def test_the_takeoff_settles_on_the_relative_target_and_never_climbs_to_the_floor_target(guarded):
    stage1 = trajectory(guarded, stage_window(guarded, 1))
    assert max(r["z"] for r in stage1) < 1.0 + 0.1, "climbed past the firmware launch height"

    settle = [line for line in guarded.lines if "[PLATFORM departure_settle err=" in line.text]
    assert settle, "Stage 2 did not settle before departing"
    at_departure = [r for r in trajectory(guarded) if r["mono"] <= settle[0].mono][-1]
    assert at_departure["z"] == pytest.approx(RELATIVE_TARGET_M, abs=0.1)


def test_the_pad_flight_never_rises_past_the_venue_ceiling(guarded):
    venue_relative = VENUE_AGL_M - PAD_HEIGHT_M
    assert max(r["z"] for r in trajectory(guarded)) < venue_relative


def test_the_pad_edge_is_recognised_leaving_in_stage_2_and_entering_in_stage_5(guarded):
    transitions = platform_transitions(guarded)
    low2, high2 = stage_window(guarded, 2)
    low5, _ = stage_window(guarded, 5)

    stage2 = [state for mono, state in transitions if low2 <= mono < high2]
    stage5 = [state for mono, state in transitions if mono >= low5]
    assert stage2 == ["LEAVING", "OFF"], transitions
    assert stage5 == ["ENTERING", "ON"], transitions


def test_the_re_entry_false_climb_stays_under_the_effective_ceiling(guarded):
    assert not any("Altitude ceiling breached" in line.text for line in guarded.lines)
    effective = min(RELATIVE_TARGET_M + 0.6 + ALLOWANCE_M, VENUE_AGL_M - PAD_HEIGHT_M)
    stage5 = trajectory(guarded, stage_window(guarded, 5))
    assert max(r["z"] for r in stage5) < effective


def test_the_pad_is_left_to_the_left_and_re_entered_flying_right(guarded):
    stage2 = trajectory(guarded, stage_window(guarded, 2))
    stage5 = trajectory(guarded, stage_window(guarded, 5))
    assert max(r["y"] for r in stage2) > EDGE_M, "Stage 2 never crossed the pad edge to the left"
    assert stage5[0]["y"] > EDGE_M, "Stage 5 did not start off the pad"
    assert abs(stage5[-1]["y"]) < EDGE_M, "Stage 5 did not come back over the pad"


def test_the_return_from_the_right_re_enters_through_entering_without_an_emergency(guarded):
    """Lateral re-entry with plant integration: guard engaged, no breach, no failsafe landing."""
    entering = [mono for mono, state in platform_transitions(guarded) if state == "ENTERING"]
    assert entering, "the return never passed through ENTERING"
    low5, _ = stage_window(guarded, 5)
    assert entering[0] >= low5
    assert not any("Altitude ceiling breached" in line.text for line in guarded.lines)
    assert not any("FAILSAFE ENGAGED" in line.text for line in guarded.lines)

    effective = min(RELATIVE_TARGET_M + 0.6 + ALLOWANCE_M, VENUE_AGL_M - PAD_HEIGHT_M)
    peak = re_entry_peak(guarded)
    print(f"re-entry peak: {peak:.3f} m, false climb {peak - RELATIVE_TARGET_M:+.3f} m, "
          f"effective ceiling {effective:.3f} m")
    assert peak < effective


def test_the_landing_is_on_the_pad_top(guarded):
    records = trajectory(guarded)
    landed = records[-1]
    ground = next(r["odom_z"] for r in records if r["odom_z"] is not None)
    touchdowns = [payload for _mono, key, payload in guarded.milestones() if key == "mission.touchdown"]
    odometric = "na" if landed["odom_z"] is None else f"{landed['odom_z'] - ground:+.3f} m"
    print(f"landing: z_rel {landed['z']:+.3f} m (odometric {odometric}) "
          f"at y {landed['y']:+.3f} m, x {landed['x']:+.3f} m")
    assert touchdowns and touchdowns[-1]["at_base"] is True, touchdowns
    assert max(abs(landed["x"]), abs(landed["y"])) < EDGE_M
    assert landed["z"] == pytest.approx(0.0, abs=0.05)


def test_the_guard_keeps_the_exit_sink_inside_the_target(guarded, unguarded):
    guarded_loss = exit_loss(guarded)
    unguarded_loss = exit_loss(unguarded)
    print(f"exit loss: guard {guarded_loss:.3f} m, no guard {unguarded_loss:.3f} m")
    assert guarded_loss < unguarded_loss
    assert guarded_loss <= MAX_EXIT_LOSS_M
