"""Pre-synthesis, verification and transport of the copilot phrase cache.

At the fair the station has no network: the copilot voice (Gemini Live,
``announcer.SYNTHESIZER_VOICE``) is heard only for sentences whose PCM is
already in the phrase cache (``announcer.PhraseCache``, ``~/.cache/bmg/speech``
or ``BMG_SPEECH_CACHE_DIR``). Every sentence the station can speak is finite
and listed by the station's manifest exporter
(``scripts/exportSpeechManifest.ts``, schema ``bmg.speech_manifest.v1``) plus
the alerts the mission composes in Python
(``mvp_mission_bebop/telemetry/spoken_alerts.py``). This tool fills the cache
from that list, verifies it, prunes it, carries it between machines and
renders the speech table ``docs/TABELA_DE_FALAS_COPILOTO.md``.

The cache key holds the sentence, the prompt mode, the voice and the model, so
the machine that synthesizes and the machine at the fair must run the same
``SPEECH_VOICE`` and ``SPEECH_MODEL`` (D4). The audio stays out of Git (D3);
``--pack`` and ``--unpack`` move it as a ``tar.gz``.

Usage (inside ``nectar-activate``, from the repository root)::

    python3 bebop_mission_control/streamer/precache_speech.py            # synthesize what is missing
    python3 bebop_mission_control/streamer/precache_speech.py --dry-run  # counts only, no writes
    python3 bebop_mission_control/streamer/precache_speech.py --check    # exit 1 while anything is missing
    python3 bebop_mission_control/streamer/precache_speech.py --prune [--yes]  # lists; --yes removes
    python3 bebop_mission_control/streamer/precache_speech.py --pack ~/bmg-speech-feira.tar.gz
    python3 bebop_mission_control/streamer/precache_speech.py --unpack ~/bmg-speech-feira.tar.gz
    python3 bebop_mission_control/streamer/precache_speech.py --table bebop_mission_control/docs/TABELA_DE_FALAS_COPILOTO.md

An entry shorter than 0.25 s per word (a cut turn) counts as missing: the
synthesis run overwrites it, ``--check`` lists it and ``--pack`` refuses it.

Exit codes: 0 done, 1 entries missing or short (``--check``, ``--pack``, a
synthesis run with failures or interrupted), 2 usage, manifest, network, API
key, archive error or refused ``--prune``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Final, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BMG_ROOT: Final[Path] = REPO_ROOT / "bebop_mission_control"

# The station starts its speech daemon as `python3 -m` from the repository, so
# it imports the source tree; the colcon build copy may lag behind it. Any
# drift in `PhraseCache.path_for` between the two would make every entry
# written here invisible to the daemon.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mvp_mission_bebop.telemetry import announcer  # noqa: E402
from mvp_mission_bebop.telemetry.spoken_alerts import ALERT_SENTENCES  # noqa: E402

logger = logging.getLogger("PrecacheSpeech")

MANIFEST_SCHEMA: Final[str] = "bmg.speech_manifest.v1"
PACK_SCHEMA: Final[str] = "bmg.speech_cache_pack.v1"
PACK_INDEX: Final[str] = "manifest.json"
EXPORTER: Final[str] = "scripts/exportSpeechManifest.ts"
TABLE_PATH: Final[str] = "bebop_mission_control/docs/TABELA_DE_FALAS_COPILOTO.md"
SCRIPT_PATH: Final[str] = "bebop_mission_control/streamer/precache_speech.py"

EXIT_OK: Final[int] = 0
EXIT_MISSING: Final[int] = 1
EXIT_ERROR: Final[int] = 2

#: Exporter wall time: vite-node transpiles the catalog in about 6 s here.
_EXPORT_TIMEOUT_SEC: Final[float] = 180.0
#: Reachability probe before a synthesis run; a first DNS lookup can be slow.
_PROBE_TIMEOUT_SEC: Final[float] = 3.0
_ENTRY_NAME: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}\.pcm$")
_SHA1_HEX: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_LINE_FIELDS: Final[Tuple[str, ...]] = ("text", "category", "condition", "source")


class SpeechLine(NamedTuple):
    """One sentence of the manifest and when the station speaks it.

    Attributes
    ----------
    text : str
        The exact sentence, stripped.
    category : str
        ``SpeechCategory`` of ``src/lib/spokenCatalog.ts``.
    condition : str
        Trigger, in pt-BR.
    source : str
        File and pool or call site the sentence comes from.
    """

    text: str
    category: str
    condition: str
    source: str


class TableGroup(NamedTuple):
    """Rows of one category in the speech table."""

    category: str
    title: str
    rows: Tuple[SpeechLine, ...]


class TableSection(NamedTuple):
    """One numbered section of the speech table."""

    title: str
    notes: Tuple[str, ...]
    groups: Tuple[TableGroup, ...]


class SpeechManifest(NamedTuple):
    """The station manifest merged with the mission's Python alerts.

    Attributes
    ----------
    lines : Tuple[SpeechLine, ...]
        Every sentence once, station entries first, in exporter order.
    sections : Tuple[TableSection, ...]
        The table grouping of the exporter with the Python rows appended to
        the group of their category.
    parameter_sets : Tuple[Dict[str, Any], ...]
        Spoken parameter documents the station sentences were rendered with.
    """

    lines: Tuple[SpeechLine, ...]
    sections: Tuple[TableSection, ...]
    parameter_sets: Tuple[Dict[str, Any], ...]

    def statements(self) -> Tuple[str, ...]:
        """The sentences, in manifest order."""
        return tuple(line.text for line in self.lines)

    def categories(self) -> Tuple[str, ...]:
        """Categories in table order, then any category without a table group."""
        ordered = [group.category for section in self.sections for group in section.groups]
        extra = sorted({line.category for line in self.lines} - set(ordered))
        return tuple(ordered + extra)


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _require_list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list, got {type(value).__name__}")
    return value


def _line_of(raw: Any, where: str) -> SpeechLine:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    fields = {name: _require_str(raw.get(name), f"{where}.{name}").strip() for name in _LINE_FIELDS}
    if not fields["text"]:
        raise ValueError(f"{where}.text must not be blank")
    return SpeechLine(**fields)


def merge_manifest(document: Mapping[str, Any]) -> SpeechManifest:
    """Validate an exporter document and merge the mission's Python alerts into it.

    Parameters
    ----------
    document : Mapping[str, Any]
        Parsed output of ``scripts/exportSpeechManifest.ts``.

    Returns
    -------
    SpeechManifest
        Station entries first, then each alert of
        ``spoken_alerts.ALERT_SENTENCES`` whose text the station does not
        already list; table rows follow the same rule.

    Raises
    ------
    TypeError
        If ``document`` is not a mapping.
    ValueError
        If the schema differs from :data:`MANIFEST_SCHEMA`, a field is
        malformed, or an alert category has no group in the table.
    """
    if not isinstance(document, Mapping):
        raise TypeError(f"document must be a mapping, got {type(document).__name__}")
    if document.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}, got {document.get('schema')!r}")

    lines: List[SpeechLine] = []
    seen: Set[str] = set()
    for index, raw in enumerate(_require_list(document.get("entries"), "entries")):
        line = _line_of(raw, f"entries[{index}]")
        if line.text not in seen:
            seen.add(line.text)
            lines.append(line)

    sections: List[Tuple[str, Tuple[str, ...], List[Tuple[str, str, List[SpeechLine]]]]] = []
    in_table: Set[str] = set()
    for s_index, raw_section in enumerate(_require_list(document.get("table"), "table")):
        where = f"table[{s_index}]"
        if not isinstance(raw_section, dict):
            raise ValueError(f"{where} must be an object")
        title = _require_str(raw_section.get("title"), f"{where}.title")
        notes = tuple(
            _require_str(note, f"{where}.notes[{n}]")
            for n, note in enumerate(_require_list(raw_section.get("notes", []), f"{where}.notes"))
        )
        groups: List[Tuple[str, str, List[SpeechLine]]] = []
        for g_index, raw_group in enumerate(_require_list(raw_section.get("groups"), f"{where}.groups")):
            g_where = f"{where}.groups[{g_index}]"
            if not isinstance(raw_group, dict):
                raise ValueError(f"{g_where} must be an object")
            rows = [
                _line_of(row, f"{g_where}.rows[{r}]")
                for r, row in enumerate(_require_list(raw_group.get("rows"), f"{g_where}.rows"))
            ]
            in_table.update(row.text for row in rows)
            groups.append(
                (
                    _require_str(raw_group.get("category"), f"{g_where}.category"),
                    _require_str(raw_group.get("title"), f"{g_where}.title"),
                    rows,
                )
            )
        sections.append((title, notes, groups))

    group_rows: Dict[str, List[SpeechLine]] = {
        category: rows for _title, _notes, groups in sections for category, _g, rows in groups
    }
    for spec in ALERT_SENTENCES:
        line = SpeechLine(text=spec.text.strip(), category=spec.category, condition=spec.condition, source=spec.source)
        if line.category not in group_rows:
            raise ValueError(f"alert category {line.category!r} has no group in the manifest table")
        if line.text not in seen:
            seen.add(line.text)
            lines.append(line)
        if line.text not in in_table:
            in_table.add(line.text)
            group_rows[line.category].append(line)

    parameter_sets = tuple(
        dict(entry) if isinstance(entry, dict) else {}
        for entry in _require_list(document.get("parameter_sets", []), "parameter_sets")
    )
    return SpeechManifest(
        lines=tuple(lines),
        sections=tuple(
            TableSection(
                title=title,
                notes=notes,
                groups=tuple(TableGroup(category, g_title, tuple(rows)) for category, g_title, rows in groups),
            )
            for title, notes, groups in sections
        ),
        parameter_sets=parameter_sets,
    )


def exporter_command() -> List[str]:
    """The command that runs the manifest exporter, without its options.

    The station's own ``node_modules/.bin/vite-node`` is preferred. Otherwise
    ``npx --no-install`` resolves an installed one and fails at once when there
    is none, instead of downloading the package, which on the fair network
    would hang until the export timeout.

    Returns
    -------
    List[str]
        Executable and arguments, ending with the exporter script.

    Raises
    ------
    RuntimeError
        If neither the local ``vite-node`` nor ``npx`` exists.
    """
    local = BMG_ROOT / "node_modules" / ".bin" / "vite-node"
    if local.is_file() and os.access(local, os.X_OK):
        return [str(local), EXPORTER]
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError(
            f"vite-node not installed in {BMG_ROOT / 'node_modules'} and npx absent; pass --manifest FILE exported elsewhere"
        )
    return [npx, "--no-install", "vite-node", EXPORTER]


def export_manifest(parameters: Optional[Path] = None, *, canonical: bool = False) -> Dict[str, Any]:
    """Run the station's manifest exporter and parse its JSON.

    Parameters
    ----------
    parameters : Optional[Path]
        JSON list of further ``mission.parameters`` documents, passed as
        ``--parameters``.
    canonical : bool
        Leave the machine's ``mission_config.json`` out, keeping the fair and
        schema-default parameter sets only. The versioned table is rendered
        this way so that it does not change with the station's local store.

    Returns
    -------
    Dict[str, Any]
        The exporter document.

    Raises
    ------
    TypeError
        If ``parameters`` is neither None nor a path.
    RuntimeError
        If no ``vite-node`` resolves (:func:`exporter_command`), the exporter
        fails or exceeds its timeout.
    ValueError
        If its output is not a JSON object.
    """
    if parameters is not None and not isinstance(parameters, (str, Path)):
        raise TypeError(f"parameters must be a path, got {type(parameters).__name__}")
    command = exporter_command()
    if parameters is not None:
        command += ["--parameters", str(Path(parameters).resolve())]
    with tempfile.TemporaryDirectory(prefix="bmg-speech-manifest-") as scratch:
        if canonical:
            command += ["--config", str(Path(scratch) / "mission_config.absent.json")]
        try:
            result = subprocess.run(
                command, cwd=BMG_ROOT, capture_output=True, text=True, timeout=_EXPORT_TIMEOUT_SEC, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"manifest exporter exceeded {_EXPORT_TIMEOUT_SEC:.0f} s") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"manifest exporter exited {result.returncode}: {result.stderr.strip()}; "
            "pass --manifest FILE exported elsewhere"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest exporter output is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("manifest exporter output is not a JSON object")
    return document


def load_manifest(
    manifest: Optional[Path] = None, parameters: Optional[Path] = None, *, canonical: bool = False
) -> SpeechManifest:
    """The merged manifest, from a file or from the exporter.

    Parameters
    ----------
    manifest : Optional[Path]
        Exporter document saved earlier; the exporter runs when None.
    parameters : Optional[Path]
        Forwarded to :func:`export_manifest`; must be None with ``manifest``.
    canonical : bool
        Forwarded to :func:`export_manifest`.

    Returns
    -------
    SpeechManifest

    Raises
    ------
    ValueError
        If both ``manifest`` and ``parameters`` are given, or the document is
        unreadable or malformed.
    RuntimeError
        If the exporter fails.
    """
    if manifest is None:
        return merge_manifest(export_manifest(parameters, canonical=canonical))
    if parameters is not None:
        raise ValueError("--parameters applies to the exporter, not to --manifest")
    try:
        document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest {manifest} unreadable: {exc}") from exc
    return merge_manifest(document)


class CacheAudit(NamedTuple):
    """Manifest sentences without a complete verbatim entry, by category.

    Attributes
    ----------
    absent : Dict[str, List[str]]
        No usable entry (``PhraseCache.get`` is None).
    short : Dict[str, List[str]]
        An entry exists but is shorter than ``announcer._minimum_speech_bytes``
        (0.25 s per word): a cut turn, typically stored by the daemon's
        ``warm_cache``. The pre-cache overwrites these.
    """

    absent: Dict[str, List[str]]
    short: Dict[str, List[str]]

    def total_absent(self) -> int:
        """Number of absent statements."""
        return sum(len(texts) for texts in self.absent.values())

    def total_short(self) -> int:
        """Number of statements with a short entry."""
        return sum(len(texts) for texts in self.short.values())


def audit_cache(manifest: SpeechManifest, cache: announcer.PhraseCache) -> CacheAudit:
    """Classify every manifest sentence as complete, short or absent in ``cache``.

    Parameters
    ----------
    manifest : SpeechManifest
    cache : announcer.PhraseCache

    Returns
    -------
    CacheAudit
        Every category of :meth:`SpeechManifest.categories`, in order, in both
        maps, with statements in manifest order (lists possibly empty).

    Raises
    ------
    TypeError
        If ``manifest`` or ``cache`` has the wrong type.
    """
    if not isinstance(manifest, SpeechManifest):
        raise TypeError(f"manifest must be a SpeechManifest, got {type(manifest).__name__}")
    if not isinstance(cache, announcer.PhraseCache):
        raise TypeError(f"cache must be a PhraseCache, got {type(cache).__name__}")
    absent: Dict[str, List[str]] = {category: [] for category in manifest.categories()}
    short: Dict[str, List[str]] = {category: [] for category in manifest.categories()}
    for line in manifest.lines:
        if cache.get(line.text, True) is None:
            absent[line.category].append(line.text)
        elif not announcer._entry_is_complete(cache, line.text):
            short[line.category].append(line.text)
    return CacheAudit(absent=absent, short=short)


def _print_summary(manifest: SpeechManifest, cache: announcer.PhraseCache, audit: CacheAudit) -> int:
    """Print voice, model, cache and per-category counts. Returns absent plus short."""
    totals: Dict[str, int] = {category: 0 for category in manifest.categories()}
    for line in manifest.lines:
        totals[line.category] += 1
    width = max([len("categoria")] + [len(category) for category in totals])
    print(f"voz: {announcer.SYNTHESIZER_VOICE}")
    print(f"modelo: {announcer.SYNTHESIZER_MODEL}")
    print(f"cache: {cache.directory}")
    print(f"{'categoria':<{width}}  {'total':>6}  {'em cache':>8}  {'curtas':>6}  {'faltam':>6}")
    for category, total in totals.items():
        absent = len(audit.absent.get(category, []))
        short = len(audit.short.get(category, []))
        print(f"{category:<{width}}  {total:>6}  {total - absent - short:>8}  {short:>6}  {absent + short:>6}")
    total_missing = audit.total_absent() + audit.total_short()
    print(f"total: {len(manifest.lines)}")
    print(f"em cache: {len(manifest.lines) - total_missing}")
    print(f"curtas: {audit.total_short()}")
    print(f"faltam: {total_missing}")
    return total_missing


def _part_is_stale(path: Path) -> bool:
    """Whether ``<key>.<pid>.part`` belongs to a process that no longer runs."""
    pieces = path.name.split(".")
    if len(pieces) != 3 or not pieces[1].isdigit():
        return True
    return not announcer._pid_alive(int(pieces[1]))


def prune_cache(manifest: SpeechManifest, cache: announcer.PhraseCache, *, confirm: bool = False) -> List[Path]:
    """Remove every cache file the current manifest does not reference.

    The kept set is the verbatim entry of every manifest sentence under the
    current ``SYNTHESIZER_VOICE`` and ``SYNTHESIZER_MODEL``. File names are
    hashes, so an entry of another voice or model cannot be told apart from a
    sentence that left the manifest: both are removed. Run under the wrong
    ``SPEECH_VOICE``/``SPEECH_MODEL`` this would delete a whole valid cache,
    so it is refused when fewer than half of the manifest's sentences have an
    entry under the current key. ``.part`` files of processes no longer
    running are removed too; those of a live writer are left alone.

    Parameters
    ----------
    manifest : SpeechManifest
    cache : announcer.PhraseCache
    confirm : bool
        Remove the files; when False they are only listed.

    Returns
    -------
    List[Path]
        Files removed, or that would be, sorted.

    Raises
    ------
    TypeError
        If ``manifest`` or ``cache`` has the wrong type.
    ValueError
        If fewer than half of the manifest's sentences are cached under the
        current voice and model.
    """
    if not isinstance(manifest, SpeechManifest):
        raise TypeError(f"manifest must be a SpeechManifest, got {type(manifest).__name__}")
    if not isinstance(cache, announcer.PhraseCache):
        raise TypeError(f"cache must be a PhraseCache, got {type(cache).__name__}")
    statements = manifest.statements()
    present = sum(1 for text in statements if cache.get(text, True) is not None)
    if 2 * present < len(statements):
        raise ValueError(
            f"only {present} of {len(statements)} manifest entries exist under SPEECH_VOICE="
            f"{announcer.SYNTHESIZER_VOICE} SPEECH_MODEL={announcer.SYNTHESIZER_MODEL}; prune refused, "
            "the cache was probably synthesized with another voice or model"
        )
    directory = Path(cache.directory)
    if not directory.is_dir():
        return []
    keep = {cache.path_for(text, True).name for text in statements}
    doomed = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and ((path.suffix == ".pcm" and path.name not in keep) or (path.suffix == ".part" and _part_is_stale(path)))
    )
    if confirm:
        for path in doomed:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return doomed


class ShortEntriesError(ValueError):
    """Raised by :func:`pack_cache` when cached entries are shorter than their sentence.

    Attributes
    ----------
    statements : Tuple[str, ...]
        The sentences whose entry is short, in manifest order.
    """

    def __init__(self, statements: Tuple[str, ...]) -> None:
        super().__init__(f"{len(statements)} cached entries are shorter than 0.25 s per word")
        self.statements = statements


def pack_cache(manifest: SpeechManifest, cache: announcer.PhraseCache, archive: Path) -> Tuple[int, int]:
    """Write the manifest's cache entries to a ``tar.gz`` for another machine.

    The archive holds :data:`PACK_INDEX` (schema, voice, model and the SHA-1
    and size of every file) followed by the ``<key>.pcm`` files. It is written
    beside ``archive`` and renamed into place.

    Parameters
    ----------
    manifest : SpeechManifest
    cache : announcer.PhraseCache
    archive : Path
        Destination file.

    Returns
    -------
    Tuple[int, int]
        ``(packed, missing)``: entries written and manifest sentences absent
        from the cache.

    Raises
    ------
    TypeError
        If an argument has the wrong type.
    ShortEntriesError
        If any cached entry is shorter than its sentence; nothing is written,
        since a cut line would be carried to the fair as if complete.
    OSError
        If the archive cannot be written.
    """
    if not isinstance(manifest, SpeechManifest):
        raise TypeError(f"manifest must be a SpeechManifest, got {type(manifest).__name__}")
    if not isinstance(cache, announcer.PhraseCache):
        raise TypeError(f"cache must be a PhraseCache, got {type(cache).__name__}")
    if not isinstance(archive, (str, Path)):
        raise TypeError(f"archive must be a path, got {type(archive).__name__}")

    payload: Dict[str, bytes] = {}
    short: List[str] = []
    missing = 0
    for text in manifest.statements():
        data = cache.get(text, True)
        if data is None:
            missing += 1
            continue
        if len(data) < announcer._minimum_speech_bytes(text):
            short.append(text)
            continue
        payload[cache.path_for(text, True).name] = data
    if short:
        raise ShortEntriesError(tuple(short))
    index = {
        "schema": PACK_SCHEMA,
        "voice": announcer.SYNTHESIZER_VOICE,
        "model": announcer.SYNTHESIZER_MODEL,
        "files": {
            name: {"sha1": hashlib.sha1(data).hexdigest(), "bytes": len(data)} for name, data in sorted(payload.items())
        },
    }

    target = Path(archive).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.{os.getpid()}.part")
    try:
        with tarfile.open(partial, "w:gz") as bundle:
            members = [(PACK_INDEX, json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"))]
            members.extend(sorted(payload.items()))
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                bundle.addfile(info, io.BytesIO(data))
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return len(payload), missing


def _read_index(bundle: tarfile.TarFile) -> Dict[str, Dict[str, Any]]:
    """Validate the pack index against this station's voice and model. Returns its files."""
    try:
        handle = bundle.extractfile(PACK_INDEX)
    except KeyError as exc:
        raise ValueError(f"archive has no {PACK_INDEX}") from exc
    if handle is None:
        raise ValueError(f"{PACK_INDEX} is not a regular file")
    try:
        index = json.loads(handle.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{PACK_INDEX} unreadable: {exc}") from exc
    if not isinstance(index, dict) or index.get("schema") != PACK_SCHEMA:
        raise ValueError(f"archive schema must be {PACK_SCHEMA}")
    voice, model = index.get("voice"), index.get("model")
    if voice != announcer.SYNTHESIZER_VOICE or model != announcer.SYNTHESIZER_MODEL:
        raise ValueError(
            f"archive voice/model {voice}/{model} differs from this station's "
            f"{announcer.SYNTHESIZER_VOICE}/{announcer.SYNTHESIZER_MODEL} (SPEECH_VOICE/SPEECH_MODEL); "
            "its cache keys would never match"
        )
    files = index.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"{PACK_INDEX} files must be an object")
    for name, entry in files.items():
        if not _ENTRY_NAME.match(str(name)):
            raise ValueError(f"{PACK_INDEX} lists an invalid entry name {name!r}")
        if (
            not isinstance(entry, dict)
            or not _SHA1_HEX.match(str(entry.get("sha1", "")))
            or isinstance(entry.get("bytes"), bool)
            or not isinstance(entry.get("bytes"), int)
        ):
            raise ValueError(f"{PACK_INDEX} entry {name} malformed")
    return files


