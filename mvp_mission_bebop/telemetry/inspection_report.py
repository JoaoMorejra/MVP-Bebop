"""Forensic report engine: four findings, the victim line decided by inference.

The report clears four topics -- police, ambulance (SAMU), the victim and the
vehicle. Police, SAMU and the vehicle are fixed clearances: only the variant
drawn changes, and every vehicle wording states that a tow truck was called to
clear the road. The victim line is
the one finding that depends on evidence, and it is drawn from the pool of the
inspection frame's :class:`~mvp_mission_bebop.perception.assessment.VictimStatus`:

* ``detected``: conscious and without apparent injuries;
* ``not_detected``: explicitly no visible victim;
* ``unknown``: neutral. Without a completed inference nothing is stated about
  the victim -- neither presence, absence, consciousness nor injuries.

Order: the report opens with the victim when one was detected and with the
vehicle otherwise (``not_detected`` and ``unknown``), then the other of the two,
then SAMU and police in an order that differs from the previous flight's. Wordings avoid
the variants of the last :data:`HISTORY_WINDOW` flights, with the semantics of
``pickVariant`` in ``bebop_mission_control/src/lib/copilotPhrases.ts``. The
station holds the same tables and algorithm in ``src/lib/forensics.ts``.

Cross-flight memory lives in ``inspection_history.json`` in the mission's
output directory, written atomically. A missing or corrupt file reads as no
history: at worst one line repeats, the report is still drawn.
"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Dict, Final, List, Mapping, Optional, Sequence, Tuple, Union

from mvp_mission_bebop.perception.assessment import InspectionAssessment, VictimStatus

logger = logging.getLogger("InspectionReport")

#: History file name, in the mission's ``output_dir``.
HISTORY_FILENAME: Final[str] = "inspection_history.json"

#: Schema tag of the history document.
HISTORY_SCHEMA: Final[str] = "bmg.inspection_history.v1"

#: Flights of memory per history key.
HISTORY_WINDOW: Final[int] = 5

#: The four topics, in the canonical enumeration.
TOPICS: Final[Tuple[str, ...]] = ("police", "samu", "victim", "vehicle")

#: Topics that may open the report.
LEAD_TOPICS: Final[Tuple[str, str]] = ("vehicle", "victim")

#: Orders of the dispatch topics, as indexed by ``finding.dispatch_order``.
DISPATCH_ORDERS: Final[Tuple[Tuple[str, str], ...]] = (("samu", "police"), ("police", "samu"))

#: Fixed clearances. Every vehicle wording states that a tow truck was called
#: to clear the road, by order of the station's operator.
FIXED_FINDINGS: Final[Dict[str, Tuple[str, ...]]] = {
    "police": (
        "Sem necessidade de polícia",
        "Acionamento policial dispensado",
        "Sem demanda para autoridade policial",
        "Segurança pública não requisitada",
    ),
    "samu": (
        "Sem necessidade de Samu",
        "Socorro médico dispensado",
        "Atendimento emergencial não necessário",
        "SAMU dispensado para a ocorrência",
    ),
    "vehicle": (
        "Veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via",
        "Veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via",
        "Sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via",
        "Integridade do automóvel preservada. Guincho acionado para desobstrução da via",
        "Veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via",
    ),
}

#: Victim line per :class:`VictimStatus`. The ``unknown`` pool states nothing
#: about presence, absence, consciousness or injuries.
VICTIM_FINDINGS: Final[Dict[str, Tuple[str, ...]]] = {
    VictimStatus.DETECTED.value: (
        "Vítima consciente e sem ferimentos aparentes",
        "Acidentado consciente, sem ferimentos aparentes",
        "Vítima identificada, consciente e sem lesões aparentes",
        "Estado do acidentado não é grave, sem ferimentos aparentes",
        "Acidentado consciente e sem ferimentos críticos aparentes",
    ),
    VictimStatus.NOT_DETECTED.value: (
        "Nenhuma vítima visível na cena",
        "Sem vítimas visíveis no local",
        "Nenhum acidentado identificado na imagem",
        "Cena sem vítimas aparentes",
        "Ausência de vítimas visíveis na imagem",
        "Nenhuma vítima visível no local",
    ),
    VictimStatus.UNKNOWN.value: (
        "Avaliação da vítima não incluída neste laudo preliminar",
        "Condição da vítima a confirmar em verificação presencial",
        "Sem dados para avaliar a vítima neste registro",
        "Avaliação da vítima pendente de conferência no local",
    ),
}

#: Every wording per topic; the victim entry is the union of its three pools.
FORENSIC_FINDINGS: Final[Dict[str, Tuple[str, ...]]] = {
    "police": FIXED_FINDINGS["police"],
    "samu": FIXED_FINDINGS["samu"],
    "victim": tuple(line for pool in VICTIM_FINDINGS.values() for line in pool),
    "vehicle": FIXED_FINDINGS["vehicle"],
}

#: What leads each finding when read, so the report is talked through rather
#: than listed. Same pools as ``src/lib/forensics.ts``; every finding is a
#: clearance, so the connectors add and never contrast.
FORENSIC_OPENERS: Final[tuple] = (
    "Para começar,",
    "De início,",
    "Logo de cara,",
    "Começando pelo essencial,",
    "De saída,",
    "Abrindo a análise,",
)
FORENSIC_LINKERS: Final[tuple] = (
    "Além disso,",
    "Na sequência,",
    "Somado a isso,",
    "Em seguida,",
    "Outro ponto:",
    "Também observamos:",
    "E mais:",
)
FORENSIC_CLOSERS: Final[tuple] = (
    "Por fim,",
    "Para fechar,",
    "Para arrematar,",
    "E, para concluir,",
    "Encerrando,",
    "Por último,",
)

History = Dict[str, List[int]]


def _decapitalise(line: str) -> str:
    """Lower the first letter so a line reads on after an ordinal.

    An acronym is left alone: "SAMU dispensado" must not become "sAMU".
    """
    if len(line) > 1 and line[1].isupper():
        return line
    return line[0].lower() + line[1:]


def history_path_for(output_dir: str) -> str:
    """Path of the history file in ``output_dir``.

    Raises
    ------
    TypeError
        If ``output_dir`` is not a string.
    """
    if not isinstance(output_dir, str):
        raise TypeError(f"output_dir must be a string, got {type(output_dir).__name__}")
    return os.path.join(output_dir or ".", HISTORY_FILENAME)


def victim_status_of(assessment: Optional[InspectionAssessment]) -> VictimStatus:
    """Victim status of ``assessment``; ``unknown`` without one.

    Raises
    ------
    TypeError
        If ``assessment`` is neither ``None`` nor an :class:`InspectionAssessment`.
    """
    if assessment is None:
        return VictimStatus.UNKNOWN
    if not isinstance(assessment, InspectionAssessment):
        raise TypeError(f"expected an InspectionAssessment, got {type(assessment).__name__}")
    return assessment.victim_status


def _coerce_status(victim_status: Union[VictimStatus, str]) -> VictimStatus:
    if isinstance(victim_status, VictimStatus):
        return victim_status
    if not isinstance(victim_status, str):
        raise TypeError(f"victim_status must be a VictimStatus or str, got {type(victim_status).__name__}")
    try:
        return VictimStatus(victim_status)
    except ValueError as exc:
        raise ValueError(f"unknown victim_status {victim_status!r}") from exc


def load_history(path: Optional[str]) -> History:
    """Read the history document, keeping only well-formed entries.

    Never raises: ``None``, a missing file or a corrupt one reads as no history.
    """
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Inspection history unreadable (%s); starting without memory.", exc)
        return {}
    if not isinstance(document, Mapping) or document.get("schema") != HISTORY_SCHEMA:
        return {}
    entries = document.get("history")
    if not isinstance(entries, Mapping):
        return {}
    history: History = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        kept = [item for item in value if isinstance(item, int) and not isinstance(item, bool) and item >= 0]
        if kept:
            history[key] = kept[-HISTORY_WINDOW:]
    return history


def save_history(path: str, history: History) -> bool:
    """Write the history atomically: temporary file, then rename.

    Returns
    -------
    bool
        ``False`` when the write failed; the report already drawn stands.
    """
    directory = os.path.dirname(path) or "."
    base, extension = os.path.splitext(os.path.basename(path))
    temporary = os.path.join(directory, f".{base}.tmp{extension}")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"schema": HISTORY_SCHEMA, "history": history}, handle, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("Inspection history not saved at %s: %s", path, exc)
        try:
            os.remove(temporary)
        except OSError:
            pass
        return False
    return True


def pick_variant(key: str, size: int, history: History, rng: random.Random) -> int:
    """Draw an index of a pool of ``size``, avoiding the recent ones of ``key``.

    The indices recorded for the last :data:`HISTORY_WINDOW` flights are
    excluded; a pool too small for that excludes the most recent ``size - 1``,
    so it never repeats the previous flight's choice and never fails. Recorded
    indices outside the pool are disregarded. Mirrors ``pickVariant``.

    Raises
    ------
    ValueError
        If ``size`` is not positive.
    """
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError(f"pool for {key} must hold at least one variant, got {size!r}")
    recorded = [index for index in history.get(key, []) if index < size]
    excludable = min(HISTORY_WINDOW, size - 1)
    excluded = set(recorded[-excludable:]) if excludable > 0 else set()
    candidates = [index for index in range(size) if index not in excluded]
    return candidates[rng.randrange(len(candidates))]


def record_variant(history: History, key: str, index: int) -> History:
    """Append one flight's pick for ``key``, dropping the oldest beyond the window. Pure."""
    updated = {name: list(entries) for name, entries in history.items()}
    updated[key] = [*updated.get(key, []), index][-HISTORY_WINDOW:]
    return updated


