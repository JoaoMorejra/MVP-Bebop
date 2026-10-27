"""Guard against a native text-to-speech engine entering the station.

The copilot speaks only in the Gemini Live voice, from synthesis or from the
phrase cache. A local engine would be a second, different voice, so none may
be referenced by the mission package, the streamer or the ground station.
The engine names are assembled at run time so this file does not match itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, List, Tuple

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
BMG_ROOT: Final[Path] = REPO_ROOT / "bebop_mission_control"
SCAN_ROOTS: Final[Tuple[Path, ...]] = (
    REPO_ROOT / "mvp_mission_bebop",
    BMG_ROOT / "streamer",
    BMG_ROOT / "electron",
    BMG_ROOT / "src",
)
EXTENSIONS: Final[frozenset[str]] = frozenset({".py", ".ts", ".tsx", ".cjs", ".js", ".mjs", ".json", ".sh"})
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset({"node_modules", "dist", "__pycache__"})
BANNED_ENGINES: Final[Tuple[str, ...]] = (
    "".join(("es", "peak")),
    "".join(("spd", "-say")),
    "".join(("pytt", "sx3")),
    "".join(("fest", "ival")),
    "".join(("fl", "ite")),
)


def _collect(root: Path) -> List[Path]:
    """Return the scannable files under ``root``.

    Parameters
    ----------
    root : Path
        Directory to walk. A missing directory yields an empty list.

    Returns
    -------
    List[Path]
        Files with a scanned extension, excluding build and cache directories.
    """
    if not root.is_dir():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in EXTENSIONS
        and not EXCLUDED_DIRS.intersection(path.relative_to(root).parts)
    ]


def test_scan_roots_exist() -> None:
    """Assert that every scanned root is present, so a move cannot empty the scan."""
    assert [root for root in SCAN_ROOTS if not root.is_dir()] == []


def test_no_native_tts_engine_is_referenced() -> None:
    """Assert that no scanned file names a native speech engine.

    Fails with ``path: engine`` pairs relative to the repository root.
    """
    offenders: List[str] = []
    for root in SCAN_ROOTS:
        for path in _collect(root):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            offenders.extend(
                f"{path.relative_to(REPO_ROOT)}: {engine}" for engine in BANNED_ENGINES if engine in text
            )
    assert sorted(offenders) == []
