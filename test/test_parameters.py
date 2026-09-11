"""Unit tests for mission parameters configuration and serialization."""

import json
import tempfile

from mvp_mission_bebop.parameters import MissionParameters


def test_default_parameters_initialization():
    params = MissionParameters()
    assert params.network.drone_ip == "192.168.42.1"
    assert params.gimbal.search_tilt_deg == -20.0
    assert params.gimbal.nadir_tilt_deg == -80.0
    assert params.kinematics.target_altitude_m == 1.00
    assert params.vision.confidence_threshold == 0.50
    assert params.no_fly is False


def test_parameters_dictionary_roundtrip():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.80
    params.vision.confidence_threshold = 0.65

    dumped = params.to_dict()
    assert isinstance(dumped, dict)
    assert dumped["kinematics"]["target_altitude_m"] == 1.80
    assert dumped["vision"]["confidence_threshold"] == 0.65

    reloaded = MissionParameters()
    reloaded.update_from_dict(dumped)
    assert reloaded.kinematics.target_altitude_m == 1.80
    assert reloaded.vision.confidence_threshold == 0.65


def test_parameters_file_persistence():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        params = MissionParameters()
        params.kinematics.forward_cruise_velocity = 0.08
        params.save_to_file(temp_path)

        with open(temp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["kinematics"]["forward_cruise_velocity"] == 0.08

        loaded = MissionParameters.load_from_file(temp_path)
        assert loaded.kinematics.forward_cruise_velocity == 0.08
    finally:
        import os
        if os.path.exists(temp_path):
            os.remove(temp_path)
