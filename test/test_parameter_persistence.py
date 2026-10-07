"""Phase 5: the station's parameter document is the only source of a launch.

``--params-json`` is merged over ``mission_config.json`` and nothing else
overrides it; the station's defaults come from ``mission.py --dump-defaults``;
``kinematics.countdown_sec`` is taken literally, 0 included.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest

from mvp_mission_bebop.parameters import COUNTDOWN_MIN_SEC, MissionParameters

_MISSION = os.path.join(os.path.dirname(__file__), "..", "mvp_mission_bebop", "mission.py")


def resolve(monkeypatch, tmp_path, argv, stored=None):
    from mvp_mission_bebop import mission

    config = tmp_path / "mission_config.json"
    if stored is not None:
        config.write_text(json.dumps(stored))
    monkeypatch.setattr(sys, "argv", ["mission.py", "--config", str(config), *argv])
    args = mission.parse_arguments(MissionParameters())
    return mission.resolve_parameters(args), config


def test_the_factory_is_the_dataclass_defaults():
    assert MissionParameters.factory().to_dict() == MissionParameters().to_dict()


def test_dump_defaults_prints_the_factory_document_and_touches_nothing(tmp_path):
    completed = subprocess.run(
        [sys.executable, _MISSION, "--dump-defaults", "--config", str(tmp_path / "c.json")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    expected = json.loads(json.dumps(MissionParameters.factory().to_dict()))
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == expected
    assert list(tmp_path.iterdir()) == []


def test_the_station_document_is_merged_over_the_file_without_flags(monkeypatch, tmp_path):
    stored = {"kinematics": {"target_altitude_m": 1.8, "countdown_sec": 6.0}, "lateral_pid": {"kp": 0.9}}
    sent = {"kinematics": {"target_altitude_m": 2.2, "forward_cruise_velocity": 0.15}}
    params, config = resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", json.dumps(sent)], stored)

    assert params.kinematics.target_altitude_m == pytest.approx(2.2)
    assert params.kinematics.forward_cruise_velocity == pytest.approx(0.15)
    assert params.kinematics.countdown_sec == pytest.approx(6.0)
    assert params.lateral_pid.kp == pytest.approx(0.9)
    written = json.loads(config.read_text())
    assert written["kinematics"]["target_altitude_m"] == pytest.approx(2.2)


def test_a_zero_countdown_is_respected(monkeypatch, tmp_path):
    sent = {"kinematics": {"countdown_sec": 0}}
    params, _ = resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", json.dumps(sent)])
    assert params.kinematics.countdown_sec == 0.0


@pytest.mark.parametrize("value", [-1.0, "ten", None])
def test_a_countdown_below_the_floor_or_not_a_number_is_refused(monkeypatch, tmp_path, value):
    sent = {"kinematics": {"countdown_sec": value}}
    with pytest.raises(SystemExit):
        resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", json.dumps(sent)])


def test_a_non_finite_countdown_is_refused(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        resolve(monkeypatch, tmp_path, ["--no-fly", "--countdown", "nan"])


def test_the_countdown_default_and_floor():
    assert MissionParameters().kinematics.countdown_sec == pytest.approx(10.0)
    assert COUNTDOWN_MIN_SEC == 0.0
    assert math.isfinite(MissionParameters().kinematics.countdown_sec)


def test_default_invocation_arms_motors_even_if_store_had_no_fly_true(monkeypatch, tmp_path):
    """Running mission.py without flags arms the motors (no_fly=False) and persists no_fly=False."""
    stored = {"no_fly": True, "kinematics": {"target_altitude_m": 1.5}}
    params, config = resolve(monkeypatch, tmp_path, [], stored)
    assert params.no_fly is False
    written = json.loads(config.read_text())
    assert written["no_fly"] is False


def test_no_fly_flag_sets_no_fly_true_without_persisting_it(monkeypatch, tmp_path):
    """--no-fly enables benchtop mode for this run, but disk store remains no_fly=False."""
    params, config = resolve(monkeypatch, tmp_path, ["--no-fly"])
    assert params.no_fly is True
    written = json.loads(config.read_text())
    assert written["no_fly"] is False


def test_fly_flag_explicitly_arms_motors(monkeypatch, tmp_path):
    """--fly explicitly arms the motors."""
    params, config = resolve(monkeypatch, tmp_path, ["--fly"])
    assert params.no_fly is False
    written = json.loads(config.read_text())
    assert written["no_fly"] is False

