"""Pre-synthesis, verification and transport of the copilot phrase cache.

At the fair the station has no network, so every sentence it can speak must be
on disk before the flight (``announcer.PhraseCache``). The pre-cache tool fills
the cache from the finite manifest, verifies it, prunes it and carries it to
another machine. No test here opens a socket or an audio device: synthesis is
replaced by a deterministic PCM generator, the cache lives in ``tmp_path`` and
the endpoint is reported reachable by ``conftest.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from mvp_mission_bebop.telemetry import announcer
from mvp_mission_bebop.telemetry.spoken_alerts import all_alert_sentences

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = REPO_ROOT / "bebop_mission_control" / "streamer" / "precache_speech.py"
VERSIONED_TABLE: Path = REPO_ROOT / "bebop_mission_control" / "docs" / "TABELA_DE_FALAS_COPILOTO.md"

_spec = importlib.util.spec_from_file_location("precache_speech_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
precache_speech = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = precache_speech
_spec.loader.exec_module(precache_speech)

#: int16 mono at 24 kHz.
BYTES_PER_SEC: int = announcer.PCM_SAMPLE_RATE * announcer.PCM_CHANNELS * 2

TS_ENTRIES: List[Dict[str, str]] = [
    {
        "text": "Missão iniciada. Carregando parâmetros de voo.",
        "category": "voo.contagem",
        "source": "src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.start']",
        "condition": "Clique em iniciar missão (mission.start)",
    },
    {
        "text": "Pouso concluído com segurança.",
        "category": "voo.pouso",
        "source": "src/lib/copilotPhrases.ts:PHRASE_POOLS['mission.landed']",
        "condition": "Touchdown confirmado (mission.landed)",
    },
    {
        "text": "Apresentando o laudo preliminar da ocorrência.",
        "category": "laudo.abertura",
        "source": "src/lib/forensics.ts:OPENERS",
        "condition": "Primeiro card do laudo",
    },
    {
        "text": "Falha na etapa.",
        "category": "falha.aborto",
        "source": "src/lib/copilotPhrases.ts:ALERT_FALLBACK",
        "condition": "Alerta sem texto",
    },
]

CATEGORIES: Tuple[str, ...] = (
    "voo.contagem",
    "voo.pouso",
    "laudo.abertura",
    "falha.aborto",
    "falha.link",
    "falha.bateria",
    "falha.calibracao",
    "falha.decolagem",
)


def _table(entries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """The ``table`` field of the exporter for ``entries``, one group per category."""
    sections = (("Voo normal", "voo.", []), ("Laudo pericial", "laudo.", ["Cenário A: Veículo, Vítima."]), ("Falhas", "falha.", []))
    return [
        {
            "title": title,
            "notes": list(notes),
            "groups": [
                {
                    "category": category,
                    "title": category,
                    "rows": [
                        {"category": category, "condition": e["condition"], "source": e["source"], "text": e["text"]}
                        for e in entries
                        if e["category"] == category
                    ],
                }
                for category in CATEGORIES
                if category.startswith(prefix)
            ],
        }
        for title, prefix, notes in sections
    ]


def _write_manifest(directory: Path, entries: Optional[List[Dict[str, str]]] = None) -> Path:
    """Write a ``bmg.speech_manifest.v1`` document and return its path."""
    chosen = TS_ENTRIES if entries is None else entries
    document = {
        "schema": "bmg.speech_manifest.v1",
        "voice": None,
        "parameter_sets": [{"target_altitude_m": 2, "cruise_mps": 0.15}],
        "entries": chosen,
        "table": _table(chosen),
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _speech_pcm(statement: str, seconds_per_word: float = 0.5) -> bytes:
    """Deterministic PCM as long as ``statement`` spoken at ``seconds_per_word``."""
    size = int(len(statement.split()) * seconds_per_word * BYTES_PER_SEC)
    size -= size % 2
    seed = hashlib.sha1(statement.encode("utf-8")).digest()
    return (seed * (size // len(seed) + 1))[:size]


class FakeSpeaker:
    """Stand-in for the announcer: ``synthesize_blocking`` without a Live session."""

    def __init__(self, produce: Optional[Callable[[str, int], bytes]] = None) -> None:
        self.calls: List[str] = []
        self._produce = produce

    def synthesize_blocking(self, statement: str, verbatim: bool = True, timeout: float = 30.0) -> bytes:
        self.calls.append(statement)
        if self._produce is not None:
            return self._produce(statement, len(self.calls))
        return _speech_pcm(statement)


@pytest.fixture
def cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A private phrase cache directory, with a resolvable key and no backoff wait."""
    directory = tmp_path / "speech"
    monkeypatch.setenv(announcer.SPEECH_CACHE_ENV, str(directory))
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: "test-key")
    monkeypatch.setattr(announcer, "_PRECACHE_BACKOFF_SEC", 0.0)
    return directory


