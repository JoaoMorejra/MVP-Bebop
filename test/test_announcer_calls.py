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
import os
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


@pytest.fixture(autouse=True)
def _isolated_phrase_cache(monkeypatch, tmp_path_factory):
    """No test reads or writes the operator's real phrase cache."""
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path_factory.mktemp("speech")))


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
            "Falha na inicialização: Falha de conexão com driver do drone no IP 192.168.42.1.",
        ),
        (
            "Missão abortada",
            {"etapa": "missão abortada, pousando drone"},
            "Missão abortada, pousando drone",
        ),
        (
            "Falha de segurança",
            {"erro": "odometria perdida", "em_voo": True},
            "Alerta de voo: odometria perdida. Executando pouso seguro imediatamente.",
        ),
        (
            "Falha de segurança",
            {"erro": "odometria perdida"},
            "Alerta de segurança: odometria perdida.",
        ),
        (
            "Falha na decolagem",
            {"etapa": "bateria em 12 por cento, no limiar de pouso de 20 por cento, decolagem cancelada"},
            "Falha na decolagem: bateria em 12 por cento, no limiar de pouso de 20 por cento, decolagem cancelada.",
        ),
        (
            "Falha na calibração",
            {"etapa": "falha na calibração de referência de solo, decolagem cancelada."},
            "Falha na calibração: falha na calibração de referência de solo, decolagem cancelada.",
        ),
        (
            "Falha na etapa",
            {"etapa": "Decolagem", "em_voo": True},
            "Falha na etapa: Decolagem. Executando pouso seguro imediatamente.",
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
            "details": {"etapa": "Varredura", "em_voo": False},
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

    failsafe.trigger_emergency_land("Odometry telemetry stream loss (heartbeat timeout).")

    assert spoken == [
        {
            "action": "Falha de segurança",
            "details": {"erro": "perda da telemetria de odometria", "em_voo": False},
            "priority": "CRITICAL",
        }
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

    async def fake_synthesize(self, statement, verbatim, on_chunk=None):
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


# ---------------------------------------------------------- warm session


class _FakeLive:
    """A Live client whose sessions are numbered and whose closes are counted."""

    def __init__(self):
        self.opened = 0
        self.closed = []

    def connect(self, model=None, config=None):
        live = self

        class _Ctx:
            async def __aenter__(self_inner):
                live.opened += 1
                self_inner.number = live.opened
                return f"session-{live.opened}"

            async def __aexit__(self_inner, *exc):
                live.closed.append(self_inner.number)

        return _Ctx()


@pytest.fixture
def live_announcer(monkeypatch):
    device = _SilentDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    live = _FakeLive()
    client = types.SimpleNamespace(aio=types.SimpleNamespace(live=live))
    monkeypatch.setattr(announcer, "_get_synthesis_client", lambda token: client)
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: "test-token")
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance.auth_token = "test-token"
    yield instance, live
    instance.close()


def _run(instance, coroutine):
    return asyncio.run_coroutine_threadsafe(coroutine, instance._loop).result(timeout=5)


def test_a_fresh_warm_session_is_handed_to_the_next_line(live_announcer):
    instance, live = live_announcer
    _run(instance, instance._ensure_warm_session())
    warm = instance._warm_session

    session, _ = _run(instance, instance._open_session())

    assert session == warm


def test_a_warm_session_past_its_refresh_window_is_closed_not_used(live_announcer):
    instance, live = live_announcer
    _run(instance, instance._ensure_warm_session())
    stale = instance._warm_session
    instance._warm_session_time -= announcer._WARM_MAX_AGE_SEC + announcer._WARM_REFRESH_SEC + 1

    session, _ = _run(instance, instance._open_session())

    assert session != stale
    assert int(stale.split("-")[1]) in live.closed


def test_the_idle_refresh_recycles_an_aged_warm_session(live_announcer):
    instance, live = live_announcer
    _run(instance, instance._ensure_warm_session())
    first = instance._warm_session
    instance._warm_session_time -= announcer._WARM_MAX_AGE_SEC + 1

    _run(instance, instance._ensure_warm_session())

    assert instance._warm_session != first
    assert int(first.split("-")[1]) in live.closed


def test_a_failed_warm_up_is_reported_once_per_streak(live_announcer, monkeypatch):
    instance, live = live_announcer
    warnings = []
    monkeypatch.setattr(announcer.logger, "warning", lambda msg, *a: warnings.append(msg % a if a else msg))

    def broken(model=None, config=None):
        raise ConnectionError("no route")

    monkeypatch.setattr(live, "connect", broken)
    for _ in range(3):
        instance._warm_session = None
        instance._warm_ctx = None
        _run(instance, instance._ensure_warm_session())

    assert len([w for w in warnings if "warm-up failed" in w]) == 1


# ------------------------------------------------------------ ground alerts (3.6)


@pytest.mark.parametrize(
    "reason, spoken_text",
    [
        ("No odometry has ever been received. Verify /bebop/odom is publishing.", "odometria nunca recebida"),
        ("Odometry telemetry stream loss (heartbeat timeout).", "perda da telemetria de odometria"),
        ("Altitude ceiling breached: 2.10 m > 2.00 m.", "teto de altitude excedido"),
        ("Altitude ceiling breached during ascent: 2.10 m > 2.00 m.", "teto de altitude excedido"),
        ("Battery at 18% <= land threshold 20%; landing in place.", "bateria no limiar de pouso"),
        ("Camera stream loss: frame age 3.1 s > 2.0 s.", "perda do vídeo da câmera"),
        ("Ground reference lost during ascent.", "referência de solo perdida na subida"),
        ("No odometry received during RTL. Verify /bebop/odom is publishing.", "odometria nunca recebida"),
        ("Odometry telemetry loss during RTL.", "perda da telemetria de odometria"),
        ("Altitude ceiling breached during RTL.", "teto de altitude excedido"),
        ("'NoneType' object has no attribute 'snapshot'", "falha interna da missão"),
        ("", "falha interna da missão"),
    ],
)
def test_failsafe_reasons_are_spoken_in_portuguese_without_raw_exceptions(reason, spoken_text):
    from mvp_mission_bebop.telemetry.failsafe import spoken_reason

    assert spoken_reason(reason) == spoken_text


def test_no_alert_sentence_carries_a_double_period():
    for action, details in [
        ("Falha de segurança", {"erro": "perda do vídeo da câmera.", "em_voo": True}),
        ("Falha na decolagem", {"etapa": "decolagem rejeitada pela controladora de voo."}),
    ]:
        assert ".." not in _format_telemetry_statement(action, details)


def test_the_failsafe_says_landing_only_when_airborne(spoken):
    params = MissionParameters()
    failsafe = FailsafeSupervisor(
        drone_actuator=Actuator(),
        odom_supervisor=types.SimpleNamespace(relative_altitude=1.4),
        timeouts_cfg=params.timeouts,
        kinematics_cfg=params.kinematics,
    )
    failsafe.trigger_emergency_land("Camera stream loss: frame age 3.1 s > 2.0 s.")
    assert spoken[-1]["details"] == {"erro": "perda do vídeo da câmera", "em_voo": True}


class Alerts(BaseStep):
    """A step that raises its own alert before failing, as TakeoffStep does."""

    def __init__(self):
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")

    def execute(self, ctx):
        announcer.announce_sync("Falha na decolagem", details={"etapa": "decolagem rejeitada"}, priority="CRITICAL")
        return StepStatus.FAILURE


def test_a_step_that_already_alerted_is_not_announced_again(station):
    runner = MissionRunner(runner_context(), [Alerts()], stage_numbers=[1])
    runner.run()
    assert [key for key, _ in station] == ["mission.takeoff_failed"]


def test_a_failsafe_landing_is_not_followed_by_a_step_failure(station):
    ctx = runner_context()
    ctx.failsafe = types.SimpleNamespace(failsafe_active=True)
    MissionRunner(ctx, [Ends(StepStatus.FAILURE)], stage_numbers=[2]).run()
    assert station == []


def test_a_silent_step_failure_on_the_ground_names_the_stage_without_a_landing(station):
    MissionRunner(runner_context(), [Ends(StepStatus.FAILURE)], stage_numbers=[2]).run()
    assert len(station) == 1
    key, payload = station[0]
    assert key == "mission.step_failed"
    assert "Executando pouso" not in payload["text"]
    assert "Varredura" in payload["text"]


# ------------------------------------------------------------ real mutex (3.8)


class _RecordingDevice(_SilentDevice):
    """Records plays and stops, in order."""

    def __init__(self):
        super().__init__()
        self.events = []

    def play_audio(self, pcm):
        self.played.append(pcm)
        self.events.append(("play", len(pcm)))

    def stop_current(self):
        self.events.append(("stop", 0))


@pytest.fixture
def slow_announcer(monkeypatch):
    """Synthesis that takes as long as the test says, one gate per statement."""
    device = _RecordingDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    gates = {}
    started = []

    async def fake_synthesize(self, statement, verbatim, on_chunk=None):
        started.append(statement)
        gate = gates.setdefault(statement, asyncio.Event())
        await gate.wait()
        return b"\x01\x00" * (4800 + len(statement))

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize", fake_synthesize)
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance._ensure_warm_session = lambda: asyncio.sleep(0)

    def release(statement):
        def set_gate():
            gates.setdefault(statement, asyncio.Event()).set()

        instance._loop.call_soon_threadsafe(set_gate)

    def wait_started(statement, timeout=2.0):
        deadline = time.monotonic() + timeout
        while statement not in started and time.monotonic() < deadline:
            time.sleep(0.005)
        return statement in started

    yield instance, device, release, wait_started
    instance.close()


def test_a_line_cancelled_mid_synthesis_is_never_played(slow_announcer):
    """Measured: a cancelled line finished synthesizing and played in full, 12 s later."""
    instance, device, release, wait_started = slow_announcer
    assert instance.announce("Linha longa cancelada.", verbatim=True) is True
    assert wait_started("Linha longa cancelada.")
    instance.cancel_pending()
    release("Linha longa cancelada.")
    time.sleep(0.2)
    assert device.played == []


def test_after_a_cancel_the_next_line_still_plays(slow_announcer):
    instance, device, release, wait_started = slow_announcer
    instance.announce("Antes do cancel.", verbatim=True)
    assert wait_started("Antes do cancel.")
    instance.cancel_pending()
    release("Antes do cancel.")
    instance.announce("Depois do cancel.", verbatim=True)
    assert wait_started("Depois do cancel.")
    release("Depois do cancel.")
    deadline = time.monotonic() + 2.0
    while not device.played and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(device.played) == 1


def test_an_alert_does_not_cut_an_alert(slow_announcer):
    instance, device, release, wait_started = slow_announcer
    instance.announce("Primeiro alerta.", priority="URGENT", verbatim=True)
    assert wait_started("Primeiro alerta.")
    release("Primeiro alerta.")
    deadline = time.monotonic() + 2.0
    while len(device.played) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    instance._playing_urgent_until = time.monotonic() + 5.0
    instance.announce("Segundo alerta.", priority="URGENT", verbatim=True)
    assert wait_started("Segundo alerta.")
    release("Segundo alerta.")
    deadline = time.monotonic() + 2.0
    while len(device.played) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    first_play = device.events.index(("play", device.events[-2][1]))
    assert ("stop", 0) not in device.events[first_play:]
    assert len(device.played) == 2


def test_an_alert_cuts_narration(slow_announcer):
    instance, device, release, wait_started = slow_announcer
    instance.announce("Narração em curso.", verbatim=True)
    assert wait_started("Narração em curso.")
    release("Narração em curso.")
    deadline = time.monotonic() + 2.0
    while not device.played and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.announce("Alerta urgente.", priority="URGENT", verbatim=True)
    assert wait_started("Alerta urgente.")
    assert ("stop", 0) in device.events


# ------------------------------------------------------------ single player lock (3.8)


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("BMG_GCS_SESSION", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_a_live_daemon_lock_makes_any_other_process_leave_the_voice_to_it(lock_dir):
    import subprocess
    import sys as _sys

    holder = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (lock_dir / "bmg-announcer.lock").write_text(str(holder.pid))
        assert announcer.station_narrates() is True
    finally:
        holder.kill()
        holder.wait()


def test_a_stale_lock_from_a_dead_daemon_is_ignored(lock_dir):
    (lock_dir / "bmg-announcer.lock").write_text("999999999")
    assert announcer.station_narrates() is False


def test_the_daemon_is_not_silenced_by_its_own_lock(lock_dir):
    (lock_dir / "bmg-announcer.lock").write_text(str(os.getpid()))
    assert announcer.station_narrates() is False


def test_the_daemon_takes_and_releases_the_lock(lock_dir):
    path = announcer.acquire_player_lock()
    assert path is not None and path.read_text().strip() == str(os.getpid())
    announcer.release_player_lock(path)
    assert not path.exists()


def test_the_session_variable_still_decides_on_its_own(lock_dir, monkeypatch):
    monkeypatch.setenv("BMG_GCS_SESSION", "1")
    assert announcer.station_narrates() is True


# ------------------------------------------------------------ phrase cache (3.9)


@pytest.fixture
def cached_announcer(monkeypatch, tmp_path):
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path / "speech"))
    device = _SilentDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    calls = []

    async def fake_synthesize(self, statement, verbatim, on_chunk=None):
        calls.append(statement)
        await asyncio.sleep(0.05)
        return b"\x02\x00" * 4800

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize", fake_synthesize)
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance._ensure_warm_session = lambda: asyncio.sleep(0)
    yield instance, calls, device, tmp_path / "speech"
    instance.close()


