"""Start-up phase timing (7.5): ``[TIMING] phase=<name> ms=<n>`` per phase."""

from __future__ import annotations

import logging
import time

import pytest

from mvp_mission_bebop.engine.startup_timing import StartupTimer, process_age_ms


class Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def records():
    handler = Records()
    logger = logging.getLogger("StartupTiming")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


def test_a_phase_logs_its_duration(records):
    timer = StartupTimer()
    with timer.phase("camera_open"):
        time.sleep(0.02)
    assert len(records.messages) == 1
    name, ms = records.messages[0].split()[1:3]
    assert name == "phase=camera_open"
    assert 15 <= int(ms.split("=")[1]) < 1000


def test_a_failing_phase_is_still_logged(records):
    timer = StartupTimer()
    with pytest.raises(RuntimeError):
        with timer.phase("driver_connect"):
            raise RuntimeError("down")
    assert records.messages[0].startswith("[TIMING] phase=driver_connect ms=")


def test_a_measured_duration_can_be_recorded(records):
    StartupTimer().record("interpreter", 412.7)
    assert records.messages == ["[TIMING] phase=interpreter ms=413"]


@pytest.mark.parametrize("name", ["", "Camera", "two words", "x-y"])
def test_phase_names_are_lowercase_identifiers(name):
    with pytest.raises(ValueError):
        StartupTimer().record(name, 1.0)


def test_the_process_age_is_measured_from_its_start():
    age = process_age_ms()
    assert age is None or 0.0 <= age < 24 * 3600 * 1000.0


def test_laps_log_the_time_since_the_previous_one(records):
    timer = StartupTimer()
    time.sleep(0.02)
    timer.lap("parameters")
    time.sleep(0.03)
    timer.lap("nectar_init")
    first = int(records.messages[0].split("ms=")[1])
    second = int(records.messages[1].split("ms=")[1])
    assert records.messages[0].startswith("[TIMING] phase=parameters ")
    assert 15 <= first < 500 and 25 <= second < 500


@pytest.mark.parametrize(
    "driver_reachable, camera_publishers, expected",
    [(True, 1, 2.5), (None, 0, 2.5), (False, 1, 2.5), (False, 0, 0.2)],
)
def test_the_first_frame_wait_is_short_only_without_any_camera(driver_reachable, camera_publishers, expected):
    """Live: a rehearsal fed by bench_relay.py has a camera but no driver in its graph."""
    from mvp_mission_bebop.mission import first_frame_timeout

    assert first_frame_timeout(driver_reachable, camera_publishers) == expected
