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

import threading
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