@pytest.fixture
def speaker(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> Iterator[FakeSpeaker]:
    """The fake speaker installed as the global announcer."""
    fake = FakeSpeaker()
    monkeypatch.setattr(announcer, "get_announcer", lambda: fake)
    yield fake


def _fill(cache: announcer.PhraseCache, statements: Tuple[str, ...]) -> None:
    for statement in statements:
        cache.put(statement, True, _speech_pcm(statement))


def _parts(directory: Path) -> List[Path]:
    return sorted(directory.glob("*.part")) if directory.is_dir() else []


def test_only_missing_are_synthesized_and_second_run_is_noop(speaker: FakeSpeaker, cache_dir: Path) -> None:
    statements = ["Decolagem autorizada.", "Subindo para 2 metros.", "  Decolagem autorizada. ", "Altitude atingida."]
    cache = announcer.PhraseCache()

    first = announcer.precache_phrases(statements, cache=cache)
    assert first == announcer.PrecacheReport(synthesized=3, skipped=0, failed=())
    assert speaker.calls == ["Decolagem autorizada.", "Subindo para 2 metros.", "Altitude atingida."]
    assert all(cache.get(s, True) == _speech_pcm(s) for s in speaker.calls)

    second = announcer.precache_phrases(statements, cache=cache)
    assert second == announcer.PrecacheReport(synthesized=0, skipped=3, failed=())
    assert len(speaker.calls) == 3


def test_progress_is_reported_per_synthesized_statement(speaker: FakeSpeaker, cache_dir: Path) -> None:
    seen: List[Tuple[int, int, str]] = []
    announcer.precache_phrases(["Primeira fala.", "Segunda fala."], on_progress=lambda i, n, s: seen.append((i, n, s)))
    assert seen == [(1, 2, "Primeira fala."), (2, 2, "Segunda fala.")]


def test_precache_rejects_bad_arguments(speaker: FakeSpeaker, cache_dir: Path) -> None:
    with pytest.raises(TypeError):
        announcer.precache_phrases("Uma fala.")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        announcer.precache_phrases(["Uma fala.", 3])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        announcer.precache_phrases(["Uma fala."], attempts=0)
    with pytest.raises(TypeError):
        announcer.precache_phrases(["Uma fala."], attempts=True)  # type: ignore[arg-type]


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch, speaker: FakeSpeaker, cache_dir: Path) -> None:
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: None)
    with pytest.raises(ValueError):
        announcer.precache_phrases(["Uma fala sem chave."])
    assert speaker.calls == []


