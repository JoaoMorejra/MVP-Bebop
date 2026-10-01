"""Process exit status of ``mission.py``: the mission outcome on the wire.

The ground station reads nothing but the exit status to decide how a flight
ended (``useMissionRuntime.ts`` via ``src/lib/missionOutcome.ts``). A single
``0 or 1`` made an operator abort and an unconfirmed touchdown both read as a
completed mission, and the station announced a safe landing at base for each.
Each outcome now has its own status; the values are an IPC contract pinned by
``test/test_contracts.py``.
"""

from __future__ import annotations

from typing import Final, Optional

#: Every configured stage succeeded and, if the run landed, touchdown was confirmed.
EXIT_COMPLETE: Final[int] = 0

#: A stage failed, the pipeline raised, or initialisation did not complete.
EXIT_FAILURE: Final[int] = 1

#: The operator aborted; the controlled landing burst was transmitted.
EXIT_ABORTED_LANDED: Final[int] = 3

#: The return leg commanded the landing but odometry never confirmed touchdown.
EXIT_TOUCHDOWN_UNCONFIRMED: Final[int] = 4

#: The operator insisted with a second interrupt after the landing burst.
FORCED_EXIT_CODE: Final[int] = 130


def mission_exit_code(
    succeeded: bool, *, aborted: bool, touchdown_confirmed: Optional[bool]
) -> int:
    """Reduce a finished run to its exit status.

    Parameters
    ----------
    succeeded : bool
        Whether every configured step reported success.
    aborted : bool
        Whether an operator interrupt was received. Takes precedence: an
        aborted run is reported as aborted however far it got.
    touchdown_confirmed : bool or None
        Outcome of the return leg's touchdown; ``None`` when the run did not
        land (a partial bench run without Stage 5, or a failure before it).

    Returns
    -------
    int
        One of :data:`EXIT_COMPLETE`, :data:`EXIT_FAILURE`,
        :data:`EXIT_ABORTED_LANDED`, :data:`EXIT_TOUCHDOWN_UNCONFIRMED`.
    """
    if aborted:
        return EXIT_ABORTED_LANDED
    if not succeeded:
        return EXIT_FAILURE
    if touchdown_confirmed is False:
        return EXIT_TOUCHDOWN_UNCONFIRMED
    return EXIT_COMPLETE
