"""Telemetry the station shows is what the aircraft reported, and when (6.2).

Freshness from the source timestamp rather than the arrival time, GPS gated
like every other flight field, the home and the altitude reference reset per
mission from ``/bebop/mission/ground_reference``, roll/pitch and the sonar on
the wire, and the battery's age from its own stamp.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import types

import pytest

_BRIDGE = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "telemetry_bridge.py")


@pytest.fixture
def bridge(monkeypatch):
    spec = importlib.util.spec_from_file_location("telemetry_bridge_truth", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    clock = {"t": 1000.0, "wall": 1_800_000_000.0}
    monkeypatch.setattr(module, "_now", lambda: clock["t"])
    monkeypatch.setattr(module, "_wall", lambda: clock["wall"])
    module.clock = clock
    yield module
    sys.modules.pop(spec.name, None)


def linked(bridge, *, stamp_age=0.1, raw_z=1.9):
    state = bridge.TelemetryState()
    state.set_link(True, "Bebop2-A035633", -48)
    state.set_flying_state(2)
    state.set_odom(
        speed=0.4,
        altitude=abs(raw_z),
        heading=0.0,
        position_xy=(2.0, -1.0),
        raw_z=raw_z,
        stamp=bridge.clock["wall"] - stamp_age,
        roll_deg=3.0,
        pitch_deg=-2.0,
    )
    return state


def test_a_sample_stamped_too_long_ago_is_not_fresh_however_recently_it_arrived(bridge):
    assert linked(bridge, stamp_age=0.5).to_payload(None)["data_fresh"] is True
    payload = linked(bridge, stamp_age=bridge.STAMP_STALE_SEC + 0.2).to_payload(None)
    assert payload["data_fresh"] is False
    assert payload["altitude"] == 0.0 and payload["speed"] == 0.0


def test_the_odometry_age_comes_from_the_stamp(bridge):
    payload = linked(bridge, stamp_age=0.8).to_payload(None)
    assert payload["odom_age_sec"] == pytest.approx(0.8, abs=0.01)


def test_gps_coordinates_follow_the_same_gating_as_the_fix(bridge):
    state = linked(bridge)
    state.set_gps(True, -22.4, -45.4)
    assert state.to_payload(None)["position_source"] == "gps"
    bridge.clock["wall"] += 5.0
    bridge.clock["t"] += 5.0
    payload = state.to_payload(None)
    assert payload["gps_fix"] is False
    assert payload["position_source"] != "gps"
    assert payload["latitude"] == 0.0 and payload["longitude"] == 0.0


def test_each_mission_takes_its_own_home(bridge):
    state = linked(bridge)
    state.set_gps(True, -22.4, -45.4)
    assert state.to_payload(None)["base_latitude"] == pytest.approx(-22.4)
    state.set_ground_reference(0.1)
    assert state.to_payload(None)["base_source"] != "gps"
    state.set_gps(True, -22.5, -45.5)
    assert state.to_payload(None)["base_latitude"] == pytest.approx(-22.5)


def test_the_altitude_is_relative_to_the_mission_z0(bridge):
    state = linked(bridge, raw_z=1.9)
    payload = state.to_payload(None)
    assert payload["altitude"] == pytest.approx(1.9)
    assert payload["altitude_reference"] == "odometry"
    state.set_ground_reference(0.1)
    payload = state.to_payload(None)
    assert payload["altitude"] == pytest.approx(1.8)
    assert payload["altitude_reference"] == "mission"


def test_roll_pitch_and_sonar_reach_the_station(bridge):
    state = linked(bridge)
    state.set_sonar_altitude(1.75)
    payload = state.to_payload(None)
    assert payload["roll_deg"] == pytest.approx(3.0)
    assert payload["pitch_deg"] == pytest.approx(-2.0)
    assert payload["sonar_altitude"] == pytest.approx(1.75)


def test_roll_and_pitch_are_absent_without_fresh_data(bridge):
    payload = linked(bridge, stamp_age=5.0).to_payload(None)
    assert payload["roll_deg"] is None and payload["pitch_deg"] is None


def test_the_battery_age_is_the_age_of_its_report(bridge):
    state = linked(bridge)
    state.set_battery(63, "aircraft", stamp=bridge.clock["wall"] - 30.0)
    assert state.to_payload(None)["battery_age_sec"] == pytest.approx(30.0, abs=0.1)


def test_the_driver_reporting_the_link_down_drops_the_flight_fields_at_once(bridge):
    state = linked(bridge)
    state.set_driver_link(False)
    payload = state.to_payload(None)
    assert payload["data_fresh"] is False
    assert payload["flying_state"] is None
    state.set_driver_link(True)
    assert state.to_payload(None)["data_fresh"] is True


def _quaternion(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return types.SimpleNamespace(
        w=cr * cp * cy + sr * sp * sy,
        x=sr * cp * cy - cr * sp * sy,
        y=cr * sp * cy + sr * cp * sy,
        z=cr * cp * sy - sr * sp * cy,
    )


def test_roll_and_pitch_are_decoded_from_the_odometry_quaternion(bridge):
    msg = types.SimpleNamespace(pose=types.SimpleNamespace(pose=types.SimpleNamespace(orientation=_quaternion(math.radians(5), math.radians(-3), 1.0))))
    roll, pitch = bridge.decode_attitude(msg)
    assert roll == pytest.approx(5.0, abs=1e-6)
    assert pitch == pytest.approx(-3.0, abs=1e-6)


def test_a_header_stamp_is_read_in_seconds(bridge):
    header = types.SimpleNamespace(stamp=types.SimpleNamespace(sec=1_800_000_000, nanosec=500_000_000))
    assert bridge.stamp_seconds(header) == pytest.approx(1_800_000_000.5)
    zero = types.SimpleNamespace(stamp=types.SimpleNamespace(sec=0, nanosec=0))
    assert bridge.stamp_seconds(zero) is None


def test_the_magnetometer_calibration_is_forwarded_as_reported(bridge):
    state = linked(bridge)
    assert state.to_payload(None)["magneto_calibration"] is None
    state.set_magneto_calibration(
        '{"sequence":2,"kind":"state","x":1,"y":0,"z":0,"failed":0,"required":1,"axis":"y","started":1,"stamp":1.0}'
    )
    magneto = state.to_payload(None)["magneto_calibration"]
    assert magneto["x"] == 1 and magneto["axis"] == "y" and magneto["started"] == 1
    state.set_magneto_calibration("not json")
    assert state.to_payload(None)["magneto_calibration"]["sequence"] == 2
