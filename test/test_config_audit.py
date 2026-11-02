"""Unit tests for the boot-time configuration envelope audit."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from typing import List

import pytest

from mvp_mission_bebop.config_audit import (
    ENVELOPE_TAG,
    audit_safe_envelopes,
    log_envelope_divergences,
)
from mvp_mission_bebop.parameters import MissionParameters

#: OpenCV's DICT_APRILTAG_36h11 enum, returned by a resolver that accepts it.
APRILTAG_36H11 = 20

ENVELOPE_LINE = re.compile(r"\[CONFIG ENVELOPE\] (\{.*\})$")


def accepting(_marker_dict):
    return APRILTAG_36H11


def refusing(_marker_dict):
    return None


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records():
    handler = Records()
    target = logging.getLogger("ConfigAudit")
    target.addHandler(handler)
    yield handler
    target.removeHandler(handler)


def paths(found) -> List[str]:
    return [d.path for d in found]


def test_the_shipped_defaults_are_inside_every_envelope():
    assert audit_safe_envelopes(MissionParameters(), accepting) == []


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("gimbal", "nadir_tilt_deg", -95.0),
        ("gimbal", "nadir_tilt_deg", 25.0),
        ("kinematics", "target_altitude_m", 0.30),
        ("kinematics", "target_altitude_m", 0.10),
        ("kinematics", "altitude_ceiling_margin_m", 0.0),
        ("kinematics", "altitude_ceiling_margin_m", 0.20),
        ("kinematics", "altitude_ceiling_margin_m", float("nan")),
        ("kinematics", "target_altitude_m", "high"),
        ("kinematics", "max_horizontal_speed", 0.15),
        ("kinematics", "max_horizontal_speed", 1.0),
        ("rtl", "target_aruco_id", -1),
        ("rtl", "target_aruco_id", 587),
        ("rtl", "target_aruco_id", "8"),
        ("rtl", "tag_size", 0.0),
        ("rtl", "camera_tilt_deg", -90.0),
        ("rtl", "camera_tilt_deg", 5.0),
        ("rtl", "reverse_cruise_velocity", 0.10),
        ("rtl", "landing_radius_m", 0.02),
        ("rtl", "centering_timeout_sec", 0.0),
        ("rtl", "centering_timeout_sec", -5.0),
        ("rtl", "centering_timeout_sec", float("nan")),
        ("kinematics", "climb_position_kp", 0.0),
        ("kinematics", "climb_position_kp", 4.5),
        ("kinematics", "climb_lag_sec", -0.1),
        ("kinematics", "climb_lag_sec", 1.5),
        ("rtl", "camera_offset_forward_m", 0.35),
        ("rtl", "camera_offset_left_m", -0.4),
        ("rtl", "commit_height_m", 0.1),
        ("rtl", "commit_height_m", 0.9),
        ("rtl", "descent_speed_mps", 0.3),
        ("rtl", "descent_gate_radius_m", 0.02),
        ("timeouts", "video_stream_timeout_sec", 8.0),
        ("timeouts", "video_stream_timeout_sec", 1.0),
        ("timeouts", "video_stream_timeout_sec", float("nan")),
    ],
)
def test_a_value_outside_its_documented_envelope_is_reported(section, field, value):
    params = MissionParameters()
    setattr(getattr(params, section), field, value)

    assert paths(audit_safe_envelopes(params, accepting)) == [f"{section}.{field}"]


def test_the_ceiling_margin_floor_is_the_climb_band_plus_twice_the_settle_sigma():
    """climb_deadband_m 0.05 + 2 * climb_settle_max_position_sigma_m 0.08 = 0.21 m."""
    params = MissionParameters()
    params.kinematics.altitude_ceiling_margin_m = 0.21
    assert audit_safe_envelopes(params, accepting) == []

    params.kinematics.altitude_ceiling_margin_m = 0.209
    assert paths(audit_safe_envelopes(params, accepting)) == ["kinematics.altitude_ceiling_margin_m"]


def pad_top(target: float, margin: float, venue=None) -> MissionParameters:
    params = MissionParameters()
    params.kinematics.target_altitude_m = target
    params.kinematics.altitude_ceiling_margin_m = margin
    params.platform.height_m = 1.0
    params.platform.takeoff_from_ground = False
    params.platform.venue_ceiling_agl_m = venue
    return params


def test_the_pad_top_margin_is_judged_once_unlocked_above_2_0_m():
    assert "kinematics.altitude_ceiling_margin_m" in paths(audit_safe_envelopes(pad_top(2.5, 0.1), accepting))
    assert "kinematics.altitude_ceiling_margin_m" not in paths(audit_safe_envelopes(pad_top(2.0, 0.1), accepting))


def test_a_3_0_m_pad_top_target_under_a_3_2_m_venue_is_reported_as_expected():
    """Target 3.0 on a 1.0 m pad: the ceiling is cut at 2.2 m relative, 0.2 m over the target (D4)."""
    found = paths(audit_safe_envelopes(pad_top(3.0, 0.6, venue=3.2), accepting))
    assert "platform.venue_ceiling_agl_m" in found
    assert "platform.venue_ceiling_agl_m" not in paths(audit_safe_envelopes(pad_top(2.0, 0.6, venue=3.2), accepting))


def test_a_horizontal_ceiling_below_the_cruise_is_still_reported():
    params = MissionParameters()
    params.kinematics.forward_cruise_velocity = 0.60
    params.rtl.reverse_cruise_velocity = -0.60
    params.kinematics.max_horizontal_speed = 0.30
    assert paths(audit_safe_envelopes(params, accepting)) == ["kinematics.max_horizontal_speed"]


def test_a_lifted_ceiling_clears_the_fastest_coupled_cruise():
    params = MissionParameters()
    params.kinematics.forward_cruise_velocity = 0.60
    params.rtl.reverse_cruise_velocity = -0.60
    params.kinematics.max_horizontal_speed = 0.65
    assert audit_safe_envelopes(params, accepting) == []


@pytest.mark.parametrize("search, nadir", [(0.0, -80.0), (-45.0, -80.0), (0.0, -1.0), (-30.0, -10.0), (-30.0, -30.0)])
def test_the_widened_gimbal_ranges_accept_upward_downward_and_fixed(search, nadir):
    params = MissionParameters()
    params.gimbal.search_tilt_deg = search
    params.gimbal.nadir_tilt_deg = nadir
    assert audit_safe_envelopes(params, accepting) == []

    params.gimbal.nadir_tilt_deg = -95.0
    assert paths(audit_safe_envelopes(params, accepting)) == ["gimbal.nadir_tilt_deg"]


def test_an_unresolvable_marker_dictionary_is_reported():
    params = MissionParameters()
    params.rtl.marker_dict = "DICT_NOT_A_FAMILY"

    assert "rtl.marker_dict" in paths(audit_safe_envelopes(params, refusing))


def test_the_marker_id_is_bounded_by_the_dictionary_size():
    """DICT_APRILTAG_36h11 holds 587 markers, IDs 0-586."""
    params = MissionParameters()
    params.rtl.target_aruco_id = 586
    assert audit_safe_envelopes(params, accepting) == []

    params.rtl.target_aruco_id = 587
    assert paths(audit_safe_envelopes(params, accepting)) == ["rtl.target_aruco_id"]


def test_divergences_are_logged_as_structured_warnings_and_never_raise(records):
    """The persisted 8.0 s video timeout of a pre-change config, among others."""
    params = MissionParameters()
    params.update_from_dict(
        {
            "timeouts": {"video_stream_timeout_sec": 8},
            "gimbal": {"nadir_tilt_deg": -95.0},
        }
    )

    found = log_envelope_divergences(params, accepting)

    warnings = [
        r for r in records.records if r.levelno == logging.WARNING and r.getMessage().startswith(f"[{ENVELOPE_TAG}] ")
    ]
    assert len(warnings) == len(found) == 2
    payloads = [json.loads(ENVELOPE_LINE.search(r.getMessage()).group(1)) for r in warnings]
    assert {p["path"] for p in payloads} == {
        "timeouts.video_stream_timeout_sec",
        "gimbal.nadir_tilt_deg",
    }
    video = next(p for p in payloads if p["path"] == "timeouts.video_stream_timeout_sec")
    assert video["value"] == 8
    assert video["envelope"] == "[1.5, 2]"
    assert all(r.getMessage().startswith(f"[{ENVELOPE_TAG}] ") for r in warnings)


def test_a_nominal_configuration_logs_no_warning(records):
    """Nominal as the station sends it: the return cruise coupled to the forward one."""
    params = MissionParameters()
    params.rtl.reverse_cruise_velocity = -params.kinematics.forward_cruise_velocity
    assert log_envelope_divergences(params, accepting) == []
    assert not [r for r in records.records if r.levelno >= logging.WARNING]


def test_a_failing_resolver_does_not_stop_the_mission(records):
    def broken(_marker_dict):
        raise RuntimeError("resolver fault")

    assert log_envelope_divergences(MissionParameters(), broken) == []
    assert any("audit failed" in r.getMessage() for r in records.records)


def test_non_finite_values_render_on_one_json_line():
    params = MissionParameters()
    params.timeouts.video_stream_timeout_sec = float("inf")
    (divergence,) = audit_safe_envelopes(params, accepting)
    line = divergence.as_log_line()
    assert "\n" not in line
    assert json.loads(ENVELOPE_LINE.search(line).group(1))["value"] == "inf"


def test_the_mission_boot_logs_a_divergent_persisted_value(tmp_path):
    """``mission.py`` audits the merged configuration before it initializes the SDK."""
    pytest.importorskip("rclpy")
    config = tmp_path / "mission_config.json"
    persisted = MissionParameters()
    persisted.timeouts.video_stream_timeout_sec = 8.0
    persisted.save_to_file(str(config))

    program = (
        "import sys, mvp_mission_bebop.mission as m\n"
        "class Stop(Exception): pass\n"
        "def halt(*a, **k): raise Stop()\n"
        "m.nectar.init = halt\n"
        "sys.argv = ['mission.py', '--no-fly', '--config', sys.argv[1]]\n"
        "try:\n"
        "    m.main()\n"
        "except Stop:\n"
        "    pass\n"
    )
    repo = os.path.join(os.path.dirname(__file__), "..")
    completed = subprocess.run(
        [sys.executable, "-c", program, str(config)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    lines = [ENVELOPE_LINE.search(line) for line in completed.stdout.splitlines()]
    reported = {json.loads(m.group(1))["path"] for m in lines if m}
    assert "timeouts.video_stream_timeout_sec" in reported
