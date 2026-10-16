"""Platform section of the configuration: the takeoff pad the mission flies above.

The requirement is stated over the floor (1.8 m), the control loop works over the
launch point (the top of a 1.0 m pad). ``relative_target_altitude_m`` is the one
place the two meet, and the safety ceiling has to agree with it and with the
venue's own ceiling.
"""

from __future__ import annotations

import json

import pytest

from mvp_mission_bebop.parameters import MissionParameters, PlatformConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


def pad_params(target: float = 1.8, height: float = 1.0) -> MissionParameters:
    params = MissionParameters()
    params.kinematics.target_altitude_m = target
    params.platform.height_m = height
    return params


def test_relative_target_is_the_height_above_the_pad():
    assert pad_params(1.8, 1.0).relative_target_altitude_m == pytest.approx(0.8)


def test_relative_target_without_a_pad_is_the_configured_target():
    params = pad_params(1.8, 0.0)
    assert params.relative_target_altitude_m == params.kinematics.target_altitude_m


def test_a_pad_leaving_too_little_relative_height_is_refused():
    with pytest.raises(ValueError, match="takeoff_settle_min_altitude_m"):
        pad_params(1.2, 1.0).relative_target_altitude_m


def test_a_low_target_without_a_pad_is_not_refused():
    """With no pad the relative target is the configured one, low targets included."""
    assert pad_params(0.20, 0.0).relative_target_altitude_m == pytest.approx(0.20)


def test_platform_defaults_leave_the_mission_unchanged():
    platform = MissionParameters().platform
    assert isinstance(platform, PlatformConfig)
    assert platform.height_m == 0.0
    assert platform.edge_ahead_m is None
    assert platform.venue_ceiling_agl_m is None
    assert platform.ceiling_allowance_m == 0.0
    assert platform.sag_feedforward_mps == 0.0
    assert platform.rise_feedforward_mps == 0.0


def test_platform_section_round_trips_through_json():
    original = pad_params(1.8, 1.0)
    original.platform.edge_ahead_m = 0.25
    original.platform.venue_ceiling_agl_m = 2.6
    document = json.loads(json.dumps(original.to_dict()))

    assert "platform" in document
    restored = MissionParameters()
    restored.update_from_dict(document)
    assert restored.platform == original.platform
    assert json.loads(json.dumps(restored.to_dict())) == document


def test_an_older_document_without_the_platform_section_loads_with_defaults():
    document = MissionParameters().to_dict()
    del document["platform"]
    restored = MissionParameters()
    restored.update_from_dict(document)
    assert restored.platform == PlatformConfig()


def supervisor_for(params: MissionParameters) -> OdometrySupervisor:
    return OdometrySupervisor(
        params.kinematics, params.timeouts, params.calibration, params.platform
    )


def test_ceiling_is_measured_above_the_pad():
    params = pad_params(1.8, 1.0)
    params.kinematics.altitude_ceiling_margin_m = 0.6
    assert supervisor_for(params).altitude_ceiling == pytest.approx(1.4)


def test_ceiling_is_clamped_by_the_venue():
    params = pad_params(1.8, 1.0)
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.venue_ceiling_agl_m = 2.2
    assert supervisor_for(params).altitude_ceiling == pytest.approx(1.2)


def test_a_venue_above_the_ceiling_does_not_raise_it():
    params = pad_params(1.8, 1.0)
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.ceiling_allowance_m = 0.5
    params.platform.venue_ceiling_agl_m = 2.6
    assert supervisor_for(params).altitude_ceiling == pytest.approx(1.4)


def test_ceiling_without_a_platform_section_is_unchanged():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.55
    params.kinematics.altitude_ceiling_margin_m = 0.6
    supervisor = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)
    assert supervisor.altitude_ceiling == pytest.approx(2.15)


def test_the_allowance_is_granted_only_inside_the_guard_and_never_past_the_venue():
    from mvp_mission_bebop.estimation.platform_monitor import PlatformReport, PlatformState

    def ceiling(state, venue):
        params = pad_params(1.8, 1.0)
        params.kinematics.altitude_ceiling_margin_m = 0.6
        params.platform.edge_ahead_m = 0.25
        params.platform.ceiling_allowance_m = 0.5
        params.platform.venue_ceiling_agl_m = venue
        odom = supervisor_for(params)
        if state is not None:
            guarded = state in (PlatformState.LEAVING, PlatformState.ENTERING)
            odom._platform_report = PlatformReport(state, guarded, 0.6, 0.54, -1, 0.0)
        return odom.altitude_ceiling

    assert ceiling(PlatformState.ENTERING, 2.6) == pytest.approx(1.6)
    assert ceiling(PlatformState.ENTERING, None) == pytest.approx(1.9)
    assert ceiling(PlatformState.OFF, 2.6) == pytest.approx(1.4)
    assert ceiling(None, 2.6) == pytest.approx(1.4)


