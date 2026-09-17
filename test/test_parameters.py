"""Unit tests for mission parameters configuration and serialization."""

import json
import tempfile

from mvp_mission_bebop.parameters import MissionParameters


def test_default_parameters_initialization():
    params = MissionParameters()
    assert params.network.drone_ip == "192.168.42.1"
    assert params.gimbal.search_tilt_deg == -20.0
    # The inspection attitude, moved from -80 so the drone frames the scene from
    # a standoff instead of overflying it. Pinned because it is the number the
    # whole Stage 3 geometry is calibrated against.
    assert params.gimbal.nadir_tilt_deg == -69.0
    assert params.gimbal.nadir_tilt_tolerance_deg == 1.0
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


def test_nadir_inference_threshold_is_configurable():
    """The forensic pass must not silently cap the operator's threshold.

    `NadirInspectionStep` used to compute `min(0.30, confidence_threshold)`,
    which meant every value above 0.30 set in the GCS had no effect at all on
    the only inference the evidence record depends on.
    """
    params = MissionParameters()
    assert params.inspection.nadir_confidence_threshold == 0.30

    params.update_from_dict({"inspection": {"nadir_confidence_threshold": 0.55}})
    assert params.inspection.nadir_confidence_threshold == 0.55
    assert params.to_dict()["inspection"]["nadir_confidence_threshold"] == 0.55


def test_nadir_arrival_gate_is_configurable():
    """Dwell and arrival tolerance were literals inside the tracking step."""
    params = MissionParameters()
    assert params.vision.alignment_dwell_cycles == 4
    assert params.vision.nadir_arrival_tolerance_factor == 2.0

    params.update_from_dict(
        {"vision": {"alignment_dwell_cycles": 9, "nadir_arrival_tolerance_factor": 3.5}}
    )
    assert params.vision.alignment_dwell_cycles == 9
    assert params.vision.nadir_arrival_tolerance_factor == 3.5


def test_save_to_file_is_atomic():
    """A reader must never observe a partially written configuration.

    The GCS polls this file to populate the parameter sheet and a mission
    rewrites it on every run, so the two do overlap in practice.
    """
    import os

    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, "mission_config.json")

        params = MissionParameters()
        params.kinematics.target_altitude_m = 1.42
        params.save_to_file(target)

        # No temporary is left behind, and the document parses whole.
        assert os.listdir(directory) == ["mission_config.json"]
        with open(target, "r", encoding="utf-8") as stream:
            assert json.load(stream)["kinematics"]["target_altitude_m"] == 1.42

        # Overwriting an existing file also leaves nothing behind.
        params.kinematics.target_altitude_m = 2.10
        params.save_to_file(target)
        assert os.listdir(directory) == ["mission_config.json"]
        assert MissionParameters.load_from_file(target).kinematics.target_altitude_m == 2.10