def lead_topics_for(victim_status: VictimStatus) -> Tuple[str, str]:
    """First two topics of the report for a victim status.

    Parameters
    ----------
    victim_status : VictimStatus
        Status of the inspection frame's assessment.

    Returns
    -------
    Tuple[str, str]
        ``("victim", "vehicle")`` when a victim was detected; ``("vehicle",
        "victim")`` for ``not_detected`` and for ``unknown``, where the
        inference did not conclude and nothing is asserted about the victim
        before the vehicle line.

    Raises
    ------
    TypeError
        If ``victim_status`` is not a :class:`VictimStatus`.
    """
    if not isinstance(victim_status, VictimStatus):
        raise TypeError(f"victim_status must be a VictimStatus, got {type(victim_status).__name__}")
    if victim_status is VictimStatus.DETECTED:
        return ("victim", "vehicle")
    return ("vehicle", "victim")


def check_topic_order(topics: Sequence[str]) -> None:
    """Enforce the report order invariants.

    Raises
    ------
    ValueError
        Unless ``topics`` holds the four topics exactly once each and opens
        with the vehicle or the victim.
    """
    if len(topics) != len(TOPICS) or set(topics) != set(TOPICS):
        raise ValueError(f"a report covers {TOPICS} exactly once each, got {list(topics)}")
    if topics[0] not in LEAD_TOPICS:
        raise ValueError(f"a report opens with {LEAD_TOPICS}, never {topics[0]!r}")


