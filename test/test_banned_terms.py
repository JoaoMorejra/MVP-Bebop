"""Guard against a banned word re-entering sources, tests and operator-facing text."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, List

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
BMG_ROOT: Final[Path] = REPO_ROOT / "bebop_mission_control"
SCAN_ROOTS: Final[List[Path]] = [
    REPO_ROOT / "mvp_mission_bebop",
    REPO_ROOT / "test",
    REPO_ROOT / "scripts",
    BMG_ROOT / "src",
    BMG_ROOT / "electron",
    BMG_ROOT / "streamer",
    BMG_ROOT / "docs",
    BMG_ROOT / "scripts",
    BMG_ROOT / "config",
]
EXTRA_FILES: Final[List[Path]] = [BMG_ROOT / "docs" / "TABELA_DE_FALAS_COPILOTO.md"]
EXTENSIONS: Final[frozenset[str]] = frozenset({".ts", ".tsx", ".cjs", ".py", ".json"})
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset({"node_modules", "dist", "__pycache__"})
BANNED: Final[re.Pattern[str]] = re.compile("".join(["sini", "stro"]), re.IGNORECASE)


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


def test_no_banned_term_in_package_and_tests() -> None:
    """Assert that no scanned file spells the banned word.

    Walks the package, tests, scripts and ground station source roots plus the
    copilot phrase table when present, and fails with the sorted list of
    offending paths relative to the repository root. Missing directories are
    skipped, so the scan holds before ``bebop_mission_control/docs`` exists.
    """
    files = [path for root in SCAN_ROOTS for path in _collect(root)]
    files.extend(path for path in EXTRA_FILES if path.is_file())
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in files
        if BANNED.search(path.read_text(encoding="utf-8", errors="ignore"))
    )
    assert offenders == []
