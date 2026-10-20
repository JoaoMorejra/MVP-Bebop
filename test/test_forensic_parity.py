"""The station and the mission read the same report.

``bebop_mission_control/src/lib/forensics.ts`` decides the report whenever the
station drives; ``telemetry/inspection_report.py`` is what the mission narrates
on its own. The tables are extracted from the TypeScript source and compared
with the Python ones entry by entry, order included: a wording edited on one
side only would make the screen and the voice disagree between the two paths.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

from mvp_mission_bebop.telemetry import inspection_report as engine

_FORENSICS_TS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bebop_mission_control",
    "src",
    "lib",
    "forensics.ts",
)

_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _source() -> str:
    with open(_FORENSICS_TS, encoding="utf-8") as handle:
        return handle.read()


def _block(name: str) -> str:
    """Text of ``export const <name> ... ;``: one line, or up to its closing bracket at column 0."""
    source = _source()
    single = re.search(rf"^export const {name}\b[^\n]*;$", source, re.MULTILINE)
    if single:
        return single.group(0)
    match = re.search(rf"^export const {name}\b.*?^[\]\}}][^\n]*;$", source, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in forensics.ts"
    return match.group(0)


def _strings(text: str) -> List[str]:
    return [value.replace("\\'", "'") for value in _STRING.findall(text)]


def _record(name: str) -> Dict[str, List[str]]:
    body = _block(name)
    body = body[body.index("= {") + 3 :]
    entries = re.findall(r"^\s+(\w+): \[(.*?)\],?$", body, re.MULTILINE | re.DOTALL)
    assert entries, f"{name} has no entries"
    return {key: _strings(values) for key, values in entries}


def test_the_fixed_pools_are_identical():
    assert _record("FIXED_WORDINGS") == {topic: list(lines) for topic, lines in engine.FIXED_FINDINGS.items()}


def test_the_victim_pools_are_identical():
    assert _record("VICTIM_WORDINGS") == {status: list(lines) for status, lines in engine.VICTIM_FINDINGS.items()}


def test_the_connectors_are_identical():
    for ts_name, python in (
        ("OPENERS", engine.FORENSIC_OPENERS),
        ("LINKERS", engine.FORENSIC_LINKERS),
        ("CLOSERS", engine.FORENSIC_CLOSERS),
    ):
        block = _block(ts_name)
        assert _strings(block[block.index("= [") :]) == list(python), ts_name


def test_the_order_tables_are_identical():
    lead = _block("LEAD_TOPICS")
    assert _strings(lead[lead.index("= [") :]) == list(engine.LEAD_TOPICS)
    dispatch = _block("DISPATCH_ORDERS")
    pairs = re.findall(r"\[('[a-z]+'), ('[a-z]+')\]", dispatch[dispatch.index("= [") :])
    assert [tuple(_strings(" ".join(pair))) for pair in pairs] == [tuple(order) for order in engine.DISPATCH_ORDERS]