def build_inspection_report(
    victim_status: Union[VictimStatus, str],
    *,
    history_path: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, str]]:
    """Draw one report and record it in the history.

    The opening topics follow the victim status and are not drawn. Draw order
    is dispatch order, the fixed wordings, the connectors and last the victim
    wording, so for one seed and one history the three branches differ in the
    victim line (and the opening order it implies) only.

    Parameters
    ----------
    victim_status : Union[VictimStatus, str]
        Pool the victim line is drawn from.
    history_path : Optional[str]
        History file; ``None`` draws without cross-flight memory.
    rng : Optional[random.Random]
        Source of randomness; a fresh one by default.

    Returns
    -------
    List[Dict[str, str]]
        One dict per finding, in reading order, with ``topic``, ``card`` (the
        line the station displays) and ``speech`` (the same line led by its
        connector and closed by a full stop).

    Raises
    ------
    TypeError, ValueError
        On an invalid ``victim_status``.
    """
    status = _coerce_status(victim_status)
    rng = rng if rng is not None else random.Random()
    history = load_history(history_path)

    dispatch_index = pick_variant("finding.dispatch_order", len(DISPATCH_ORDERS), history, rng)
    history = record_variant(history, "finding.dispatch_order", dispatch_index)
    topics = [*lead_topics_for(status), *DISPATCH_ORDERS[dispatch_index]]
    check_topic_order(topics)

    cards: Dict[str, str] = {}
    for topic in ("police", "samu", "vehicle"):
        key = f"finding.{topic}"
        index = pick_variant(key, len(FIXED_FINDINGS[topic]), history, rng)
        history = record_variant(history, key, index)
        cards[topic] = FIXED_FINDINGS[topic][index]

    linkers = rng.sample(FORENSIC_LINKERS, len(topics) - 2)
    connectors = [rng.choice(FORENSIC_OPENERS), *linkers, rng.choice(FORENSIC_CLOSERS)]

    victim_pool = VICTIM_FINDINGS[status.value]
    victim_key = f"finding.victim.{status.value}"
    victim_index = pick_variant(victim_key, len(victim_pool), history, rng)
    history = record_variant(history, victim_key, victim_index)
    cards["victim"] = victim_pool[victim_index]

    if history_path is not None:
        save_history(history_path, history)

    return [
        {"topic": topic, "card": cards[topic], "speech": f"{connector} {_decapitalise(cards[topic])}."}
        for topic, connector in zip(topics, connectors)
    ]
