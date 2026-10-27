"""Alert sentences the mission can hand the ground station, as a finite list.

Under the station the mission has no voice of its own
(``announcer.station_narrates``): an urgent announcement leaves as an
``[ALERT <key>]`` line whose ``text`` is the sentence
:func:`announcer._format_telemetry_statement` composed, and the station speaks
that string verbatim. Without the network it is heard only if its audio was
synthesized ahead into the phrase cache, so every sentence a call site can
produce is listed here, composed by the same formatter from the same arguments
the call site passes. ``streamer/precache_speech.py`` merges the list with the
station's own manifest (``src/lib/spokenCatalog.ts``).

Call sites whose sentence carries a measured or operator-chosen value, and
narration the station drops (``announcer._hand_to_station`` forwards only
urgent announcements), are not listed, except standalone-only narration whose
sentence is also a station pool variant (the forced battery return), listed so
both voices share one cached audio.
"""

from __future__ import annotations

from typing import Any, Dict, Final, FrozenSet, List, NamedTuple, Optional, Set, Tuple

from mvp_mission_bebop.mission_mode import spoken_stage_five_name
from mvp_mission_bebop.parameters import NetworkConfig
from mvp_mission_bebop.telemetry.announcer import (
    _GROUND_FAULT_ACTIONS,
    BATTERY_RETURN_ACTION,
    _format_telemetry_statement,
)
from mvp_mission_bebop.telemetry.failsafe import _SPOKEN_REASONS, _UNKNOWN_REASON