def test_truncated_audio_is_rejected(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    statement = "Inspeção concluída, evidências registradas e laudo preliminar pronto para leitura agora."
    words = len(statement.split())
    # Above the 0.1 s floor of a cache entry but under 0.25 s per word.
    short = _speech_pcm(statement, seconds_per_word=0.2)
    assert len(short) >= announcer._MIN_AUDIO_BYTES
    assert len(short) < words * 0.25 * BYTES_PER_SEC
    fake = FakeSpeaker(lambda s, n: short)
    monkeypatch.setattr(announcer, "get_announcer", lambda: fake)
    cache = announcer.PhraseCache()

    report = announcer.precache_phrases([statement, "Ok."], cache=cache, attempts=3)

    # The same buffer is a complete reading of the one-word line.
    assert report.failed == (statement,)
    assert report.synthesized == 1
    assert cache.get("Ok.", True) == short
    assert fake.calls.count(statement) == 3
    assert cache.get(statement, True) is None
    assert not cache.path_for(statement, True).exists()
    assert _parts(cache_dir) == []


def test_empty_and_failing_synthesis_is_retried_then_failed(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    def flaky(statement: str, call: int) -> bytes:
        if call == 1:
            raise TimeoutError("no audio within the timeout")
        if call == 2:
            return b""
        return _speech_pcm(statement)

    fake = FakeSpeaker(flaky)
    monkeypatch.setattr(announcer, "get_announcer", lambda: fake)
    report = announcer.precache_phrases(["Retorno à base iniciado."], attempts=3)
    assert report == announcer.PrecacheReport(synthesized=1, skipped=0, failed=())
    assert len(fake.calls) == 3


def test_interrupted_run_leaves_no_partial_entry(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    statements = [f"Fala número {word} da sequência de teste." for word in ("um", "dois", "três", "quatro", "cinco")]
    cache = announcer.PhraseCache()

    def interrupt_on_third(statement: str, call: int) -> bytes:
        if call == 3:
            raise KeyboardInterrupt
        return _speech_pcm(statement)

    monkeypatch.setattr(announcer, "get_announcer", lambda: FakeSpeaker(interrupt_on_third))
    with pytest.raises(KeyboardInterrupt):
        announcer.precache_phrases(statements, cache=cache)

    # Interrupted between the partial write and the rename, as a Ctrl-C would.
    original_put = announcer.PhraseCache.put

    def put_then_interrupt(self: announcer.PhraseCache, statement: str, verbatim: bool, pcm: bytes) -> None:
        self.path_for(statement, verbatim).with_suffix(f".{os.getpid()}.part").write_bytes(pcm[: len(pcm) // 2])
        raise KeyboardInterrupt

    monkeypatch.setattr(announcer.PhraseCache, "put", put_then_interrupt)
    monkeypatch.setattr(announcer, "get_announcer", lambda: FakeSpeaker())
    with pytest.raises(KeyboardInterrupt):
        announcer.precache_phrases(statements, cache=cache)
    monkeypatch.setattr(announcer.PhraseCache, "put", original_put)

    assert _parts(cache_dir) == []
    present = [s for s in statements if cache.path_for(s, True).exists()]
    assert len(present) == 2
    assert all(cache.get(s, True) == _speech_pcm(s) for s in present)

    resumed = FakeSpeaker()
    monkeypatch.setattr(announcer, "get_announcer", lambda: resumed)
    report = announcer.precache_phrases(statements, cache=cache)
    assert report == announcer.PrecacheReport(synthesized=3, skipped=2, failed=())
    assert resumed.calls == statements[2:]


def test_synthesize_blocking_validates_and_runs_on_the_announcer_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    speaker = announcer.MissionAudioAnnouncer.__new__(announcer.MissionAudioAnnouncer)
    speaker._loop = None
    speaker._active = True
    speaker.session_config = None
    speaker.auth_token = "test-key"
    with pytest.raises(TypeError):
        speaker.synthesize_blocking(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        speaker.synthesize_blocking("Sem sessão.")

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    threads: List[str] = []

    async def fake_synthesize(statement: str, verbatim: bool, on_chunk: Any = None) -> bytes:
        threads.append(threading.current_thread().name)
        return _speech_pcm(statement) if verbatim else b""

    try:
        speaker._loop = loop
        speaker.session_config = object()
        monkeypatch.setattr(speaker, "_synthesize", fake_synthesize)
        assert speaker.synthesize_blocking("Fala no loop.") == _speech_pcm("Fala no loop.")
        assert threads == [runner.name]
        with pytest.raises(ValueError):
            speaker.synthesize_blocking("   ")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=2.0)
        loop.close()


def test_manifest_merges_the_mission_alerts(tmp_path: Path) -> None:
    manifest = precache_speech.load_manifest(_write_manifest(tmp_path))
    statements = manifest.statements()
    alerts = all_alert_sentences()
    assert statements[: len(TS_ENTRIES)] == tuple(e["text"] for e in TS_ENTRIES)
    assert set(alerts) <= set(statements)
    assert len(statements) == len(set(statements))
    assert statements.count("Falha na etapa.") == 1


def test_check_exit_codes(speaker: FakeSpeaker, cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = _write_manifest(tmp_path)
    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert "Pouso concluído com segurança." in out
    assert "[voo.pouso]" in out
    assert speaker.calls == []
    assert not cache_dir.exists() or not any(cache_dir.iterdir())

    statements = precache_speech.load_manifest(manifest_path).statements()
    _fill(announcer.PhraseCache(), statements)
    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 0
    out = capsys.readouterr().out
    assert "faltam: 0" in out
    assert speaker.calls == []


def test_dry_run_counts_by_category_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden() -> Any:
        raise AssertionError("dry run must not build an announcer")

    monkeypatch.setattr(announcer, "get_announcer", forbidden)
    manifest_path = _write_manifest(tmp_path)
    total = len(precache_speech.load_manifest(manifest_path).statements())
    assert precache_speech.main(["--manifest", str(manifest_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"voz: {announcer.SYNTHESIZER_VOICE}" in out
    assert f"modelo: {announcer.SYNTHESIZER_MODEL}" in out
    assert "voo.contagem" in out and "falha.link" in out
    assert f"total: {total}" in out
    assert not cache_dir.exists()


def test_cli_synthesizes_what_is_missing(
    speaker: FakeSpeaker, cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path)
    statements = precache_speech.load_manifest(manifest_path).statements()
    assert precache_speech.main(["--manifest", str(manifest_path)]) == 0
    out = capsys.readouterr().out
    assert f"sintetizadas: {len(statements)}" in out
    assert "falhas: 0" in out
    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 0


def test_cli_refuses_to_synthesize_without_key_or_route(
    monkeypatch: pytest.MonkeyPatch, speaker: FakeSpeaker, cache_dir: Path, tmp_path: Path
) -> None:
    manifest_path = _write_manifest(tmp_path)
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "1")
    assert precache_speech.main(["--manifest", str(manifest_path)]) == 2
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "0")
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: None)
    assert precache_speech.main(["--manifest", str(manifest_path)]) == 2
    assert speaker.calls == []


def test_prune_keeps_manifest_entries_only(cache_dir: Path, tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    statements = precache_speech.load_manifest(manifest_path).statements()
    cache = announcer.PhraseCache()
    _fill(cache, statements)
    _fill(cache, ("Fala de um manifesto antigo.", "Outra fala que saiu do catálogo."))
    stale_part = cache.path_for("Fala interrompida.", True).with_suffix(".999999999.part")
    stale_part.write_bytes(b"\x00" * 100)
    kept = {cache.path_for(s, True).name for s in statements}

    before = _tree(cache_dir)
    assert precache_speech.main(["--manifest", str(manifest_path), "--prune", "--dry-run"]) == 0
    assert _tree(cache_dir) == before
    assert precache_speech.main(["--manifest", str(manifest_path), "--prune"]) == 0
    assert _tree(cache_dir) == before
    assert precache_speech.main(["--manifest", str(manifest_path), "--prune", "--dry-run", "--yes"]) == 0
    assert _tree(cache_dir) == before

    assert precache_speech.main(["--manifest", str(manifest_path), "--prune", "--yes"]) == 0
    assert {path.name for path in cache_dir.iterdir()} == kept
    assert all(cache.get(s, True) == _speech_pcm(s) for s in statements)


def test_prune_without_yes_lists_and_keeps(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path)
    cache = announcer.PhraseCache()
    _fill(cache, precache_speech.load_manifest(manifest_path).statements())
    _fill(cache, ("Fala de um manifesto antigo.",))
    extra = cache.path_for("Fala de um manifesto antigo.", True).name
    assert precache_speech.main(["--manifest", str(manifest_path), "--prune"]) == 0
    out = capsys.readouterr().out
    assert out.index(f"voz: {announcer.SYNTHESIZER_VOICE}") < out.index(extra)
    assert "a remover: 1" in out
    assert (cache_dir / extra).exists()


def test_prune_is_refused_under_another_voice(
    monkeypatch: pytest.MonkeyPatch, cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path)
    cache = announcer.PhraseCache()
    _fill(cache, precache_speech.load_manifest(manifest_path).statements())
    before = _tree(cache_dir)
    monkeypatch.setattr(announcer, "SYNTHESIZER_VOICE", "Puck")

    assert precache_speech.main(["--manifest", str(manifest_path), "--prune", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "SPEECH_VOICE=Puck" in out and "SPEECH_MODEL=" in out
    assert _tree(cache_dir) == before
    with pytest.raises(ValueError):
        precache_speech.prune_cache(precache_speech.load_manifest(manifest_path), announcer.PhraseCache(), confirm=True)
    assert _tree(cache_dir) == before


def _tree(directory: Path) -> Dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def test_pack_unpack_roundtrip(monkeypatch: pytest.MonkeyPatch, cache_dir: Path, tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    statements = precache_speech.load_manifest(manifest_path).statements()
    _fill(announcer.PhraseCache(), statements)
    _fill(announcer.PhraseCache(), ("Fala fora do manifesto.",))
    archive = tmp_path / "bmg-speech.tar.gz"

    assert precache_speech.main(["--manifest", str(manifest_path), "--pack", str(archive)]) == 0
    with tarfile.open(archive, "r:gz") as bundle:
        index = json.load(bundle.extractfile("manifest.json"))  # type: ignore[arg-type]
    assert index["voice"] == announcer.SYNTHESIZER_VOICE
    assert index["model"] == announcer.SYNTHESIZER_MODEL
    expected = {announcer.PhraseCache().path_for(s, True).name: _speech_pcm(s) for s in statements}
    assert {name: entry["sha1"] for name, entry in index["files"].items()} == {
        name: hashlib.sha1(data).hexdigest() for name, data in expected.items()
    }

    target = tmp_path / "fair-machine"
    monkeypatch.setenv(announcer.SPEECH_CACHE_ENV, str(target))
    assert precache_speech.main(["--unpack", str(archive)]) == 0
    assert _tree(target) == expected
    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 0

    other = tmp_path / "other-voice"
    monkeypatch.setenv(announcer.SPEECH_CACHE_ENV, str(other))
    monkeypatch.setattr(announcer, "SYNTHESIZER_VOICE", "Puck")
    with pytest.raises(ValueError):
        precache_speech.unpack_cache(archive, announcer.PhraseCache())
    assert precache_speech.main(["--unpack", str(archive)]) == 2
    assert not other.exists() or not any(other.iterdir())


def test_unpack_with_another_speech_voice_exits_2(cache_dir: Path, tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    statements = precache_speech.load_manifest(manifest_path).statements()
    _fill(announcer.PhraseCache(), statements)
    archive = tmp_path / "bmg-speech.tar.gz"
    assert precache_speech.main(["--manifest", str(manifest_path), "--pack", str(archive)]) == 0

    target = tmp_path / "fair-machine"
    env = dict(os.environ, SPEECH_VOICE="Puck", BMG_SPEECH_CACHE_DIR=str(target), BMG_SPEECH_OFFLINE="1")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--unpack", str(archive)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Puck" in result.stdout + result.stderr
    assert not target.exists() or not any(target.iterdir())


def test_unpack_rejects_a_tampered_archive(cache_dir: Path, tmp_path: Path) -> None:
    statement = "Fala do pacote adulterado."
    cache = announcer.PhraseCache()
    name = cache.path_for(statement, True).name
    data = _speech_pcm(statement)
    index = {
        "schema": precache_speech.PACK_SCHEMA,
        "voice": announcer.SYNTHESIZER_VOICE,
        "model": announcer.SYNTHESIZER_MODEL,
        "files": {name: {"sha1": hashlib.sha1(data).hexdigest(), "bytes": len(data)}},
    }
    archive = tmp_path / "tampered.tar.gz"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "manifest.json").write_text(json.dumps(index), encoding="utf-8")
    (staging / name).write_bytes(data[:-2] + b"\x01\x02")
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(staging / "manifest.json", arcname="manifest.json")
        bundle.add(staging / name, arcname=name)
    with pytest.raises(ValueError):
        precache_speech.unpack_cache(archive, cache)
    assert not cache.path_for(statement, True).exists()
    assert _parts(cache_dir) == []

    escaping = tmp_path / "escaping.tar.gz"
    with tarfile.open(escaping, "w:gz") as bundle:
        bundle.add(staging / "manifest.json", arcname="manifest.json")
        bundle.add(staging / name, arcname=f"../{name}")
    with pytest.raises(ValueError):
        precache_speech.unpack_cache(escaping, cache)
    assert not (cache_dir.parent / name).exists()


def test_table_renders_sections_rows_and_order_rules(tmp_path: Path) -> None:
    manifest = precache_speech.load_manifest(_write_manifest(tmp_path))
    table = precache_speech.render_table(manifest)
    assert table == precache_speech.render_table(manifest)
    for heading in ("## 1. Voo normal", "## 2. Laudo pericial", "## 3. Falhas"):
        assert heading in table
    assert "Cenário A: Veículo, Vítima." in table
    assert "| Condição de disparo | Fonte | Texto |" in table
    assert "Pouso concluído com segurança." in table
    for alert in all_alert_sentences():
        assert alert.replace("|", "\\|") in table


def test_table_is_current(tmp_path: Path) -> None:
    if not (REPO_ROOT / "bebop_mission_control" / "node_modules" / ".bin" / "vite-node").is_file():
        pytest.skip("vite-node not installed: the station manifest cannot be exported")
    generated = tmp_path / "TABELA_DE_FALAS_COPILOTO.md"
    assert precache_speech.main(["--table", str(generated)]) == 0
    assert VERSIONED_TABLE.is_file(), "versioned table missing; run precache_speech.py --table"
    assert generated.read_text(encoding="utf-8") == VERSIONED_TABLE.read_text(encoding="utf-8")


def test_short_cached_entry_counts_as_missing_and_is_replaced(
    speaker: FakeSpeaker, cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path)
    statements = precache_speech.load_manifest(manifest_path).statements()
    cache = announcer.PhraseCache()
    _fill(cache, statements)
    cut = TS_ENTRIES[0]["text"]
    # A turn cut short, as the daemon's warm_cache stores it: above the
    # 4800-byte floor of PhraseCache.get, under 0.25 s per word.
    cache.put(cut, True, _speech_pcm(cut, seconds_per_word=0.2))
    assert cache.has(cut, True)

    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert "curtas: 1" in out
    assert out.index("curtas por categoria") < out.index(f"  {cut}")

    archive = tmp_path / "bmg-speech.tar.gz"
    assert precache_speech.main(["--manifest", str(manifest_path), "--pack", str(archive)]) == 1
    out = capsys.readouterr().out
    assert "recusado: 1" in out and cut in out
    assert not archive.exists()
    assert list(tmp_path.glob("bmg-speech.tar.gz*")) == []

    assert precache_speech.main(["--manifest", str(manifest_path)]) == 0
    assert speaker.calls == [cut]
    assert cache.get(cut, True) == _speech_pcm(cut)
    assert precache_speech.main(["--manifest", str(manifest_path), "--check"]) == 0


def test_precache_overwrites_a_short_entry(speaker: FakeSpeaker, cache_dir: Path) -> None:
    cache = announcer.PhraseCache()
    full, cut = "Altitude de cruzeiro atingida.", "Varredura lateral iniciada à esquerda agora."
    cache.put(full, True, _speech_pcm(full))
    cache.put(cut, True, _speech_pcm(cut, seconds_per_word=0.2))
    report = announcer.precache_phrases([full, cut], cache=cache)
    assert report == announcer.PrecacheReport(synthesized=1, skipped=1, failed=())
    assert speaker.calls == [cut]
    assert cache.get(cut, True) == _speech_pcm(cut)


def test_exporter_never_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(precache_speech, "BMG_ROOT", tmp_path)
    monkeypatch.setattr(precache_speech.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    assert precache_speech.exporter_command() == ["/usr/bin/npx", "--no-install", "vite-node", precache_speech.EXPORTER]

    local = tmp_path / "node_modules" / ".bin" / "vite-node"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    local.chmod(0o755)
    assert precache_speech.exporter_command() == [str(local), precache_speech.EXPORTER]

    local.unlink()
    monkeypatch.setattr(precache_speech.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="--manifest FILE"):
        precache_speech.exporter_command()
    assert precache_speech.main(["--check"]) == 2


def _loop_speaker(monkeypatch: pytest.MonkeyPatch, synthesize: Any) -> Tuple[Any, Any, Any]:
    """An announcer shell bound to a private running loop, with ``_synthesize`` replaced."""
    import asyncio
    import threading

    speaker = announcer.MissionAudioAnnouncer.__new__(announcer.MissionAudioAnnouncer)
    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    speaker._loop = loop
    speaker._active = True
    speaker.session_config = object()
    speaker.auth_token = "test-key"
    monkeypatch.setattr(speaker, "_synthesize", synthesize)
    return speaker, loop, runner


def test_synthesize_blocking_refuses_the_loop_thread_and_cancels_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    cancelled = threading.Event()

    async def slow(statement: str, verbatim: bool, on_chunk: Any = None) -> bytes:
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return b""

    speaker, loop, runner = _loop_speaker(monkeypatch, slow)
    try:
        with pytest.raises(TimeoutError):
            speaker.synthesize_blocking("Fala que não termina.", timeout=0.1)
        assert cancelled.wait(2.0)

        async def from_the_loop() -> str:
            try:
                speaker.synthesize_blocking("Fala no próprio loop.", timeout=0.1)
            except RuntimeError as exc:
                return str(exc)
            return "no error"

        message = asyncio.run_coroutine_threadsafe(from_the_loop(), loop).result(timeout=2.0)
        assert "loop thread" in message
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=2.0)
        loop.close()