def test_a_spoken_line_is_cached_and_the_next_time_plays_from_disk(cached_announcer):
    instance, calls, device, cache_dir = cached_announcer
    assert instance.announce("Pouso confirmado na base.", wait=True, timeout=5.0, verbatim=True) is True
    assert instance.announce("Pouso confirmado na base.", wait=True, timeout=5.0, verbatim=True) is True
    assert calls == ["Pouso confirmado na base."]
    assert len(device.played) == 2
    assert len(list(cache_dir.glob("*.pcm"))) == 1


def test_the_cache_key_covers_text_mode_voice_and_model(monkeypatch, tmp_path):
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path))
    cache = announcer.PhraseCache()
    a = cache.path_for("Pouso.", verbatim=True)
    assert a != cache.path_for("Pouso.", verbatim=False)
    assert a != cache.path_for("Pouso!", verbatim=True)
    monkeypatch.setattr(announcer, "SYNTHESIZER_VOICE", "Outra")
    assert a != cache.path_for("Pouso.", verbatim=True)


def test_a_truncated_cache_entry_is_not_played(monkeypatch, tmp_path):
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path))
    cache = announcer.PhraseCache()
    cache.path_for("Curta.", verbatim=True).parent.mkdir(parents=True, exist_ok=True)
    cache.path_for("Curta.", verbatim=True).write_bytes(b"\x00")
    assert cache.get("Curta.", verbatim=True) is None