#: Failure categories of ``SpeechCategory`` in ``src/lib/spokenCatalog.ts``.
ALERT_CATEGORIES: Final[FrozenSet[str]] = frozenset(
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

#: Stages 1 to 4 as ``MissionRunner._announce_failure`` names them. Mirrors
#: ``engine.runner._SPOKEN_STAGE_NAMES``, which this module does not import
#: (the runner pulls the Nectar runtime); ``test/test_spoken_alerts.py`` holds
#: the two equal. Stage 5 comes from :func:`spoken_stage_five_name`.
SPOKEN_STAGE_NAMES: Final[Tuple[Tuple[int, str], ...]] = (
    (1, "Decolagem"),
    (2, "Varredura"),
    (3, "Aproximação"),
    (4, "Inspeção"),
)

#: Category of each failsafe clause of ``failsafe._SPOKEN_REASONS``; a clause
#: not named here (the unknown reason included) is an aborted flight.
_FAILSAFE_CATEGORY: Final[Dict[str, str]] = {
    "odometria nunca recebida": "falha.link",
    "perda da telemetria de odometria": "falha.link",
    "perda do vídeo da câmera": "falha.link",
    "bateria no limiar de pouso": "falha.bateria",
}

#: Category of each bare ground-fault action of ``announcer._GROUND_FAULT_ACTIONS``.
_GROUND_FAULT_CATEGORY: Final[Dict[str, str]] = {
    "falha na inicialização": "falha.link",
    "falha na calibração": "falha.calibracao",
    "falha na decolagem": "falha.decolagem",
    "falha na etapa": "falha.aborto",
    "nivelamento sem confirmação": "falha.calibracao",
}


class AlertSpec(NamedTuple):
    """One alert sentence and when the station speaks it.

    Attributes
    ----------
    category : str
        One of :data:`ALERT_CATEGORIES`.
    condition : str
        The trigger, in pt-BR, as the speech table states it.
    text : str
        The exact string the station receives in the alert payload.
    source : str
        Call site that raises it, ``file:function``.
    """

    category: str
    condition: str
    text: str
    source: str = ""


def alert_spec(category: str, condition: str, text: str, source: str) -> AlertSpec:
    """Build a validated :class:`AlertSpec`.

    Parameters
    ----------
    category : str
        One of :data:`ALERT_CATEGORIES`.
    condition : str
        Non-blank trigger description.
    text : str
        Non-blank sentence.
    source : str
        Non-blank call site.

    Returns
    -------
    AlertSpec

    Raises
    ------
    TypeError
        If any field is not a ``str``.
    ValueError
        If ``category`` is not a failure category or another field is blank.
    """
    fields = {"category": category, "condition": condition, "text": text, "source": source}
    for name, value in fields.items():
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a str, got {type(value).__name__}")
        if not value.strip():
            raise ValueError(f"{name} must not be blank")
    if category not in ALERT_CATEGORIES:
        raise ValueError(f"category '{category}' is not one of: {', '.join(sorted(ALERT_CATEGORIES))}")
    return AlertSpec(category=category, condition=condition, text=text, source=source)


def _composed(
    category: str, condition: str, source: str, action: str, details: Optional[Dict[str, Any]] = None
) -> AlertSpec:
    """The spec of the sentence ``announce_sync(action, details)`` hands the station."""
    return alert_spec(category, condition, _format_telemetry_statement(action, details), source)


def _airborne_label(airborne: bool) -> str:
    return "com a aeronave em voo" if airborne else "com a aeronave em solo"


def _build() -> Tuple[AlertSpec, ...]:
    """Compose every fixed alert sentence from its call site's arguments.

    Each sentence is produced by :func:`announcer._format_telemetry_statement`
    with the ``(action, details)`` the call site passes, so the list follows
    the formatter rather than restating its output.

    Returns
    -------
    Tuple[AlertSpec, ...]
        One spec per distinct text, in call-site order: driver connection,
        takeoff step refusals, failsafe clauses in flight and on the ground,
        runner abort, the forced battery return of each profile, stage
        failures, then the bare ground-fault forms. A text
        composed twice keeps its first spec.
    """
    specs: List[AlertSpec] = []

    specs.append(
        _composed(
            "falha.link",
            "Driver do drone inacessível no endereço padrão do Bebop 2; missão encerrada antes do voo",
            "mvp_mission_bebop/mission.py:main",
            "Falha na inicialização",
            {"erro": f"Falha de conexão com driver do drone no IP {NetworkConfig().drone_ip}"},
        )
    )

    specs.extend(
        (
            _composed(
                "falha.calibracao",
                "Nivelamento (flat trim) não confirmado pela aeronave; decolagem cancelada",
                "mvp_mission_bebop/steps/takeoff.py:_flat_trim",
                "Nivelamento sem confirmação",
                {"etapa": "a aeronave não confirmou o nivelamento, decolagem cancelada"},
            ),
            _composed(
                "falha.calibracao",
                "Referência de altitude de solo recusada; decolagem cancelada",
                "mvp_mission_bebop/steps/takeoff.py:_calibrate",
                "Falha na calibração",
                {"etapa": "falha na calibração de referência de solo, decolagem cancelada"},
            ),
            _composed(
                "falha.decolagem",
                "Comando de decolagem rejeitado pela controladora de voo",
                "mvp_mission_bebop/steps/takeoff.py:_command_takeoff",
                "Falha na decolagem",
                {"etapa": "decolagem rejeitada pela controladora de voo", "em_voo": False},
            ),
            _composed(
                "falha.decolagem",
                "Decolagem não confirmada pela aeronave dentro do prazo (R1)",
                "mvp_mission_bebop/steps/takeoff.py:_confirm_takeoff",
                "Falha na decolagem",
                {"etapa": "decolagem não confirmada pela aeronave", "em_voo": False},
            ),
        )
    )

    clauses: List[str] = []
    for _prefix, clause in _SPOKEN_REASONS:
        if clause not in clauses:
            clauses.append(clause)
    clauses.append(_UNKNOWN_REASON)
    for clause in clauses:
        for airborne in (True, False):
            specs.append(
                _composed(
                    _FAILSAFE_CATEGORY.get(clause, "falha.aborto"),
                    f"Failsafe da missão: {clause}, {_airborne_label(airborne)}",
                    "mvp_mission_bebop/telemetry/failsafe.py:trigger_emergency_land",
                    "Falha de segurança",
                    {"erro": clause, "em_voo": airborne},
                )
            )

    specs.append(
        _composed(
            "falha.aborto",
            "Etapa interrompida por aborto (StepStatus.ABORTED); pouso comandado pela missão",
            "mvp_mission_bebop/engine/runner.py:run",
            "Missão abortada",
            {"etapa": "missão abortada, pousando drone"},
        )
    )

    for mode in ("advanced", "simple"):
        specs.append(
            _composed(
                "falha.bateria",
                f"Retorno forçado por bateria crítica, modo {'avançado' if mode == 'advanced' else 'simples'}, "
                "com a missão narrando sozinha (sem a estação)",
                "mvp_mission_bebop/steps/rtl.py:_announce_battery_return",
                BATTERY_RETURN_ACTION,
                {"modo": mode},
            )
        )

    stage_names = [name for _stage, name in SPOKEN_STAGE_NAMES]
    stage_names.extend((spoken_stage_five_name(True), spoken_stage_five_name(False)))
    for name in stage_names:
        for airborne in (True, False):
            specs.append(
                _composed(
                    "falha.aborto",
                    f"Etapa {name} falhou sem alerta próprio, {_airborne_label(airborne)}",
                    "mvp_mission_bebop/engine/runner.py:_announce_failure",
                    "Falha na etapa",
                    {"etapa": name, "em_voo": airborne},
                )
            )

    for action in _GROUND_FAULT_ACTIONS:
        for details in (None, {"em_voo": True}):
            specs.append(
                _composed(
                    _GROUND_FAULT_CATEGORY[action],
                    "Forma do formatador sem motivo informado"
                    + (", com a aeronave em voo" if details else ""),
                    "mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement",
                    action.capitalize(),
                    details,
                )
            )

    unique: List[AlertSpec] = []
    seen: Set[str] = set()
    for spec in specs:
        if spec.text in seen:
            continue
        seen.add(spec.text)
        unique.append(spec)
    return tuple(unique)


#: Every fixed alert sentence, in a stable order.
ALERT_SENTENCES: Final[Tuple[AlertSpec, ...]] = _build()


def all_alert_sentences() -> Tuple[str, ...]:
    """The texts of :data:`ALERT_SENTENCES`, in order.

    Returns
    -------
    Tuple[str, ...]
        Each sentence once, exactly as the station receives it.
    """
    return tuple(spec.text for spec in ALERT_SENTENCES)
