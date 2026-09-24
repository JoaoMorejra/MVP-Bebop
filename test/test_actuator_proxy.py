"""Structural hardening of the yaw invariant at the single point every
velocity command in the mission passes through.

No known call site in `mvp_mission_bebop` currently passes a nonzero `vyaw` --
every guidance law and every step was audited for this (see the design spec,
Ponto 5). This is defense in depth: it makes a future regression impossible to
introduce silently, rather than fixing a bug that has been observed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Iterator, List

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy


class _Recorder(logging.Handler):
    """Captures the proxy logger's records.

    Attached directly rather than through ``caplog``, which this environment's
    pytest configuration does not deliver (see ``test_dead_reckoning``): with
    the fixture, the logged case would fail for no reason and the silent case
    would pass vacuously.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def recording(minimum: int = logging.ERROR) -> Iterator[List[str]]:
    handler = _Recorder()
    target = logging.getLogger("ActuatorProxy")
    target.addHandler(handler)
    messages: List[str] = []
    try:
        yield messages
    finally:
        target.removeHandler(handler)
        messages.extend(r.getMessage() for r in handler.records if r.levelno >= minimum)


class StubDrone:
    def __init__(self):
        self.calls = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append((vx, vy, vz, vyaw))


def test_yaw_is_clamped_to_zero_regardless_of_caller_intent():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    proxy.move_velocity(vx=0.2, vy=0.0, vz=0.0, vyaw=0.5)

    assert drone.calls == [(0.2, 0.0, 0.0, 0.0)]


def test_a_zero_yaw_command_is_unaffected_and_silent():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    with recording() as errors:
        proxy.move_velocity(vx=0.2, vy=0.0, vz=0.0, vyaw=0.0)

    assert drone.calls == [(0.2, 0.0, 0.0, 0.0)]
    assert not errors


def test_a_nonzero_yaw_command_is_logged_at_error():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    with recording() as errors:
        proxy.move_velocity(vyaw=0.3)

    assert any("vyaw" in message for message in errors)


class RecordingSimulator:
    def __init__(self):
        self.calls = []

    def command(self, vx, vy, vz, vyaw):
        self.calls.append((vx, vy, vz, vyaw))


def test_no_fly_mode_also_clamps_yaw_before_the_simulator():
    drone = StubDrone()
    simulator = RecordingSimulator()
    proxy = BenchtopDroneProxy(drone, no_fly=True, simulator=simulator)

    proxy.move_velocity(vx=0.1, vyaw=-0.4)

    assert drone.calls == [], "no-fly must not reach the real drone at all"
    assert simulator.calls == [(0.1, 0.0, 0.0, 0.0)], "the simulator integrated a yaw rate"