# ------------------------------------------------------------- envelope audit

PLATFORM_FIELDS = {f for f in PlatformConfig.__dataclass_fields__}


def platform_divergences(params: MissionParameters):
    from mvp_mission_bebop.config_audit import audit_safe_envelopes

    return [
        d.path
        for d in audit_safe_envelopes(params, marker_resolver=lambda _dictionary: 20)
        if d.path.startswith("platform.")
    ]


def test_the_platform_defaults_are_inside_every_envelope():
    assert platform_divergences(MissionParameters()) == []


def test_the_pad_profile_is_inside_every_envelope():
    params = pad_params(1.8, 1.0)
    params.platform.edge_ahead_m = 0.25
    params.platform.venue_ceiling_agl_m = 2.8
    params.platform.ceiling_allowance_m = 0.4
    assert platform_divergences(params) == []


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("height_m", -0.1),
        ("height_m", "tall"),
        ("venue_ceiling_agl_m", 1.8),
        ("venue_ceiling_agl_m", 1.2),
        ("edge_ahead_m", 0.0),
        ("edge_ahead_m", -0.3),
        ("edge_uncertainty_m", -0.01),
        ("sonar_half_angle_deg", 5.0),
        ("sonar_half_angle_deg", 45.0),
        ("guard_lead_m", -0.1),
        ("guard_extent_m", 0.0),
        ("guard_climb_authority", 0.31),
        ("guard_climb_authority", -0.1),
        ("guard_descent_cap", 0.35),
        ("guard_kp_scale", 0.9),
        ("guard_kp_scale", 3.1),
        ("guard_deadband_m", -0.01),
        ("guard_stop_error_m", 0.08),
        ("guard_resume_error_m", 0.20),
        ("guard_vertical_shaper", "yes"),
        ("ceiling_allowance_m", -0.1),
        ("sag_feedforward_mps", -0.1),
        ("rise_feedforward_mps", -0.1),
        ("feedforward_sec", 0.0),
        ("departure_settle_window_sec", 0.0),
        ("departure_settle_timeout_sec", -1.0),
        ("reentry_breach_grace_sec", 0.0),
        ("reentry_breach_grace_sec", -2.0),
    ],
)
def test_each_platform_envelope_violation_is_reported(field_name, value):
    params = pad_params(1.8, 1.0)
    setattr(params.platform, field_name, value)
    found = platform_divergences(params)
    expected = (
        "platform.guard_stop_error_m"
        if field_name in ("guard_stop_error_m", "guard_resume_error_m")
        else f"platform.{field_name}"
    )
    assert expected in found


def test_a_venue_ceiling_close_above_the_safety_ceiling_is_reported():
    """target + margin = 2.4 m over the floor; a 2.5 m venue leaves 0.1 m of it."""
    params = pad_params(1.8, 1.0)
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.venue_ceiling_agl_m = 2.5
    assert platform_divergences(params) == ["platform.venue_ceiling_agl_m"]


def test_the_target_floor_is_judged_above_the_pad():
    from mvp_mission_bebop.config_audit import audit_safe_envelopes

    params = pad_params(1.25, 1.0)
    paths = [d.path for d in audit_safe_envelopes(params, marker_resolver=lambda _d: 20)]
    assert "kinematics.target_altitude_m" in paths
    assert "kinematics.target_altitude_m" not in [
        d.path for d in audit_safe_envelopes(pad_params(1.8, 1.0), marker_resolver=lambda _d: 20)
    ]


def test_the_station_store_loads_with_its_platform_section():
    import os

    path = os.path.join(
        os.path.dirname(__file__), "..", "mvp_mission_bebop", "mission_config.json"
    )
    if not os.path.exists(path):
        pytest.skip("mission_config.json is the station's runtime store and is not versioned")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)

    params = MissionParameters.load_from_file(path)

    assert isinstance(params.platform, PlatformConfig)
    assert set(document.get("platform", {})) <= PLATFORM_FIELDS
    assert params.relative_target_altitude_m > 0.0
