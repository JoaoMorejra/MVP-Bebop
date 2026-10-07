"""Launch from a pre-warmed mission (instant countdown).

Measured: 40 s at the station from the click to the first countdown tick, the
mission's start-up (SDK, driver link, camera, detector on the MX110) running
under the load of the driver, the bridges and the speech cache. The station now
keeps a mission prepared in ``--standby``; the click sends it the parameter
document and the click instant, and the takeoff is at that instant plus the
countdown, an absolute deadline both sides share.
"""

from __future__ import annotations

import json

import pytest

from mvp_mission_bebop.engine import launch
from mvp_mission_bebop.parameters import MissionParameters


def test_the_deadline_is_the_click_plus_the_countdown_on_the_monotonic_clock():
    deadline = launch.launch_deadline(launch_at_ms=1_000_000, countdown_sec=10.0, now_wall=1_000.4, now_mono=50.0)
    assert deadline == pytest.approx(59.6)


def test_a_click_in_the_future_is_clamped_to_now():
    deadline = launch.launch_deadline(launch_at_ms=2_000_000, countdown_sec=10.0, now_wall=1_000.0, now_mono=50.0)
    assert deadline == pytest.approx(60.0)


@pytest.mark.parametrize("value", [None, "now", -5, float("nan"), True])
def test_an_invalid_click_instant_is_refused(value):
    with pytest.raises((TypeError, ValueError)):
        launch.launch_deadline(launch_at_ms=value, countdown_sec=10.0, now_wall=1.0, now_mono=1.0)


def test_a_click_older_than_the_go_delay_pins_no_deadline():
    """Regression: a cold spawn reads the click after its own start-up.

    8 s of a 10 s countdown gone before the mission could count any of it: no
    deadline, so Stage 1 counts the whole countdown itself.
    """
    stale = launch.launch_deadline(launch_at_ms=1_000_000, countdown_sec=10.0, now_wall=1_008.0, now_mono=50.0)
    assert stale is None
    fresh = launch.launch_deadline(
        launch_at_ms=1_000_000, countdown_sec=10.0, now_wall=1_000.0 + launch.MAX_GO_DELAY_SEC, now_mono=50.0
    )
    assert fresh == pytest.approx(50.0 + 10.0 - launch.MAX_GO_DELAY_SEC)


def test_a_zero_countdown_pins_no_wait_even_late():
    assert launch.launch_deadline(launch_at_ms=1_000_000, countdown_sec=0.0, now_wall=1_000.1, now_mono=50.0) == 50.0


def test_a_go_command_is_parsed():
    doc = json.loads(json.dumps(MissionParameters().to_dict()))
    command = launch.parse_go_command(json.dumps({"op": "go", "params": doc, "launch_at_ms": 123}))
    assert command == {"op": "go", "params": doc, "launch_at_ms": 123}


@pytest.mark.parametrize(
    "line",
    ["", "garbage", "[1]", json.dumps({"op": "go"}), json.dumps({"op": "go", "params": [], "launch_at_ms": 1}),
     json.dumps({"op": "fly", "params": {}, "launch_at_ms": 1})],
)
def test_anything_else_is_refused(line):
    with pytest.raises(ValueError):
        launch.parse_go_command(line)


def test_quit_is_a_command():
    assert launch.parse_go_command('{"op": "quit"}') == {"op": "quit"}


def test_identical_documents_have_no_critical_mismatch():
    params = MissionParameters()
    assert launch.critical_mismatches(params, params.to_dict()) == []


def test_tuning_changes_are_not_critical():
    params = MissionParameters()
    doc = params.to_dict()
    doc["kinematics"]["target_altitude_m"] = 2.4
    doc["kinematics"]["countdown_sec"] = 5.0
    doc["gimbal"]["nadir_tilt_deg"] = -70.0
    assert launch.critical_mismatches(params, doc) == []


def test_start_up_fields_are_critical():
    params = MissionParameters()
    doc = params.to_dict()
    doc["no_fly"] = not params.no_fly
    doc["vision"]["inference_device"] = "IGPU"
    doc["network"]["camera_raw_topic"] = "/other/image"
    assert launch.critical_mismatches(params, doc) == [
        "no_fly",
        "network.camera_raw_topic",
        "vision.inference_device",
    ]


def test_device_and_input_size_are_compared_normalized():
    params = MissionParameters()
    params.vision.inference_device = "CUDA"
    params.vision.inference_imgsz = 480
    doc = params.to_dict()
    doc["vision"]["inference_device"] = "cuda"
    doc["vision"]["inference_imgsz"] = "480"
    assert launch.critical_mismatches(params, doc) == []


def test_the_ticker_counts_from_the_go_whatever_stage_one_is_doing():
    import time

    ticks = []
    ticker = launch.CountdownTicker(time.monotonic() + 2.2, lambda whole: ticks.append((round(time.monotonic(), 1), whole)))
    started = time.monotonic()
    ticker.start()
    time.sleep(2.5)
    ticker.stop()
    assert [whole for _, whole in ticks] == [3, 2, 1]
    assert ticks[0][0] - round(started, 1) < 0.2


def test_a_stopped_ticker_says_nothing_more():
    import time

    ticks = []
    ticker = launch.CountdownTicker(time.monotonic() + 5.0, ticks.append)
    ticker.start()
    time.sleep(0.1)
    ticker.stop()
    count = len(ticks)
    time.sleep(1.2)
    assert len(ticks) == count == 1


def test_the_ticker_says_whether_it_showed_the_countdown():
    import time

    late = launch.CountdownTicker(time.monotonic() - 1.0, lambda whole: None)
    late.start()
    time.sleep(0.1)
    late.stop()
    assert late.shown is False

    counted = launch.CountdownTicker(time.monotonic() + 1.0, lambda whole: None)
    counted.start()
    time.sleep(0.1)
    counted.stop()
    assert counted.shown is True
