"""Executable guards on the interfaces the Electron GCS depends on.

Everything asserted here is consumed by ``bebop_mission_control`` by exact
string match, so a rename that looks harmless inside this package breaks the
ground station silently -- the launch fails with an argparse exit code nobody
reads, or the step indicator simply never advances. These are contracts, not
conventions, and they are pinned here because nothing else in the codebase says
so.
"""

import json
import os
import re
import sys
import tempfile

import numpy as np
import pytest

from mvp_mission_bebop.blackboard import EvidenceRecord
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import (
    ClosedLoopRTLStep,
    ForwardSearchStep,
    NadirInspectionStep,
    TakeoffStep,
    VisualServoingStep,
)

# electron/main.cjs:698-716 matches these substrings on the mission's stdout.
EXPECTED_STEP_PREFIXES = {
    1: TakeoffStep,
    2: ForwardSearchStep,
    3: VisualServoingStep,
    4: NadirInspectionStep,
    5: ClosedLoopRTLStep,
}


# ------------------------------------------------------------- step wire format


@pytest.mark.parametrize("number,step_class", sorted(EXPECTED_STEP_PREFIXES.items()))
def test_step_names_carry_the_prefix_the_gcs_matches(number, step_class):
    name = step_class().name
    assert name.startswith(f"STEP {number}: "), (
        f"{step_class.__name__}.name is a wire format: electron/main.cjs matches "
        f"'[STEP {number}:' on the log line built from it."
    )


def test_step_log_line_contains_the_matched_substring():
    """The GCS matches '[STEP N:' including the bracket, on the log message."""
    for number, step_class in EXPECTED_STEP_PREFIXES.items():
        rendered = f"--- [{step_class().name}] ---"
        assert f"[STEP {number}:" in rendered


