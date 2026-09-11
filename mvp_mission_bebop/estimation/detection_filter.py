"""Temporal persistence filtering for detections.

The search stage used a single counter that incremented on a hit and reset to
zero on any miss. That is a persistence filter for acquisition only: once a
target is confirmed, one dropped frame discards the confirmation entirely. With
a detector running on a vibrating airframe, dropped frames are the normal case,
not the exception.

Separating the two thresholds gives acquisition and loss independent hysteresis:
a target must be seen consistently to be confirmed, and must be missing
consistently to be released.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConfirmationState(Enum):
    """Whether a target is currently held."""

    SEARCHING = "searching"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class ConfirmationReport:
    """Outcome of one observation."""

    state: ConfirmationState
    #: True on the cycle the target transitions to confirmed.
    just_confirmed: bool
    #: True on the cycle the target transitions back to searching.
    just_released: bool
    hits: int
    misses: int

    @property
    def confirmed(self) -> bool:
        """True while the target is held."""
        return self.state is ConfirmationState.CONFIRMED


class HysteresisConfirmer:
    """Dual-threshold persistence filter over a boolean detection stream."""

    __slots__ = ("_confirm_frames", "_release_frames", "_hits", "_misses", "_state")

    def __init__(self, confirm_frames: int, release_frames: int) -> None:
        if confirm_frames < 1:
            raise ValueError(f"confirm_frames must be at least 1, got {confirm_frames!r}")
        if release_frames < 1:
            raise ValueError(f"release_frames must be at least 1, got {release_frames!r}")

        self._confirm_frames = confirm_frames
        self._release_frames = release_frames
        self._hits = 0
        self._misses = 0
        self._state = ConfirmationState.SEARCHING

    @property
    def state(self) -> ConfirmationState:
        """Current confirmation state."""
        return self._state

    @property
    def confirmed(self) -> bool:
        """True while the target is held."""
        return self._state is ConfirmationState.CONFIRMED

    @property
    def hits(self) -> int:
        """Consecutive detections observed."""
        return self._hits

    def reset(self) -> None:
        """Return to the searching state with both counters cleared."""
        self._hits = 0
        self._misses = 0
        self._state = ConfirmationState.SEARCHING

    def update(self, detected: bool) -> ConfirmationReport:
        """Absorb one observation and report the resulting state."""
        just_confirmed = False
        just_released = False

        if detected:
            self._hits += 1
            self._misses = 0
            if self._state is ConfirmationState.SEARCHING and self._hits >= self._confirm_frames:
                self._state = ConfirmationState.CONFIRMED
                just_confirmed = True
        else:
            self._misses += 1
            self._hits = 0
            if self._state is ConfirmationState.CONFIRMED and self._misses >= self._release_frames:
                self._state = ConfirmationState.SEARCHING
                just_released = True

        return ConfirmationReport(
            state=self._state,
            just_confirmed=just_confirmed,
            just_released=just_released,
            hits=self._hits,
            misses=self._misses,
        )
