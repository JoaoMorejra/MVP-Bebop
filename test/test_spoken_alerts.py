"""The alert sentences the mission can hand the station are finite and listed.

Under the station the mission has no voice of its own: an urgent announcement
goes out as an ``[ALERT <key>]`` line carrying the sentence
``_format_telemetry_statement`` composed, and the station speaks that string.
Offline it is heard only if it was synthesized ahead, so every sentence a call
site can produce must appear in :data:`spoken_alerts.ALERT_SENTENCES`. The
values the list mirrors from modules it does not import are compared here.
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict, Final, FrozenSet, Optional, Tuple

import pytest

from mvp_mission_bebop.mission_mode import spoken_stage_five_name
from mvp_mission_bebop.parameters import MissionParameters, NetworkConfig
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.telemetry import spoken_alerts
from mvp_mission_bebop.telemetry.announcer import _GROUND_FAULT_ACTIONS, _format_telemetry_statement
from mvp_mission_bebop.telemetry.battery import BATTERY_WARNING_PCT
from mvp_mission_bebop.telemetry.failsafe import _SPOKEN_REASONS, _UNKNOWN_REASON
from mvp_mission_bebop.telemetry.mission_parameters import spoken_parameters

REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
CATALOG_TS: Final[pathlib.Path] = REPO_ROOT / "bebop_mission_control" / "src" / "lib" / "spokenCatalog.ts"
MISSION_PY: Final[pathlib.Path] = REPO_ROOT / "mvp_mission_bebop" / "mission.py"

FAILURE_CATEGORIES: Final[FrozenSet[str]] = frozenset(
    {
        "falha.aborto",
        "falha.link",
        "falha.bateria",
        "falha.timeout_varredura",
        "falha.calibracao",
        "falha.decolagem",
        "falha.recusa_lancamento",
    }
)


def _texts() -> Tuple[str, ...]:
    return spoken_alerts.all_alert_sentences()


def test_no_duplicates() -> None:
    """Each sentence is listed once."""
    texts = _texts()
    assert len(texts) == len(set(texts))
    assert texts == tuple(spec.text for spec in spoken_alerts.ALERT_SENTENCES)


def test_every_sentence_ends_with_a_period() -> None:
    """The station speaks the string as sent; a missing period is a different cache key."""
    for text in _texts():
        assert text.endswith("."), text
        assert text == text.strip(), text


def test_every_spec_is_a_failure_category_with_condition_and_source() -> None:
    """Categories belong to the failure section of ``SpeechCategory`` in ``spokenCatalog.ts``."""
    for spec in spoken_alerts.ALERT_SENTENCES:
        assert isinstance(spec, spoken_alerts.AlertSpec)
        assert spec.category in FAILURE_CATEGORIES, spec
        assert spec.condition.strip(), spec
        assert spec.source.strip(), spec
    assert spoken_alerts.ALERT_CATEGORIES == FAILURE_CATEGORIES


@pytest.mark.parametrize("airborne", [None, False, True])
def test_ground_fault_actions_are_listed(airborne: Optional[bool]) -> None:
    """The bare fault sentence of every ground fault action, with and without the landing clause."""
    texts = set(_texts())
    for action in _GROUND_FAULT_ACTIONS:
        details: Optional[Dict[str, object]] = None if airborne is None else {"em_voo": airborne}
        sentence = _format_telemetry_statement(action.capitalize(), details)
        assert sentence in texts, sentence


@pytest.mark.parametrize("airborne", [False, True])
def test_every_failsafe_reason_is_listed(airborne: bool) -> None:
    """``FailsafeSupervisor.trigger_emergency_land`` speaks one of a fixed set of clauses."""
    texts = set(_texts())
    clauses = {spoken for _prefix, spoken in _SPOKEN_REASONS} | {_UNKNOWN_REASON}
    for clause in clauses:
        sentence = _format_telemetry_statement("Falha de segurança", {"erro": clause, "em_voo": airborne})
        assert sentence in texts, sentence


@pytest.mark.parametrize("airborne", [False, True])
def test_every_stage_failure_is_listed(airborne: bool) -> None:
    """``MissionRunner._announce_failure('Falha na etapa', <stage name>)`` for stages 1 to 5."""
    from mvp_mission_bebop.engine.runner import _SPOKEN_STAGE_NAMES

    assert dict(spoken_alerts.SPOKEN_STAGE_NAMES) == dict(_SPOKEN_STAGE_NAMES)
    texts = set(_texts())
    names = list(_SPOKEN_STAGE_NAMES.values()) + [spoken_stage_five_name(True), spoken_stage_five_name(False)]
    for name in names:
        sentence = _format_telemetry_statement("Falha na etapa", {"etapa": name, "em_voo": airborne})
        assert sentence in texts, sentence


def test_runner_abort_is_listed() -> None:
    """``MissionRunner`` reports an aborted step with a fixed sentence, closed by a period."""
    sentence = _format_telemetry_statement("Missão abortada", {"etapa": "missão abortada, pousando drone"})
    assert sentence == "Missão abortada, pousando drone."
    spec = next(spec for spec in spoken_alerts.ALERT_SENTENCES if spec.text == sentence)
    assert spec.category == "falha.aborto"
    source = (REPO_ROOT / "mvp_mission_bebop" / "engine" / "runner.py").read_text(encoding="utf-8")
    assert '_announce_failure("Missão abortada", "missão abortada, pousando drone")' in source


def test_failure_categories_match_the_catalog() -> None:
    """The station table groups Python rows by the ``falha.*`` categories of ``SPEECH_CATEGORIES``."""
    source = CATALOG_TS.read_text(encoding="utf-8")
    block = re.search(
        r"^export const SPEECH_CATEGORIES: readonly SpeechCategory\[\] = \[(.*?)^\];$", source, re.MULTILINE | re.DOTALL
    )
    assert block, "SPEECH_CATEGORIES not found in spokenCatalog.ts"
    catalog = set(re.findall(r"'(falha\.[a-z_]+)'", block.group(1)))
    assert catalog == set(spoken_alerts.ALERT_CATEGORIES)
    assert catalog == FAILURE_CATEGORIES


def test_takeoff_step_refusals_are_listed() -> None:
    """The fixed refusals of ``TakeoffStep``, as its ``_announce`` composes them."""
    texts = set(_texts())
    calls = (
        ("Nivelamento sem confirmação", {"etapa": "a aeronave não confirmou o nivelamento, decolagem cancelada"}),
        ("Falha na calibração", {"etapa": "falha na calibração de referência de solo, decolagem cancelada"}),
        ("Falha na decolagem", {"etapa": "decolagem rejeitada pela controladora de voo", "em_voo": False}),
        ("Falha na decolagem", {"etapa": "decolagem não confirmada pela aeronave", "em_voo": False}),
    )
    for action, details in calls:
        sentence = _format_telemetry_statement(action, details)
        assert sentence in texts, sentence
    source = (REPO_ROOT / "mvp_mission_bebop" / "steps" / "takeoff.py").read_text(encoding="utf-8")
    for _action, details in calls:
        assert f'"{details["etapa"]}"' in source, details["etapa"]


def test_driver_connection_failure_is_listed_for_the_default_address() -> None:
    """``mission.py`` names the drone address; the Bebop 2 always serves on the default one."""
    source = MISSION_PY.read_text(encoding="utf-8")
    assert 'erro_msg = f"Falha de conexão com driver do drone no IP {params.network.drone_ip}"' in source
    erro = f"Falha de conexão com driver do drone no IP {NetworkConfig().drone_ip}"
    assert _format_telemetry_statement("Falha na inicialização", {"erro": erro}) in set(_texts())


def test_catalog_mirrors_the_mission_values() -> None:
    """``spokenCatalog.ts`` mirrors the warning threshold and the schema defaults it cannot import."""
    source = CATALOG_TS.read_text(encoding="utf-8")
    threshold = re.search(r"^export const BATTERY_WARNING_PCT = ([0-9.]+);$", source, re.MULTILINE)
    assert threshold, "BATTERY_WARNING_PCT not found in spokenCatalog.ts"
    assert float(threshold.group(1)) == BATTERY_WARNING_PCT

    block = re.search(r"^export const SCHEMA_DEFAULT_PARAMETERS[^{]*\{([^}]*)\}", source, re.MULTILINE)
    assert block, "SCHEMA_DEFAULT_PARAMETERS not found in spokenCatalog.ts"
    mirrored = {key: float(value) for key, value in re.findall(r"(\w+): ([0-9.]+)", block.group(1))}
    params = MissionParameters()
    emitted = spoken_parameters(params, SpeedCalibration.from_kinematics(params.kinematics))
    assert mirrored == {key: emitted[key] for key in mirrored}
    assert set(mirrored) == {"target_altitude_m", "cruise_mps"}


def test_alert_spec_validates_its_fields() -> None:
    """Defensive construction, as the rest of ``telemetry`` does."""
    with pytest.raises(TypeError):
        spoken_alerts.alert_spec("falha.link", "condição", 3, "fonte")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        spoken_alerts.alert_spec("voo.pouso", "condição", "Texto.", "fonte")
    with pytest.raises(ValueError):
        spoken_alerts.alert_spec("falha.link", " ", "Texto.", "fonte")


@pytest.mark.parametrize("mode", ["advanced", "simple"])
def test_the_battery_return_sentence_is_listed_under_falha_bateria(mode: str) -> None:
    """The forced return of ``steps/rtl.py``, said by the mission when it narrates alone."""
    sentence = _format_telemetry_statement("Retorno por bateria crítica", {"modo": mode})
    spec = next((spec for spec in spoken_alerts.ALERT_SENTENCES if spec.text == sentence), None)
    assert spec is not None, sentence
    assert spec.category == "falha.bateria"
