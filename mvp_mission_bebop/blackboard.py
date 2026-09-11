"""Typed inter-stage state.

Stages used to communicate through an untyped ``Dict[str, Any]``. Nothing
declared which keys existed, so three of them were written and never read
(``last_confirmed_tilt_deg``, ``raw_evidence_path``, ``annotated_evidence_path``)
and a typo in a key name would have failed silently as a missing gate rather
than as an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class EvidenceRecord:
    """Artefacts produced by the nadir inspection stage."""

    raw_path: Optional[str] = None
    annotated_path: Optional[str] = None
    metadata_path: Optional[str] = None
    timestamp: Optional[str] = None
    detections: List[Dict[str, object]] = field(default_factory=list)

    @property
    def captured(self) -> bool:
        """True once at least one image has been written to disk."""
        return self.raw_path is not None or self.annotated_path is not None


@dataclass
class MissionBlackboard:
    """State handed between sequential stages."""

    #: Set by the search stage once a target clears the confirmation filter.
    #: Gates the visual servoing stage.
    target_confirmed: bool = False
    #: Gimbal tilt at the moment of confirmation, in degrees.
    confirmed_tilt_deg: Optional[float] = None
    #: Target centre in image coordinates at confirmation.
    confirmed_target_px: Optional[Tuple[float, float]] = None
    #: Set by the visual servoing stage once nadir alignment is achieved.
    #: Gates the inspection stage.
    approach_finished: bool = False
    #: Forensic artefacts from the inspection stage.
    evidence: EvidenceRecord = field(default_factory=EvidenceRecord)
    #: Set once the return leg has landed, so the runner's cleanup does not
    #: issue a second, redundant landing command.
    rtl_completed: bool = False