def unpack_cache(archive: Path, cache: announcer.PhraseCache) -> int:
    """Install a :func:`pack_cache` archive into ``cache``, all or nothing.

    Every member is checked before any entry is installed: the voice and model
    must equal this station's ``SYNTHESIZER_VOICE`` and ``SYNTHESIZER_MODEL``,
    each name must be a bare ``<sha1>.pcm`` listed in the index, and each
    file's size and SHA-1 must match it. Files are staged as ``.part`` and
    renamed only after the whole archive verified; nothing is extracted by
    path.

    Parameters
    ----------
    archive : Path
        Archive written by :func:`pack_cache`.
    cache : announcer.PhraseCache
        Destination cache.

    Returns
    -------
    int
        Entries installed.

    Raises
    ------
    TypeError
        If an argument has the wrong type.
    ValueError
        If the archive is unreadable, of another voice or model, or any
        member is unlisted, unsafe, truncated or corrupt.
    """
    if not isinstance(archive, (str, Path)):
        raise TypeError(f"archive must be a path, got {type(archive).__name__}")
    if not isinstance(cache, announcer.PhraseCache):
        raise TypeError(f"cache must be a PhraseCache, got {type(cache).__name__}")

    directory = Path(cache.directory)
    staged: List[Tuple[Path, Path]] = []
    try:
        with tarfile.open(Path(archive).expanduser(), "r:gz") as bundle:
            files = _read_index(bundle)
            seen: Set[str] = set()
            for member in bundle.getmembers():
                if member.name == PACK_INDEX:
                    continue
                if member.name not in files or member.name in seen or not member.isfile():
                    raise ValueError(f"archive member {member.name!r} is not a listed cache entry")
                handle = bundle.extractfile(member)
                data = handle.read() if handle is not None else b""
                entry = files[member.name]
                if len(data) != entry["bytes"] or len(data) < announcer._MIN_AUDIO_BYTES:
                    raise ValueError(f"archive member {member.name} truncated: {len(data)} bytes")
                if hashlib.sha1(data).hexdigest() != entry["sha1"]:
                    raise ValueError(f"archive member {member.name} fails its SHA-1")
                seen.add(member.name)
                directory.mkdir(parents=True, exist_ok=True)
                final = directory / member.name
                partial = final.with_suffix(f".{os.getpid()}.part")
                partial.write_bytes(data)
                staged.append((partial, final))
            absent = sorted(set(files) - seen)
            if absent:
                raise ValueError(f"archive lacks {len(absent)} listed entries, first {absent[0]}")
        for partial, final in staged:
            os.replace(partial, final)
        installed = len(staged)
        staged = []
        return installed
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError(f"archive {archive} unreadable: {exc}") from exc
    finally:
        for partial, _final in staged:
            partial.unlink(missing_ok=True)


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def _source_cell(source: str) -> str:
    return f"`{source}`" if source and "`" not in source and "|" not in source else _cell(source)