def test_warming_the_cache_synthesizes_only_what_is_missing(cached_announcer):
    instance, calls, _device, cache_dir = cached_announcer
    instance.announce("Já falada.", wait=True, timeout=5.0, verbatim=True)
    calls.clear()
    instance.warm_cache(["Já falada.", "Nova um.", "Nova dois.", "Nova um."])
    deadline = time.monotonic() + 3.0
    while len(list(cache_dir.glob("*.pcm"))) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sorted(calls) == ["Nova dois.", "Nova um."]
    assert len(list(cache_dir.glob("*.pcm"))) == 3


def test_every_played_line_logs_its_latency(cached_announcer):
    import logging

    instance, _calls, _device, _dir = cached_announcer
    records = []
    handler = logging.Handler(level=logging.INFO)
    handler.emit = records.append
    target = logging.getLogger("TelemetryAnnouncer")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        instance.announce("Linha medida.", wait=True, timeout=5.0, verbatim=True)
        instance.announce("Linha medida.", wait=True, timeout=5.0, verbatim=True)
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)
    lines = [r.getMessage() for r in records if r.getMessage().startswith("[SPEECH]")]
    assert len(lines) == 2
    assert all("latency_ms=" in line for line in lines)
    assert "source=synth" in lines[0] and "source=cache" in lines[1]


