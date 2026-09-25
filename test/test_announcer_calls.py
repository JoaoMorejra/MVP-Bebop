"""The mission process's own spoken status and failure calls.

These are the announcements the mission makes directly through
``announce_sync`` rather than through the ground station's milestone
narration: startup, the runner's abort and step-failure calls, and the
failsafe's emergency landing. They are pinned here -- the action string the
call sites pass, the priority, and the sentence the announcer turns it into --
so a change elsewhere in the pipeline cannot silently alter what the operator
hears when something goes wrong.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest

from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.telemetry import announcer
from mvp_mission_bebop.telemetry.announcer import _format_telemetry_statement
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor


@pytest.fixture
def spoken(monkeypatch):
    """Record every ``announce_sync`` call instead of synthesizing it."""
    calls = []

    def record(action, details=None, wait=False, priority="NORMAL", verbatim=False):
        calls.append({"action": action, "details": details, "priority": priority})
        return True

    monkeypatch.setattr(announcer, "announce_sync", record)
    return calls


# ------------------------------------------------------------- phrase mapping


@pytest.mark.parametrize(
    "action, details, sentence",
    [
        (
            "Iniciando Missão",
            {"etapa": "iniciando missão, carregando parâmetros"},
            "Missão iniciada. Parâmetros de voo carregados.",
        ),
        (
            "Falha na inicialização",
            {"erro": "Falha de conexão com driver do drone no IP 192.168.42.1"},
            "Alerta de voo: Falha de conexão com driver do drone no IP 192.168.42.1. "
            "Executando pouso seguro imediatamente.",
        ),
        (
            "Missão abortada",
            {"etapa": "missão abortada, pousando drone"},
            "Missão abortada, pousando drone",
        ),
        (
            "Falha de segurança",
            {"erro": "odometria perdida"},
            "Alerta de voo: odometria perdida. Executando pouso seguro imediatamente.",
        ),
    ],
)
def test_startup_and_failure_calls_keep_their_sentences(action, details, sentence):
    assert _format_telemetry_statement(action, details) == sentence


# --------------------------------------------------------------------- runner


class Ends(BaseStep):
    def __init__(self, status):
        super().__init__("STEP 2: Linear Forward Search")
        self.status = status

    def execute(self, ctx):
        return self.status


def runner_context():
    return types.SimpleNamespace(
        emergency_event=threading.Event(),
        stage_jump_event=threading.Event(),
        requested_stage=None,
        detection_reveal_enabled=False,
    )


def test_a_failed_step_is_announced_as_critical(spoken):
    runner = MissionRunner(runner_context(), [Ends(StepStatus.FAILURE)], stage_numbers=[2])

    assert runner.run() is False
    assert spoken == [
        {
            "action": "Falha na etapa",
            "details": {"etapa": "falha na etapa STEP 2: Linear Forward Search"},
            "priority": "CRITICAL",
        }
    ]


def test_an_aborted_step_is_announced_as_urgent(spoken):
    runner = MissionRunner(runner_context(), [Ends(StepStatus.ABORTED)], stage_numbers=[2])

    assert runner.run() is False
    assert spoken == [
        {
            "action": "Missão abortada",
            "details": {"etapa": "missão abortada, pousando drone"},
            "priority": "URGENT",
        }
    ]


def test_a_successful_run_says_nothing_of_its_own(spoken):
    runner = MissionRunner(runner_context(), [Ends(StepStatus.SUCCESS)], stage_numbers=[2])

    assert runner.run() is True
    assert spoken == []


# ------------------------------------------------------------------- failsafe


class Actuator:
    def __init__(self):
        self.commands = []

    def move_velocity(self, **kwargs):
        self.commands.append(("move", kwargs))

    def land(self):
        self.commands.append(("land", {}))


def test_the_failsafe_announces_its_emergency_landing(spoken):
    params = MissionParameters()
    actuator = Actuator()
    failsafe = FailsafeSupervisor(
        drone_actuator=actuator,
        odom_supervisor=types.SimpleNamespace(),
        timeouts_cfg=params.timeouts,
        kinematics_cfg=params.kinematics,
    )

    failsafe.trigger_emergency_land("odometria perdida")

    assert spoken == [
        {"action": "Falha de segurança", "details": {"erro": "odometria perdida"}, "priority": "CRITICAL"}
    ]
    assert ("land", {}) in actuator.commands


def test_a_silent_announcer_never_blocks_the_emergency_landing(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("no audio device")

    monkeypatch.setattr(announcer, "announce_sync", broken)
    params = MissionParameters()
    actuator = Actuator()
    failsafe = FailsafeSupervisor(
        drone_actuator=actuator,
        odom_supervisor=types.SimpleNamespace(),
        timeouts_cfg=params.timeouts,
        kinematics_cfg=params.kinematics,
    )

    failsafe.trigger_emergency_land("odometria perdida")

    assert ("land", {}) in actuator.commands


# ------------------------------------------------------ single voice (GCS)


@pytest.fixture
def station(monkeypatch):
    """A mission launched by the ground station: no local player may exist."""
    monkeypatch.setenv(announcer.GCS_SESSION_ENV, "1")

    def forbidden():
        raise AssertionError("a local announcer was built under the ground station")

    monkeypatch.setattr(announcer, "get_announcer", forbidden)
    alerts = []
    from mvp_mission_bebop.telemetry import milestones

    real = milestones.emit_alert

    def record(key, payload=None):
        alerts.append((key, dict(payload or {})))
        return real(key, payload)

    monkeypatch.setattr(milestones, "emit_alert", record)
    return alerts


def test_flight_narration_is_left_to_the_station(station):
    for action, detail in [
        ("Iniciando Missão", "iniciando missão, carregando parâmetros"),
        ("Decolagem autorizada", "decolagem autorizada, iniciando voo"),
        ("Acidente detectado", "alvo detectado na pista, iniciando aproximação"),
        ("Pouso seguro concluído", "pouso seguro concluído na base de lançamento"),
    ]:
        assert announcer.announce_sync(action, details={"etapa": detail}) is False
    assert announcer.speak("Primeiro: sem necessidade de polícia.") is False
    assert station == []


@pytest.mark.parametrize(
    "action, details, priority, key",
    [
        ("Missão abortada", {"etapa": "missão abortada, pousando drone"}, "URGENT", "mission.abort"),
        ("Falha na etapa", {"etapa": "falha na etapa STEP 2"}, "CRITICAL", "mission.step_failed"),
        ("Falha de segurança", {"erro": "odometria perdida"}, "CRITICAL", "mission.failsafe"),
        ("Falha na inicialização", {"erro": "driver ausente"}, "CRITICAL", "mission.init_failed"),
        ("Falha na calibração", {"etapa": "calibração recusada"}, "CRITICAL", "mission.calibration_failed"),
        ("Falha na decolagem", {"etapa": "decolagem rejeitada"}, "CRITICAL", "mission.takeoff_failed"),
        ("Bateria crítica", {"erro": "bateria em 5 %"}, "NORMAL", "mission.alert"),
    ],
)
def test_failures_reach_the_station_as_alerts_with_their_sentence(station, action, details, priority, key):
    assert announcer.announce_sync(action, details=details, priority=priority) is True
    assert station == [
        (
            key,
            {
                "text": _format_telemetry_statement(action, details),
                "priority": priority if priority != "NORMAL" else "URGENT",
            },
        )
    ]


def test_the_runner_abort_becomes_an_alert_not_a_voice(station):
    runner = MissionRunner(runner_context(), [Ends(StepStatus.ABORTED)], stage_numbers=[2])
    runner.run()
    assert [key for key, _ in station] == ["mission.abort"]


def test_standalone_runs_still_speak_through_the_local_announcer(monkeypatch):
    monkeypatch.delenv(announcer.GCS_SESSION_ENV, raising=False)
    played = []
    local = types.SimpleNamespace(announce=lambda action, *args, **kwargs: played.append(action) or True)
    monkeypatch.setattr(announcer, "get_announcer", lambda: local)

    assert announcer.announce_sync("Acidente detectado") is True
    assert announcer.announce_sync("Falha de segurança", details={"erro": "x"}, priority="CRITICAL")
    assert played == ["Acidente detectado", "Falha de segurança"]


def test_an_alert_line_reaches_the_stdout_the_station_reads():
    """Checked in a subprocess: the assertion is about the real stdout pipe."""
    import os
    import re
    import subprocess
    import sys

    program = (
        "import mvp_mission_bebop.mission;"
        "from mvp_mission_bebop.telemetry.announcer import announce_sync;"
        "announce_sync('Falha de segurança', details={'erro': 'odometria perdida'}, priority='CRITICAL');"
        "announce_sync('Acidente detectado', details={'etapa': 'alvo detectado'})"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=180,
        env=dict(os.environ, BMG_GCS_SESSION="1"),
    )
    assert completed.returncode == 0, completed.stderr
    alerts = re.findall(r"\[ALERT ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$", completed.stdout, re.M)
    assert [key for key, _ in alerts] == ["mission.failsafe"]
    assert "odometria perdida" in alerts[0][1]
    assert "Acidente detectado" not in completed.stdout


# ------------------------------------------------------- synthesis prompt


@pytest.mark.parametrize("verbatim", [True, False])
def test_every_synthesis_prompt_pins_brazilian_portuguese(verbatim):
    instruction = announcer.synthesis_instruction("Decolagem autorizada.", verbatim)

    assert "pt-BR" in instruction
    assert "never use European Portuguese" in instruction
    assert "'Decolagem autorizada.'" in instruction


def test_a_verbatim_prompt_forbids_rewording_and_a_free_one_bounds_its_length():
    assert "exactly as written" in announcer.synthesis_instruction("Pousando.", verbatim=True)
    assert "at most twelve words" in announcer.synthesis_instruction("Pousando.", verbatim=False)


def test_the_synthesis_session_is_opened_in_the_brazilian_locale():
    assert announcer.SYNTHESIZER_LANGUAGE == "pt-BR"


@pytest.mark.parametrize("statement, error", [(None, TypeError), (3, TypeError), ("", ValueError), ("  ", ValueError)])
def test_the_synthesis_prompt_rejects_a_missing_statement(statement, error):
    with pytest.raises(error):
        announcer.synthesis_instruction(statement, verbatim=True)


# ------------------------------------------------------------ prefetch


class _SilentDevice:
    """Playback stand-in: records what would be played, plays nothing."""

    _active = True

    def __init__(self):
        self.played = []

    def play_audio(self, pcm):
        self.played.append(pcm)

    def wait_until_done(self, timeout=None):
        return True

    def stop_current(self):
        pass

    def get_level(self):
        return {"volume": 1.0, "muted": False}

    def close(self):
        pass


@pytest.fixture
def offline_announcer(monkeypatch):
    """A real announcer loop whose synthesis is a counter, not a network call."""
    device = _SilentDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    calls = []

    async def fake_synthesize(self, statement, verbatim):
        calls.append(statement)
        return b"\x01\x00" * 4800

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize", fake_synthesize)
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance._ensure_warm_session = lambda: asyncio.sleep(0)
    yield instance, calls, device
    instance.close()


def test_a_prefetched_line_is_synthesized_once_and_played_on_request(offline_announcer):
    instance, calls, device = offline_announcer

    assert instance.prefetch("Iniciando retorno à base.") is True
    deadline = time.monotonic() + 2.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.announce("Iniciando retorno à base.", wait=True, timeout=5.0, verbatim=True) is True

    assert calls == ["Iniciando retorno à base."]
    assert len(device.played) == 1


def test_a_line_without_prefetch_is_synthesized_on_request(offline_announcer):
    instance, calls, _ = offline_announcer
    assert instance.announce("Pouso iminente.", wait=True, timeout=5.0, verbatim=True) is True
    assert calls == ["Pouso iminente."]


def test_cancel_drops_prefetched_audio(offline_announcer):
    instance, calls, _ = offline_announcer
    instance.prefetch("Linha descartada.")
    deadline = time.monotonic() + 2.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.cancel_pending()
    assert instance.announce("Linha descartada.", wait=True, timeout=5.0, verbatim=True) is True
    assert calls == ["Linha descartada.", "Linha descartada."]


def test_prefetch_rejects_what_is_not_text(offline_announcer):
    instance, _, _ = offline_announcer
    with pytest.raises(TypeError):
        instance.prefetch(None)
    assert instance.prefetch("   ") is False


# ------------------------------------------------------- forensic report


@pytest.mark.parametrize("seed", range(20))
def test_the_mission_side_report_is_talked_through_not_numbered(seed):
    report = announcer.build_forensic_report(seed=seed)

    assert sorted(f["topic"] for f in report) == sorted(announcer.FORENSIC_FINDINGS)
    for finding in report:
        assert not any(word in finding["speech"] for word in ("Primeiro", "Segundo", "Terceiro", "Quarto"))
        assert finding["speech"].endswith(".")
    assert report[0]["speech"].startswith(announcer.FORENSIC_OPENERS)
    assert report[-1]["speech"].startswith(announcer.FORENSIC_CLOSERS)
    middle = [f["speech"].split(" ")[0] + f["speech"].split(" ")[1] for f in report[1:-1]]
    assert len(set(middle)) == len(middle)


def test_the_mission_side_report_has_no_pause_parameter():
    import inspect

    assert "gap_sec" not in inspect.signature(announcer.announce_forensic_report).parameters


@pytest.mark.parametrize("verbatim", [True, False])
def test_every_synthesis_prompt_asks_for_a_brisk_presenter_pace(verbatim):
    instruction = announcer.synthesis_instruction("Decolagem autorizada.", verbatim)
    assert "20 percent faster" in instruction
    assert "never robotic" in instruction