def test_step_lines_reach_stdout_not_stderr():
    """The step matcher is registered on the child's stdout pipe only.

    ``logging.basicConfig`` defaults to stderr, which is why ``bmg:step-change``
    never fired. Checked in a subprocess because the assertion is about which
    real pipe the line lands on, and pytest replaces both in-process.
    """
    import subprocess

    program = (
        "import logging, mvp_mission_bebop.mission;"
        "from mvp_mission_bebop.steps import TakeoffStep;"
        "logging.getLogger('t').info('--- [%s] ---', TakeoffStep().name)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    assert "[STEP 1:" in completed.stdout, (
        f"step line must reach stdout; stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )


# ------------------------------------------------------------------ parameters


def test_to_dict_is_callable_the_way_the_gcs_calls_it():
    """electron/main.cjs:602-604 shells out to exactly this expression."""
    dumped = MissionParameters().to_dict()
    assert isinstance(dumped, dict)
    json.dumps(dumped)  # must be serializable; the GCS pipes it through JSON


@pytest.mark.parametrize(
    "path",
    [
        ("kinematics", "target_altitude_m"),
        ("kinematics", "forward_cruise_velocity"),
        ("kinematics", "hover_duration_sec"),
        ("kinematics", "countdown_sec"),
        ("timeouts", "search_timeout_sec"),
        ("vision", "confidence_threshold"),
        ("vision", "confirmation_frames"),
        ("vision", "target_classes"),
        ("rtl", "arrival_radius_m"),
    ],
)
def test_configuration_keys_the_renderer_reads_still_exist(path):
    """MissionLaunchScreen.tsx reads these nested keys by name."""
    node = MissionParameters().to_dict()
    for segment in path:
        assert segment in node, f"{'.'.join(path)} is read by the GCS"
        node = node[segment]


def test_top_level_no_fly_key_exists():
    assert "no_fly" in MissionParameters().to_dict()


def test_params_json_payload_from_the_gcs_is_accepted():
    """The exact shape MissionLaunchScreen.tsx:114-133 sends."""
    payload = {
        "kinematics": {
            "target_altitude_m": 1.4,
            "forward_cruise_velocity": 0.18,
            "hover_duration_sec": 6.0,
            "countdown_sec": 8.0,
        },
        "timeouts": {"search_timeout_sec": 25.0},
        "vision": {
            "confidence_threshold": 0.6,
            "confirmation_frames": 4,
            "target_classes": ["motorcycle"],
        },
        "rtl": {"arrival_radius_m": 0.18},
        "no_fly": True,
    }

    params = MissionParameters()
    params.update_from_dict(payload)

    assert params.kinematics.target_altitude_m == 1.4
    assert params.kinematics.countdown_sec == 8.0
    assert params.timeouts.search_timeout_sec == 25.0
    assert params.vision.confirmation_frames == 4
    assert params.vision.target_classes == ["motorcycle"]
    assert params.rtl.arrival_radius_m == 0.18
    assert params.no_fly is True


def test_unknown_keys_are_ignored_so_older_payloads_keep_working():
    params = MissionParameters()
    params.update_from_dict({"kinematics": {"nonexistent": 1}, "made_up_section": {"x": 2}})
    assert params.kinematics.target_altitude_m == MissionParameters().kinematics.target_altitude_m


def test_output_dir_defaults_to_the_working_directory():
    """The GCS launches with cwd=MISSION_DIR and watches that same directory."""
    assert MissionParameters().output_dir == "."


def test_network_topics_match_the_streamer_bridges():
    network = MissionParameters().network
    assert network.camera_raw_topic == "/bebop/camera/image_raw"
    assert network.detection_stream_topic == "/bebop/camera/detections"
    assert network.odometry_topic == "/bebop/odom"


def test_config_round_trips_through_disk():
    """mission.py rewrites mission_config.json on every run; the GCS reads it."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        path = handle.name
    try:
        params = MissionParameters()
        params.vision.confirmation_frames = 7
        params.save_to_file(path)

        with open(path, "r", encoding="utf-8") as handle:
            on_disk = json.load(handle)
        assert on_disk["vision"]["confirmation_frames"] == 7

        assert MissionParameters.load_from_file(path).vision.confirmation_frames == 7
    finally:
        os.unlink(path)


# -------------------------------------------------------------------- evidence


def evidence_writer(output_dir):
    """A MissionContext with only the attributes the evidence path touches.

    Built without the constructor on purpose: a real context needs a drone, a
    detector, a camera handler and a live ROS node, none of which this contract
    depends on.
    """
    context = object.__new__(MissionContext)
    context.params = MissionParameters()
    context.params.output_dir = output_dir
    context.frame_width = 856
    context.frame_height = 480
    context.current_tilt_deg = -80.0
    context.start_time = 0.0
    return context


def test_evidence_filenames_match_what_the_watcher_expects():
    """electron/main.cjs:354-384 derives one name from the other by substitution."""
    frame = np.full((480, 856, 3), 128, dtype=np.uint8)
    with tempfile.TemporaryDirectory() as directory:
        record = evidence_writer(directory).record_photographic_evidence(frame, frame)

        raw = os.path.basename(record.raw_path)
        annotated = os.path.basename(record.annotated_path)

        assert re.fullmatch(r"accident_raw_\d{8}_\d{6}\.png", raw), raw
        assert re.fullmatch(r"accident_inspected_\d{8}_\d{6}\.jpg", annotated), annotated

        # The watcher rebuilds the second name from the first; the timestamps
        # must therefore be identical, not merely close.
        assert raw.replace("accident_raw_", "").replace(".png", "") == annotated.replace(
            "accident_inspected_", ""
        ).replace(".jpg", "")


def test_evidence_files_clear_the_watcher_size_gate():
    """A capture under 1000 bytes is discarded by the GCS (main.cjs:369)."""
    frame = np.random.randint(0, 255, (480, 856, 3), dtype=np.uint8)
    with tempfile.TemporaryDirectory() as directory:
        record = evidence_writer(directory).record_photographic_evidence(frame, frame)
        assert os.path.getsize(record.raw_path) > 1000
        assert os.path.getsize(record.annotated_path) > 1000


def test_no_partial_files_are_left_matching_the_watcher_prefixes():
    """Writes are atomic, and the temporary name cannot match the prefixes."""
    frame = np.full((480, 856, 3), 64, dtype=np.uint8)
    with tempfile.TemporaryDirectory() as directory:
        evidence_writer(directory).record_photographic_evidence(frame, frame)
        stray = [
            name
            for name in os.listdir(directory)
            if name.startswith(("accident_raw_", "accident_inspected_"))
            and not name.endswith((".png", ".jpg"))
        ]
        assert not stray


def test_metadata_sidecar_does_not_trip_the_evidence_watcher():
    """It must not start with a prefix the GCS treats as an image."""
    frame = np.full((480, 856, 3), 64, dtype=np.uint8)
    with tempfile.TemporaryDirectory() as directory:
        record = evidence_writer(directory).record_photographic_evidence(frame, frame)
        name = os.path.basename(record.metadata_path)
        assert not name.startswith("accident_raw_")
        assert not name.startswith("accident_inspected_")

        with open(record.metadata_path, "r", encoding="utf-8") as handle:
            json.load(handle)


def test_evidence_record_reports_capture_state():
    assert not EvidenceRecord().captured
    assert EvidenceRecord(raw_path="x.png").captured


# ------------------------------------------------------------------- announcer


def test_announcer_symbols_the_gcs_invokes_directly_exist():
    """electron/main.cjs:767-771 runs this exact import in a python3 -c call."""
    from mvp_mission_bebop.telemetry.announcer import announce_sync, get_announcer

    assert callable(announce_sync)
    assert callable(get_announcer)

    import inspect

    signature = inspect.signature(announce_sync)
    assert "priority" in signature.parameters
    assert "wait" in signature.parameters


# ------------------------------------------------------------------------- CLI


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--height", "1.5"),
        ("--velocity", "0.2"),
        ("--search-timeout", "30"),
        ("--hover-duration", "7"),
        ("--confidence", "0.5"),
        ("--arrival-radius", "0.15"),
        ("--model-path", "yolov8n.pt"),
        ("--ip", "192.168.42.1"),
        ("--countdown", "10.0"),
    ],
)
def test_cli_flags_the_gcs_passes_are_accepted(flag, value, monkeypatch):
    """electron/main.cjs:658-677 always spawns with these nine flags."""
    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py", flag, value])
    parse_arguments(MissionParameters())


def test_no_fly_flag_is_still_accepted(monkeypatch):
    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py", "--no-fly"])
    assert parse_arguments(MissionParameters()).no_fly is True


def test_params_json_flag_is_still_accepted(monkeypatch):
    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py", "--params-json", '{"no_fly": true}'])
    arguments = parse_arguments(MissionParameters())
    assert json.loads(arguments.params_json)["no_fly"] is True


def test_bench_flags_default_to_the_gcs_behaviour(monkeypatch):
    """The GCS passes neither flag; both must leave the historical paths intact."""
    from mvp_mission_bebop.mission import parse_arguments

    monkeypatch.setattr(sys, "argv", ["mission.py", "--no-fly"])
    arguments = parse_arguments(MissionParameters())
    assert arguments.config is None
    assert arguments.bench_frame is None

    monkeypatch.setattr(
        sys, "argv", ["mission.py", "--config", "/tmp/c.json", "--bench-frame", "/tmp/f.jpg"]
    )
    arguments = parse_arguments(MissionParameters())
    assert (arguments.config, arguments.bench_frame) == ("/tmp/c.json", "/tmp/f.jpg")


# ------------------------------------------------------------------ milestones

#: The pattern the GCS matches milestone lines with (spec 2026-09-24, section 3.2).
MILESTONE_LINE = re.compile(r"\[MILESTONE ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$")

#: The GCS stage matcher, verbatim from ``electron/main.cjs``.
STEP_LINE = re.compile(r"\[STEP ([1-5]):")

#: Pool keys of the synchronisation table (spec section 4) that the mission
#: process raises. ``mission.start`` and ``mission.countdown_3`` belong to the GCS.
SPEC_MILESTONE_KEYS = (
    "mission.takeoff",
    "mission.scan_start",
    "mission.target_found",
    "mission.approaching",
    "mission.capture_done",
    "mission.rtl_start",
    "mission.landing",
)


def test_the_emitted_keys_are_exactly_the_spec_pool_keys():
    from mvp_mission_bebop.telemetry.milestones import MILESTONE_KEYS

    assert MILESTONE_KEYS == SPEC_MILESTONE_KEYS


def test_a_milestone_line_is_matched_by_the_gcs_and_never_as_a_step():
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    line = encode_milestone("mission.takeoff", {"altitude_m": 1.2})
    rendered = f"2026-09-24 17:57:31 [INFO] [Milestone] {line}"

    match = MILESTONE_LINE.search(rendered)
    assert match is not None
    assert match.group(1) == "mission.takeoff"
    assert json.loads(match.group(2)) == {"altitude_m": 1.2}
    assert STEP_LINE.search(rendered) is None


def test_an_empty_payload_is_still_an_object():
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    assert encode_milestone("mission.rtl_start").endswith("] {}")


def test_non_finite_values_reach_the_renderer_as_null():
    """``JSON.parse`` rejects NaN and Infinity; one bad float must not lose the line."""
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    line = encode_milestone(
        "mission.target_found", {"bearing_deg": float("nan"), "center_px": [float("inf"), 2.0]}
    )
    payload = json.loads(MILESTONE_LINE.search(line).group(2))
    assert payload == {"bearing_deg": None, "center_px": [None, 2.0]}
    assert "\n" not in line


def test_a_payload_carrying_a_newline_stays_on_one_line():
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    line = encode_milestone("mission.capture_done", {"raw_image": "a\nb.png"})
    assert "\n" not in line
    assert json.loads(MILESTONE_LINE.search(line).group(2))["raw_image"] == "a\nb.png"


@pytest.mark.parametrize(
    "key, payload, error",
    [
        ("mission.start", None, ValueError),
        ("mission.unknown", None, ValueError),
        (7, None, TypeError),
        ("mission.takeoff", ["altitude"], TypeError),
        ("mission.takeoff", {"altitude": object()}, ValueError),
    ],
)
def test_malformed_milestones_are_refused(key, payload, error):
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    with pytest.raises(error):
        encode_milestone(key, payload)


def test_emitting_never_raises_into_the_step(monkeypatch):
    from mvp_mission_bebop.telemetry import milestones

    warnings = []
    monkeypatch.setattr(milestones.logger, "warning", lambda *args: warnings.append(args))
    milestones.emit_milestone("mission.not_a_key", {"x": 1})
    assert warnings and warnings[0][0].startswith("Milestone dropped")


_REPO = os.path.join(os.path.dirname(__file__), "..")

#: Short enough that the whole five-stage bench run completes in well under a
#: minute; every field is a plain parameter the operator can also set.
_SHORT_BENCH_RUN = {
    "kinematics": {
        "countdown_sec": 0.0,
        "takeoff_stabilize_duration_sec": 1.0,
        "hover_duration_sec": 1.0,
    },
    "timeouts": {"search_timeout_sec": 10.0, "tracking_timeout_sec": 4.0},
    "rtl": {"timeout_sec": 4.0, "centering_timeout_sec": 3.0, "touchdown_timeout_sec": 4.0},
}


def _config_digest(path):
    import hashlib

    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def test_a_full_bench_run_crosses_every_milestone_in_flight_order(tmp_path):
    """``--no-fly --stages 1,2,3,4,5`` on the bundled target frame.

    Runs the real mission process against a bench frame with a bicycle in it,
    in a scratch directory and on a scratch parameter store, so the evidence
    files and the rewritten configuration never touch the operator's copies.
    """
    import subprocess

    pytest.importorskip("rclpy")
    operator_config = os.path.join(_REPO, "mvp_mission_bebop", "mission_config.json")
    before = _config_digest(operator_config)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mvp_mission_bebop.mission",
            "--no-fly",
            "--stages",
            "1,2,3,4,5",
            "--config",
            str(tmp_path / "mission_config.json"),
            "--bench-frame",
            os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
            "--model-path",
            os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
            "--params-json",
            json.dumps(_SHORT_BENCH_RUN),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]

    sequence = []
    payloads = {}
    for line in completed.stdout.splitlines():
        step = STEP_LINE.search(line)
        if step:
            sequence.append(f"STEP {step.group(1)}")
            continue
        milestone = MILESTONE_LINE.search(line)
        if milestone:
            payloads[milestone.group(1)] = json.loads(milestone.group(2))
            sequence.append(milestone.group(1))

    assert sequence == [
        "STEP 1",
        "mission.takeoff",
        "STEP 2",
        "mission.scan_start",
        "mission.target_found",
        "STEP 3",
        "mission.approaching",
        "STEP 4",
        "mission.capture_done",
        "STEP 5",
        "mission.rtl_start",
        "mission.landing",
    ]
    assert payloads["mission.takeoff"]["altitude_m"] == pytest.approx(
        MissionParameters().kinematics.target_altitude_m
    )
    assert payloads["mission.target_found"]["class_name"] in MissionParameters().vision.target_classes
    assert _config_digest(operator_config) == before, "the bench run rewrote the operator config"