def test_every_played_line_logs_the_length_of_its_audio(cached_announcer):
    """8.3: the rehearsal checks for overlapping speech from these lines."""
    import logging

    instance, _calls, _device, _dir = cached_announcer
    records = []
    handler = logging.Handler(level=logging.INFO)
    handler.emit = records.append
    target = logging.getLogger("TelemetryAnnouncer")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        instance.announce("Linha medida.", wait=True, timeout=5.0, verbatim=True)
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)
    done = [r.getMessage() for r in records if r.getMessage().startswith("[SPEECH_DONE]")]
    assert len(done) == 1
    assert "audio_ms=" in done[0] and "priority=NORMAL" in done[0]
    assert int(done[0].split("audio_ms=")[1].split()[0]) > 0


# ------------------------------------------------------------ block playback (3.9)


class _FakeStream:
    """Stands in for ``sounddevice.RawOutputStream``: writes take real time."""

    instances = []

    def __init__(self, samplerate, channels, dtype, **_kwargs):
        self.samplerate = samplerate
        self.writes = []
        self.aborted = 0
        _FakeStream.instances.append(self)

    def start(self):
        pass

    def write(self, data):
        self.writes.append((time.monotonic(), len(data)))
        time.sleep(len(data) / 2.0 / self.samplerate)

    def abort(self):
        self.aborted += 1

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def block_device(monkeypatch):
    fake = types.SimpleNamespace(RawOutputStream=_FakeStream, stop=lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake)
    _FakeStream.instances.clear()
    device = announcer.AudioPlaybackDevice()
    assert device.start()
    yield device
    device.close()


