"""Flight telemetry freezes when the driver stops, however well the ping answers.

The reported bug: with the aircraft powered and the host on its Wi-Fi, a dead
or wedged driver left battery, Wi-Fi signal, speed and altitude on their last
values. ``connected`` follows a ping to the aircraft's access point, which
keeps answering; the flight fields used to follow it too. These tests drive
the bridge's state through exactly that case -- ping reachable, /bebop/odom
silent -- with a controlled clock.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_BRIDGE = os.path.join(
    os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "telemetry_bridge.py"
)


@pytest.fixture
def bridge(monkeypatch):
    spec = importlib.util.spec_from_file_location("telemetry_bridge_freshness", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    clock = {"t": 1000.0}
    monkeypatch.setattr(module, "_now", lambda: clock["t"])
    module.clock = clock
    yield module
    sys.modules.pop(spec.name, None)


def flying(bridge):
    state = bridge.TelemetryState()
    state.set_link(True, "Bebop2-A035633", -48)
    state.set_drone_rssi(-41)
    state.set_flying_state(2)
    state.set_odom(speed=0.4, altitude=1.8, heading=0.3, position_xy=(2.0, -1.0))
    state.set_battery(63, "aircraft")
    return state


def test_a_streaming_driver_reports_every_flight_field(bridge):
    payload = flying(bridge).to_payload(None)

    assert payload["connected"] is True
    assert payload["data_fresh"] is True
    assert payload["battery_known"] is True and payload["battery_pct"] == 63
    assert payload["speed"] == 0.4 and payload["altitude"] == 1.8
    assert payload["wifi_signal_dbm"] == -41 and payload["wifi_ssid"] == "Bebop2-A035633"
    assert payload["flying_state"] == 2


def test_odometry_stopping_with_the_ping_alive_drops_every_flight_field(bridge):
    state = flying(bridge)
    bridge.clock["t"] += bridge.ODOM_STALE_SEC + 0.5

    payload = state.to_payload(None)

    assert payload["connected"] is True, "the host is still on the aircraft's network"
    assert payload["data_fresh"] is False
    assert payload["battery_known"] is False and payload["battery_pct"] == 0
    assert payload["speed"] == 0.0 and payload["altitude"] == 0.0 and payload["heading"] == 0.0
    assert payload["wifi_signal_dbm"] == -100 and payload["wifi_ssid"] == ""
    assert payload["flying_state"] is None
    assert payload["flying_state_label"] == "disconnected"
    assert payload["driver_running"] is False
    assert payload["east_m"] is None and payload["north_m"] is None


def test_a_dead_driver_hands_the_battery_to_the_console_probe_at_once(bridge):
    state = flying(bridge)
    assert state.battery_is_live_from_aircraft() is True
    bridge.clock["t"] += bridge.ODOM_STALE_SEC + 0.5

    assert state.battery_is_live_from_aircraft() is False, "the probe must not wait out 90 s"


def test_a_console_battery_reading_stays_valid_without_the_driver(bridge):
    state = flying(bridge)
    bridge.clock["t"] += bridge.ODOM_STALE_SEC + 0.5
    state.set_battery(61, "console")

    payload = state.to_payload(None)

    assert payload["battery_known"] is True and payload["battery_pct"] == 61
    assert payload["battery_source"] == "console"
    assert payload["speed"] == 0.0


def test_odometry_resuming_restores_the_flight_fields(bridge):
    state = flying(bridge)
    bridge.clock["t"] += bridge.ODOM_STALE_SEC + 0.5
    state.set_odom(speed=0.1, altitude=1.2, heading=0.0, position_xy=(0.0, 0.0))

    payload = state.to_payload(None)

    assert payload["data_fresh"] is True
    assert payload["altitude"] == 1.2
    assert payload["battery_known"] is True


def test_losing_the_network_still_drops_everything(bridge):
    state = flying(bridge)
    state.set_link(False, None, None)
    state.set_battery(60, "console")

    payload = state.to_payload(None)

    assert payload["connected"] is False
    assert payload["battery_known"] is False
    assert payload["altitude"] == 0.0


# ------------------------------------------------------------- base anchor


def _write_cache(path, age_sec, lat=-22.42, lng=-45.45):
    import json
    import time

    path.write_text(json.dumps({"latitude": lat, "longitude": lng, "at": (time.time() - age_sec) * 1000.0}))


def test_a_configured_site_overrides_the_cached_position(bridge, monkeypatch, tmp_path):
    cache = tmp_path / "operator-location.json"
    _write_cache(cache, age_sec=10)
    monkeypatch.setenv("BMG_BASE_FILE", str(cache))
    monkeypatch.setenv("BMG_BASE_LAT", "-23.6488913")
    monkeypatch.setenv("BMG_BASE_LNG", "-46.7187124")

    base = bridge.BaseResolver().current()

    assert (base.latitude, base.longitude, base.source) == (-23.6488913, -46.7187124, "configured")


def test_a_recent_cached_position_is_the_base(bridge, monkeypatch, tmp_path):
    cache = tmp_path / "operator-location.json"
    _write_cache(cache, age_sec=10 * 60)
    monkeypatch.setenv("BMG_BASE_FILE", str(cache))
    monkeypatch.delenv("BMG_BASE_LAT", raising=False)
    monkeypatch.delenv("BMG_BASE_LNG", raising=False)

    base = bridge.BaseResolver().current()

    assert base is not None and base.source == "operator-cache"


def test_a_cached_position_older_than_an_hour_is_not_a_base(bridge, monkeypatch, tmp_path):
    cache = tmp_path / "operator-location.json"
    _write_cache(cache, age_sec=2 * 3600)
    monkeypatch.setenv("BMG_BASE_FILE", str(cache))
    monkeypatch.delenv("BMG_BASE_LAT", raising=False)
    monkeypatch.delenv("BMG_BASE_LNG", raising=False)

    assert bridge.BaseResolver().current() is None


def test_a_cache_refreshed_after_expiring_is_read_again(bridge, monkeypatch, tmp_path):
    import os

    cache = tmp_path / "operator-location.json"
    _write_cache(cache, age_sec=2 * 3600)
    monkeypatch.setenv("BMG_BASE_FILE", str(cache))
    monkeypatch.delenv("BMG_BASE_LAT", raising=False)
    monkeypatch.delenv("BMG_BASE_LNG", raising=False)
    resolver = bridge.BaseResolver()
    assert resolver.current() is None

    _write_cache(cache, age_sec=5, lat=-23.0)
    os.utime(cache, (cache.stat().st_atime, cache.stat().st_mtime + 10))
    bridge.clock["t"] += resolver.RELOAD_SEC + 1

    assert resolver.current().latitude == -23.0
