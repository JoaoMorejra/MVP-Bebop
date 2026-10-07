"""Declarative fault injection for the driver emulator.

A fault is either a standing condition the plant consults when a command
arrives (``takeoff_rejected``, ``stuck_on_ground``, ``ignore_first_n_lands``,
``flat_trim_no_ack``, ``photo_no_ack``) or an action that fires once at an
instant (``battery_step``, ``forced_landing``). Instants are plant time, either
absolute (``at_sec``) or relative to an anchor the plant reaches
(``after``: ``takeoff`` or ``hover``, plus ``delay_sec``).

Two spellings are accepted, the JSON object and a shorthand for the command
line::

    {"kind": "forced_landing", "state": 8, "after": "hover", "delay_sec": 5}
    forced_landing:state=8,after=hover,delay_sec=5

Out of scope for this round, and refused: ``link_drop``, ``controller_hang``,
``odom_freeze`` and the rest of section 4.1.4 of the 100% plan.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Final, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from support.fake_bebop.plant import Anchors

#: Every fault this emulator implements.
FAULT_KINDS: Final[Tuple[str, ...]] = (
    "takeoff_rejected",
    "stuck_on_ground",
    "ignore_first_n_lands",
    "flat_trim_no_ack",
    "photo_no_ack",
    "battery_step",
    "forced_landing",
)

#: Faults that fire at an instant and need a trigger.
TIMED_KINDS: Final[Tuple[str, ...]] = ("battery_step", "forced_landing")

#: Anchors a timed fault can follow.
ANCHORS: Final[Tuple[str, ...]] = ("takeoff", "hover")

#: Flying states a ``forced_landing`` may impose: landing, emergency, emergency landing.
FORCED_STATES: Final[Tuple[int, ...]] = (4, 5, 8)

_ALLOWED_KEYS: Final[Dict[str, Tuple[str, ...]]] = {
    "takeoff_rejected": (),
    "stuck_on_ground": (),
    "ignore_first_n_lands": ("n",),
    "flat_trim_no_ack": (),
    "photo_no_ack": (),
    "battery_step": ("to_pct", "at_sec", "after", "delay_sec"),
    "forced_landing": ("state", "at_sec", "after", "delay_sec"),
}


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def _shorthand_value(text: str) -> Union[str, float, int]:
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text else number


@dataclass(frozen=True)
class Fault:
    """One validated fault.

    Attributes
    ----------
    kind : str
        One of :data:`FAULT_KINDS`.
    n : int
        ``ignore_first_n_lands`` count.
    target : float
        ``battery_step`` charge (percent) or ``forced_landing`` state.
    at_sec, after, delay_sec
        Trigger of a timed fault: ``at_sec`` alone, or ``after`` and ``delay_sec``.
    """

    kind: str
    n: int = 0
    target: float = 0.0
    at_sec: Optional[float] = None
    after: Optional[str] = None
    delay_sec: float = 0.0

    def value(self) -> float:
        """What a timed fault imposes: the charge or the flying state."""
        return self.target

    def trigger_time(self, anchors: Anchors) -> Optional[float]:
        """Plant instant this timed fault fires at, ``None`` while its anchor is unreached."""
        if self.at_sec is not None:
            return self.at_sec
        anchor = getattr(anchors, self.after or "", None)
        return None if anchor is None else anchor + self.delay_sec


def parse_fault(spec: Union[str, Mapping[str, Any]]) -> Fault:
    """Validate one fault given as JSON, shorthand or a mapping.

    Parameters
    ----------
    spec : str or Mapping
        ``{"kind": ...}`` as JSON text or a mapping, or ``kind[:key=value,...]``.

    Returns
    -------
    Fault

    Raises
    ------
    TypeError
        If ``spec`` is neither a string nor a mapping, or a field has the wrong type.
    ValueError
        If the kind is unknown, a key is not allowed for it, a value is out of
        range, or a timed fault has no single trigger.
    """
    if isinstance(spec, str):
        text = spec.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise ValueError(f"fault is not valid JSON: {text!r}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"fault JSON must be an object: {text!r}")
        else:
            kind, _, rest = text.partition(":")
            data = {"kind": kind.strip()}
            for item in filter(None, (part.strip() for part in rest.split(","))):
                key, sep, value = item.partition("=")
                if not sep:
                    raise ValueError(f"fault option {item!r} is not key=value")
                data[key.strip()] = _shorthand_value(value.strip())
    elif isinstance(spec, Mapping):
        data = dict(spec)
    else:
        raise TypeError(f"fault must be a string or a mapping, got {type(spec).__name__}")

    kind = data.pop("kind", None)
    if kind not in FAULT_KINDS:
        raise ValueError(f"unknown fault {kind!r}; this emulator implements {', '.join(FAULT_KINDS)}")
    unexpected = set(data) - set(_ALLOWED_KEYS[kind])
    if unexpected:
        raise ValueError(f"{kind} does not take {', '.join(sorted(unexpected))}")

    if kind == "ignore_first_n_lands":
        n = data.get("n")
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError(f"ignore_first_n_lands needs an integer n, got {n!r}")
        if n < 1:
            raise ValueError(f"ignore_first_n_lands needs n >= 1, got {n}")
        return Fault(kind, n=n)
    if kind not in TIMED_KINDS:
        return Fault(kind)

    if kind == "battery_step":
        if "to_pct" not in data:
            raise ValueError("battery_step needs to_pct")
        target = _finite("to_pct", data["to_pct"])
        if not 0.0 <= target <= 100.0:
            raise ValueError(f"battery_step to_pct must lie in [0, 100], got {target}")
    else:
        state = data.get("state")
        if isinstance(state, bool) or not isinstance(state, int) or state not in FORCED_STATES:
            raise ValueError(f"forced_landing state must be one of {FORCED_STATES}, got {state!r}")
        target = float(state)

    at_sec = data.get("at_sec")
    after = data.get("after")
    if (at_sec is None) == (after is None):
        raise ValueError(f"{kind} needs exactly one trigger: at_sec, or after with delay_sec")
    if at_sec is not None:
        at = _finite("at_sec", at_sec)
        if at < 0.0:
            raise ValueError(f"at_sec must be non-negative, got {at}")
        return Fault(kind, target=target, at_sec=at)
    if after not in ANCHORS:
        raise ValueError(f"after must be one of {ANCHORS}, got {after!r}")
    delay = _finite("delay_sec", data.get("delay_sec", 0.0))
    if delay < 0.0:
        raise ValueError(f"delay_sec must be non-negative, got {delay}")
    return Fault(kind, target=target, after=after, delay_sec=delay)


@dataclass
class FaultSchedule:
    """The faults of one emulator run, as the plant's ``FaultPolicy``.

    Parameters
    ----------
    faults : iterable of Fault
    """

    faults: Sequence[Fault] = ()
    _lands_ignored: int = field(default=0, init=False)
    _fired: List[int] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.faults = tuple(self.faults)
        for fault in self.faults:
            if not isinstance(fault, Fault):
                raise TypeError(f"expected Fault, got {type(fault).__name__}")

    @classmethod
    def from_json(cls, text: str) -> "FaultSchedule":
        """A schedule from a JSON list of fault objects, or ``{"faults": [...]}``."""
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"fault schedule is not valid JSON: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("faults")
        if not isinstance(data, list):
            raise ValueError("fault schedule must be a list of faults")
        return cls([parse_fault(item) for item in data])

    @classmethod
    def from_specs(cls, specs: Iterable[str]) -> "FaultSchedule":
        """A schedule from command-line ``--fault`` values."""
        return cls([parse_fault(spec) for spec in specs])

    def _has(self, kind: str) -> bool:
        return any(fault.kind == kind for fault in self.faults)

    def rejects_takeoff(self) -> bool:
        return self._has("takeoff_rejected")

    def stuck_on_ground(self) -> bool:
        return self._has("stuck_on_ground")

    def ignores_land(self) -> bool:
        """Whether this land request is one of the first ``n`` to ignore; counts it if so."""
        limit = sum(fault.n for fault in self.faults if fault.kind == "ignore_first_n_lands")
        if self._lands_ignored < limit:
            self._lands_ignored += 1
            return True
        return False

    def acknowledges_flat_trim(self) -> bool:
        return not self._has("flat_trim_no_ack")

    def acknowledges_photo(self) -> bool:
        return not self._has("photo_no_ack")

    def due(self, t: float, anchors: Anchors) -> Sequence[Fault]:
        """Timed faults whose instant has come, each returned once."""
        ready: List[Fault] = []
        for index, fault in enumerate(self.faults):
            if fault.kind not in TIMED_KINDS or index in self._fired:
                continue
            when = fault.trigger_time(anchors)
            if when is not None and t >= when - 1e-9:
                self._fired.append(index)
                ready.append(fault)
        return ready

    def describe(self) -> List[Dict[str, Any]]:
        """The schedule as plain data, for the emulator's JSON log."""
        return [
            {k: v for k, v in vars(fault).items() if v not in (None, 0, 0.0) or k == "kind"}
            for fault in self.faults
        ]