def _pcm(seconds, rate=24_000):
    return b"\x10\x00" * int(seconds * rate)


def test_consecutive_buffers_play_through_one_open_stream(block_device):
    block_device.play_audio(_pcm(0.2))
    block_device.play_audio(_pcm(0.2))
    assert block_device.wait_until_done(timeout=3.0)
    assert len(_FakeStream.instances) == 1
    assert sum(size for _, size in _FakeStream.instances[0].writes) == 2 * len(_pcm(0.2))


def test_a_stop_silences_a_long_line_within_a_hundred_milliseconds(block_device):
    block_device.play_audio(_pcm(3.0))
    time.sleep(0.3)
    stopped_at = time.monotonic()
    block_device.stop_current()
    time.sleep(0.4)
    writes = _FakeStream.instances[0].writes
    after = [at for at, _ in writes if at > stopped_at + 0.1]
    assert after == []
    total = sum(size for _, size in writes)
    assert total < len(_pcm(1.0))


def test_a_new_line_plays_after_a_stop(block_device):
    block_device.play_audio(_pcm(2.0))
    time.sleep(0.2)
    block_device.stop_current()
    block_device.play_audio(_pcm(0.2))
    assert block_device.wait_until_done(timeout=3.0)
    last = _FakeStream.instances[-1].writes
    assert last and last[-1][1] > 0


def test_a_muted_device_writes_nothing_but_keeps_the_line_duration(block_device):
    block_device.set_level(muted=True)
    started = time.monotonic()
    block_device.play_audio(_pcm(0.3))
    assert block_device.wait_until_done(timeout=3.0)
    assert time.monotonic() - started >= 0.25
    assert all(not stream.writes for stream in _FakeStream.instances)