def _falas(count: int) -> str:
    return f"{count} fala" if count == 1 else f"{count} falas"


def _parameter_label(parameters: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={parameters[key]}" for key in sorted(parameters)) or "(vazio)"


def render_table(manifest: SpeechManifest) -> str:
    """Render ``TABELA_DE_FALAS_COPILOTO.md`` from the merged manifest.

    The output depends only on the manifest: no timestamp, voice or machine
    path, so a regenerated table equals the versioned one while the catalog
    is unchanged.

    Parameters
    ----------
    manifest : SpeechManifest

    Returns
    -------
    str
        The Markdown document, ending in a newline.

    Raises
    ------
    TypeError
        If ``manifest`` is not a :class:`SpeechManifest`.
    """
    if not isinstance(manifest, SpeechManifest):
        raise TypeError(f"manifest must be a SpeechManifest, got {type(manifest).__name__}")
    rows_total = sum(len(group.rows) for section in manifest.sections for group in section.groups)
    out: List[str] = [
        "# Tabela de falas do copiloto",
        "",
        f"Gerada por `python3 {SCRIPT_PATH} --table {TABLE_PATH}`; não editar à mão. A fonte é o manifesto "
        f"da estação (`bebop_mission_control/{EXPORTER}`, `{MANIFEST_SCHEMA}`) mesclado aos alertas que a "
        "missão compõe em Python (`mvp_mission_bebop/telemetry/spoken_alerts.py`).",
        "",
        "Cada texto é lido literalmente pela voz do copiloto (Gemini Live) e tocado do cache de frases "
        "(`PhraseCache`: `~/.cache/bmg/speech` ou `BMG_SPEECH_CACHE_DIR`), cuja chave inclui o texto, o modo, "
        "a voz (`SPEECH_VOICE`) e o modelo (`SPEECH_MODEL`). Sem rede, uma fala ausente do cache não é dita.",
        "",
        "Fontes iniciadas por `src/` são relativas a `bebop_mission_control/`; as demais, à raiz do repositório.",
        "",
        "Conjuntos de parâmetros falados: "
        + "; ".join(f"`{_parameter_label(parameters)}`" for parameters in manifest.parameter_sets)
        + ". O `mission_config.json` da estação não entra nesta tabela; o pré-cache o inclui.",
        "",
        f"Total: {_falas(rows_total)}.",
    ]
    for s_number, section in enumerate(manifest.sections, start=1):
        count = sum(len(group.rows) for group in section.groups)
        out += ["", f"## {s_number}. {section.title}", "", f"{_falas(count)}."]
        g_number = 0
        for group in section.groups:
            g_number += 1
            out += [
                "",
                f"### {s_number}.{g_number} {group.title}",
                "",
                f"Categoria `{group.category}`, {_falas(len(group.rows))}.",
                "",
            ]
            if not group.rows:
                out.append("Nenhuma fala nesta categoria.")
                continue
            out += ["| Condição de disparo | Fonte | Texto |", "| --- | --- | --- |"]
            out += [f"| {_cell(row.condition)} | {_source_cell(row.source)} | {_cell(row.text)} |" for row in group.rows]
        if section.notes:
            g_number += 1
            out += ["", f"### {s_number}.{g_number} Regra de ordem e observações", ""]
            out += [f"- {_cell(note)}" for note in section.notes]
    out += [
        "",
        "## Operação do cache",
        "",
        f"Comandos (dentro do `nectar-activate`, na raiz do repositório): `python3 {SCRIPT_PATH} <opção>`.",
        "",
        "| Opção | Efeito | Saída |",
        "| --- | --- | --- |",
        "| (nenhuma) | Sintetiza o que falta no cache e substitui entradas curtas; exige internet e "
        "`SPEECH_API_KEY` | 0 completo, 1 falhas, "
        "2 sem chave ou sem rede |",
        "| `--dry-run` | Contagem por categoria, sem rede e sem gravar | 0 |",
        "| `--check` | Lista as falas ausentes e as curtas (menos de 0,25 s por palavra) por categoria "
        "| 0 completo, 1 faltam |",
        "| `--prune` | Lista toda entrada que o manifesto atual não referencia, de qualquer voz ou modelo; "
        "só remove com `--yes`; recusa se menos da metade do manifesto existe na voz e modelo atuais "
        "| 0, 2 recusado |",
        "| `--yes` | Confirma a remoção do `--prune` | |",
        "| `--pack FILE` | Empacota o cache do manifesto em `tar.gz` com voz, modelo e SHA-1; recusa entradas curtas "
        "| 0 completo, 1 faltam ou curtas |",
        "| `--unpack FILE` | Instala um pacote; recusa voz ou modelo diferentes | 0, 2 pacote inválido |",
        "| `--table PATH` | Gera esta tabela | 0 |",
        "| `--manifest FILE` | Usa um manifesto exportado em vez de rodar o exportador | |",
        "| `--parameters FILE` | Repassado ao exportador (lista JSON de `mission.parameters`) | |",
        "",
    ]
    return "\n".join(out)


def write_table(manifest: SpeechManifest, path: Path) -> None:
    """Write :func:`render_table` to ``path`` through a temporary file and a rename.

    Raises
    ------
    TypeError
        If ``path`` is not a path.
    OSError
        If the file cannot be written.
    """
    if not isinstance(path, (str, Path)):
        raise TypeError(f"path must be a path, got {type(path).__name__}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        partial.write_text(render_table(manifest), encoding="utf-8")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _print_grouped(title: str, grouped: Dict[str, List[str]]) -> None:
    if not any(grouped.values()):
        return
    print(title)
    for category, texts in grouped.items():
        if texts:
            print(f"[{category}]")
            for text in texts:
                print(f"  {text}")


def _synthesize(manifest: SpeechManifest, cache: announcer.PhraseCache, missing_total: int) -> int:
    """Synthesize what is missing. Returns the exit code."""
    if missing_total == 0:
        print("sintetizadas: 0")
        print("falhas: 0")
        return EXIT_OK
    if not announcer._resolve_auth_token():
        print("erro: sem chave de API (SPEECH_API_KEY / GEMINI_API_KEY)")
        return EXIT_ERROR
    if not announcer.synthesis_reachable(timeout_sec=_PROBE_TIMEOUT_SEC):
        print(f"erro: {announcer.SYNTHESIS_HOST}:{announcer.SYNTHESIS_PORT} inacessível")
        return EXIT_ERROR

    done = [0]

    def progress(index: int, pending: int, statement: str) -> None:
        done[0] = index
        print(f"[{index}/{pending}] {statement}", file=sys.stderr, flush=True)

    try:
        report = announcer.precache_phrases(manifest.statements(), cache=cache, on_progress=progress)
    except KeyboardInterrupt:
        print(f"interrompido após {done[0]} de {missing_total}; entradas gravadas estão completas; repetir completa o resto")
        return EXIT_MISSING
    except (ValueError, RuntimeError) as exc:
        print(f"erro: {exc}")
        return EXIT_ERROR
    print(f"sintetizadas: {report.synthesized}")
    print(f"falhas: {len(report.failed)}")
    for text in report.failed:
        print(f"  {text}")
    return EXIT_MISSING if report.failed else EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="precache_speech.py",
        description="Pre-synthesis, verification and transport of the copilot phrase cache.",
    )
    parser.add_argument("--manifest", type=Path, metavar="FILE", help="exporter JSON; default runs the exporter")
    parser.add_argument("--parameters", type=Path, metavar="FILE", help="forwarded to the exporter")
    parser.add_argument("--dry-run", action="store_true", help="counts only; with --prune, list without removing")
    parser.add_argument("--yes", action="store_true", help="with --prune, remove the listed files")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="list missing entries; exit 1 while any is missing")
    mode.add_argument("--prune", action="store_true", help="list cache files outside the manifest, any voice")
    mode.add_argument("--pack", type=Path, metavar="FILE", help="write the manifest's entries to a tar.gz")
    mode.add_argument("--unpack", type=Path, metavar="FILE", help="install a --pack archive")
    mode.add_argument("--table", type=Path, metavar="PATH", help="render TABELA_DE_FALAS_COPILOTO.md")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command line. Returns the exit code (0, 1 or 2, see module docstring)."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    if args.dry_run and (args.check or args.pack or args.unpack or args.table):
        print("erro: --dry-run combina apenas com a síntese ou com --prune", file=sys.stderr)
        return EXIT_ERROR
    if args.yes and not args.prune:
        print("erro: --yes vale apenas com --prune", file=sys.stderr)
        return EXIT_ERROR
    if args.manifest is not None and args.parameters is not None:
        print("erro: --parameters vale para o exportador, não para --manifest", file=sys.stderr)
        return EXIT_ERROR

    cache = announcer.PhraseCache()
    try:
        if args.unpack is not None:
            installed = unpack_cache(args.unpack, cache)
            print(f"voz: {announcer.SYNTHESIZER_VOICE}")
            print(f"modelo: {announcer.SYNTHESIZER_MODEL}")
            print(f"cache: {cache.directory}")
            print(f"instaladas: {installed}")
            return EXIT_OK

        manifest = load_manifest(args.manifest, args.parameters, canonical=args.table is not None)
        if args.table is not None:
            write_table(manifest, args.table)
            print(f"tabela: {args.table} ({len(manifest.lines)} falas)")
            return EXIT_OK

        if args.pack is not None:
            print(f"voz: {announcer.SYNTHESIZER_VOICE}")
            print(f"modelo: {announcer.SYNTHESIZER_MODEL}")
            try:
                packed, absent = pack_cache(manifest, cache, args.pack)
            except ShortEntriesError as exc:
                print(f"recusado: {len(exc.statements)} entradas curtas (menos de 0,25 s por palavra); nada gravado")
                for text in exc.statements:
                    print(f"  {text}")
                print("sintetizar de novo: execute sem opções para substituí-las")
                return EXIT_MISSING
            print(f"empacotadas: {packed}")
            print(f"faltam: {absent}")
            print(f"arquivo: {args.pack}")
            return EXIT_MISSING if absent else EXIT_OK

        if args.prune:
            confirm = args.yes and not args.dry_run
            print(f"voz: {announcer.SYNTHESIZER_VOICE}")
            print(f"modelo: {announcer.SYNTHESIZER_MODEL}")
            print(f"cache: {cache.directory}")
            removed = prune_cache(manifest, cache, confirm=confirm)
            for path in removed:
                print(f"  {path.name}")
            if confirm:
                print(f"removidas: {len(removed)}")
            else:
                print(f"a remover: {len(removed)}; nada removido sem --yes")
            return EXIT_OK

        audit = audit_cache(manifest, cache)
        missing_total = _print_summary(manifest, cache, audit)
        if args.check:
            _print_grouped("ausentes por categoria:", audit.absent)
            _print_grouped("curtas por categoria (menos de 0,25 s por palavra):", audit.short)
            return EXIT_MISSING if missing_total else EXIT_OK
        if args.dry_run:
            print(f"dry-run: {missing_total} falas seriam sintetizadas; nada gravado")
            return EXIT_OK
        return _synthesize(manifest, cache, missing_total)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"erro: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(message)s", stream=sys.stderr)
    sys.exit(main())