# ------------------------------------------------------------ streaming (3.9)


@pytest.fixture
def streaming_announcer(monkeypatch):
    device = _RecordingDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    finished = []

    async def fake_synthesize(self, statement, verbatim, on_chunk=None):
        chunks = [b"\x03\x00" * 2400, b"\x04\x00" * 2400, b"\x05\x00" * 2400]
        audio = bytearray()
        for chunk in chunks:
            await asyncio.sleep(0.15)
            audio.extend(chunk)
            if on_chunk is not None:
                await on_chunk(chunk)
        finished.append(time.monotonic())
        return bytes(audio)

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize", fake_synthesize)
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance._ensure_warm_session = lambda: asyncio.sleep(0)
    yield instance, device, finished
    instance.close()


def test_a_synthesized_line_starts_playing_on_its_first_chunk(streaming_announcer):
    instance, device, finished = streaming_announcer
    plays = []
    original = device.play_audio

    def stamped(pcm):
        plays.append(time.monotonic())
        original(pcm)

    device.play_audio = stamped
    assert instance.announce("Linha em streaming.", wait=True, timeout=5.0, verbatim=True) is True
    assert len(device.played) == 3
    assert plays[0] < finished[0] - 0.2
    assert b"".join(device.played) == b"\x03\x00" * 2400 + b"\x04\x00" * 2400 + b"\x05\x00" * 2400


def test_a_line_cancelled_while_streaming_stops_feeding_the_device(streaming_announcer):
    instance, device, _finished = streaming_announcer
    instance.announce("Streaming cancelado.", verbatim=True)
    deadline = time.monotonic() + 2.0
    while not device.played and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.cancel_pending()
    time.sleep(0.6)
    assert len(device.played) == 1


# ------------------------------------------------------------ standalone verbatim (3.10)


def test_a_standalone_announcement_speaks_exactly_the_sentence_it_logs(monkeypatch):
    """Without the station the phrase mapper's sentence is logged; it must be the one spoken."""
    device = _SilentDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    seen = []

    async def fake_synthesize(self, statement, verbatim, on_chunk=None):
        seen.append((statement, verbatim))
        return b"\x01\x00" * 4800

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize", fake_synthesize)
    instance = announcer.MissionAudioAnnouncer()
    if instance.session_config is None:
        pytest.skip("google-genai not installed")
    instance._ensure_warm_session = lambda: asyncio.sleep(0)
    try:
        instance.announce(
            "Falha na decolagem",
            details={"etapa": "decolagem rejeitada pela controladora de voo"},
            wait=True,
            timeout=5.0,
        )
    finally:
        instance.close()
    assert seen == [("Falha na decolagem: decolagem rejeitada pela controladora de voo.", True)]


# ------------------------------------------------------------ clean audio shutdown


def test_closing_the_device_mid_line_stops_the_worker_before_interpreter_exit(block_device):
    """A playback thread still inside PortAudio at interpreter shutdown crashed
    the bench mission (SIGSEGV/SIGABRT) after its touchdown."""
    block_device.set_level(volume=0.5)
    block_device.play_audio(_pcm(5.0))
    time.sleep(0.2)
    started = time.monotonic()
    block_device.close()
    assert time.monotonic() - started < 0.5
    assert block_device._thread is None or not block_device._thread.is_alive()


def test_the_global_device_is_closed_at_exit(monkeypatch):
    registered = []
    monkeypatch.setattr(announcer.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(announcer, "_global_playback_device", None)
    monkeypatch.setattr(announcer, "_audio_exit_registered", False)

    class Fake:
        closed = False

        def start(self):
            return True

        def close(self):
            Fake.closed = True

    monkeypatch.setattr(announcer, "AudioPlaybackDevice", Fake)
    announcer.get_audio_playback_device()
    announcer.get_audio_playback_device()
    assert len(registered) == 1
    registered[0]()
    assert Fake.closed is True
