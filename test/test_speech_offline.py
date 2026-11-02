"""The copilot voice with no route to the synthesis service.

At the fair the station runs without network. Every line it can say is
synthesized ahead of time into the phrase cache, so a cached line must play
from disk, a line missing from the cache must give up in milliseconds instead
of paying two session timeouts, and the daemon must report a voice that the
cache alone can serve as ready. No test here opens a socket or an audio
device: reachability, synthesis and playback are all stood in for.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from mvp_mission_bebop.telemetry import announcer

PCM: bytes = b"\x03\x00" * 4800


class _CollectingDevice:
    """Playback stand-in that keeps every buffer it is handed."""

    _active = True
    sample_rate = 24000

    def __init__(self) -> None:
        self.played: List[bytes] = []

    def play_audio(self, pcm: bytes) -> None:
        self.played.append(pcm)

    def wait_until_done(self, timeout: Optional[float] = None) -> bool:
        return True

    def stop_current(self) -> None:
        pass

    def set_level(self, volume: Optional[float] = None, muted: Optional[bool] = None) -> None:
        pass

    def get_level(self) -> Dict[str, Any]:
        return {"volume": 1.0, "muted": False}

    def close(self) -> None:
        pass


OfflineRig = Tuple["announcer.MissionAudioAnnouncer", _CollectingDevice, List[str]]


def _build_rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stub_reachability: bool) -> OfflineRig:
    """A real announcer loop with no API key, a collecting device and no synthesis.

    With ``stub_reachability`` the probe is replaced by a constant False;
    without it the real :func:`announcer.synthesis_reachable` answers.
    """
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path / "speech"))
    if stub_reachability:
        monkeypatch.setattr(announcer, "synthesis_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: None)
    device = _CollectingDevice()
    monkeypatch.setattr(announcer, "get_audio_playback_device", lambda: device)
    opened: List[str] = []

    async def record_open(self: Any) -> Tuple[None, None]:
        opened.append("open")
        return None, None

    async def forbidden_synthesis(self: Any, statement: str, verbatim: bool, on_chunk: Any = None) -> Any:
        raise AssertionError(f"synthesis attempted offline for {statement!r}")

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_open_session", record_open)
    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_synthesize_once", forbidden_synthesis)
    return announcer.MissionAudioAnnouncer(), device, opened


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[OfflineRig]:
    """The rig with reachability stubbed to False."""
    rig = _build_rig(monkeypatch, tmp_path, stub_reachability=True)
    yield rig
    rig[0].close()


@pytest.fixture
def hanging_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[List[Tuple[str, int]]]:
    """A probe whose connect stalls for 2 s, as name resolution does on the Bebop Wi-Fi.

    Yields the endpoints it was asked for. Teardown releases the stall and
    waits for the background refresh to settle before the next test.
    """
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "auto")
    release = threading.Event()
    attempts: List[Tuple[str, int]] = []

    def stall(address: Tuple[str, int], timeout: Optional[float] = None) -> Any:
        attempts.append(address)
        release.wait(2.0)
        raise OSError("name resolution stalled")

    monkeypatch.setattr(socket, "create_connection", stall)
    yield attempts
    release.set()
    deadline = time.monotonic() + 2.0
    while announcer._probes_in_flight and time.monotonic() < deadline:
        time.sleep(0.01)


def test_cached_line_plays_without_network(offline: OfflineRig) -> None:
    instance, device, opened = offline
    statement = "Pouso confirmado na base."
    announcer.PhraseCache().put(statement, True, PCM)

    assert instance.announce(statement, wait=True, verbatim=True) is True
    assert device.played == [PCM]
    assert opened == []


@pytest.fixture
def announcer_records() -> Iterator[List[logging.LogRecord]]:
    """Records of the announcer logger, taken by a handler on that logger.

    ``caplog`` does not receive them under this workspace's pytest plugins;
    ``test_announcer_calls.py`` attaches its handler the same way.
    """
    records: List[logging.LogRecord] = []
    handler = logging.Handler(level=logging.DEBUG)
    handler.emit = records.append  # type: ignore[method-assign]
    target = logging.getLogger("TelemetryAnnouncer")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    yield records
    target.removeHandler(handler)
    target.setLevel(previous)


def test_cache_miss_offline_returns_fast(offline: OfflineRig, announcer_records: List[logging.LogRecord]) -> None:
    instance, device, opened = offline
    statement = "Linha que nunca foi sintetizada."

    started = time.monotonic()
    spoken = instance.announce(statement, wait=True, verbatim=True)
    elapsed = time.monotonic() - started

    assert spoken is False
    assert elapsed < 0.5
    assert opened == []
    assert device.played == []
    misses = [r.getMessage() for r in announcer_records if r.getMessage().startswith("[SPEECH_MISS]")]
    assert misses == [f"[SPEECH_MISS] '{statement}'"]


def test_synthesis_turn_offline_opens_no_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path / "speech"))
    monkeypatch.setattr(announcer, "synthesis_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: "test-token")
    monkeypatch.setattr(announcer, "get_audio_playback_device", _CollectingDevice)
    opened: List[str] = []

    async def record_open(self: Any) -> Tuple[None, None]:
        opened.append("open")
        return None, None

    monkeypatch.setattr(announcer.MissionAudioAnnouncer, "_open_session", record_open)
    instance = announcer.MissionAudioAnnouncer()
    try:
        result = asyncio.run_coroutine_threadsafe(
            instance._synthesize_once("Sem rede.", True), instance._loop
        ).result(timeout=2.0)
    finally:
        instance.close()

    assert result == (b"", "offline")
    assert opened == []


def test_warm_session_is_not_attempted_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, announcer_records: List[logging.LogRecord]
) -> None:
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path / "speech"))
    monkeypatch.setattr(announcer, "synthesis_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(announcer, "_resolve_auth_token", lambda: "test-token")
    monkeypatch.setattr(announcer, "get_audio_playback_device", _CollectingDevice)

    def forbidden_client(token: str) -> Any:
        raise AssertionError("connect attempted offline")

    monkeypatch.setattr(announcer, "_get_synthesis_client", forbidden_client)
    instance = announcer.MissionAudioAnnouncer()
    try:
        for _ in range(3):
            asyncio.run_coroutine_threadsafe(instance._ensure_warm_session(), instance._loop).result(timeout=2.0)
    finally:
        instance.close()

    assert instance._warm_session is None
    assert not [r for r in announcer_records if "warm-up failed" in r.getMessage()]


def test_warm_cache_offline_schedules_nothing(offline: OfflineRig) -> None:
    instance, _device, opened = offline
    assert instance.warm_cache(["Uma.", "Duas."]) == 0
    assert opened == []


def test_voice_readiness_cache_only() -> None:
    assert announcer.voice_readiness(
        genai_available=True, token=False, playback=True, cache_entries=10
    ) == (True, "pronto (voz em cache, sem rede)")
    ready, _detail = announcer.voice_readiness(genai_available=True, token=False, playback=True, cache_entries=0)
    assert ready is False


def test_voice_readiness_keeps_the_synthesis_and_device_verdicts() -> None:
    assert announcer.voice_readiness(genai_available=True, token=True, playback=True, cache_entries=0) == (
        True,
        "pronto",
    )
    assert announcer.voice_readiness(genai_available=True, token=True, playback=False, cache_entries=10) == (
        False,
        "sem dispositivo de áudio (PortAudio)",
    )
    assert announcer.voice_readiness(genai_available=False, token=False, playback=True, cache_entries=0) == (
        False,
        "google-genai não instalado",
    )
    assert announcer.voice_readiness(genai_available=True, token=False, playback=True, cache_entries=0) == (
        False,
        "sem chave de API (SPEECH_API_KEY / GEMINI_API_KEY)",
    )


@pytest.mark.parametrize("entries, error", [(-1, ValueError), (1.5, TypeError), (True, TypeError)])
def test_voice_readiness_rejects_a_bad_entry_count(entries: Any, error: type) -> None:
    with pytest.raises(error):
        announcer.voice_readiness(genai_available=True, token=True, playback=True, cache_entries=entries)


def test_cache_missing_lists_only_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path))
    cache = announcer.PhraseCache()
    cache.put("Já em cache.", True, PCM)

    assert cache.missing(["Já em cache.", " Falta um. ", "Falta um.", "", "Falta dois."]) == [
        "Falta um.",
        "Falta dois.",
    ]
    assert cache.missing(["Já em cache."], verbatim=False) == ["Já em cache."]
    assert cache.missing([]) == []


def test_cache_missing_rejects_a_bare_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMG_SPEECH_CACHE_DIR", str(tmp_path))
    with pytest.raises(TypeError):
        announcer.PhraseCache().missing("Uma frase.")
    with pytest.raises(TypeError):
        announcer.PhraseCache().missing(["Uma frase.", 3])


def test_reachability_honours_the_offline_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_connect(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("probe opened a socket despite the switch")

    monkeypatch.setattr(socket, "create_connection", forbidden_connect)
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "1")
    assert announcer.synthesis_reachable(host="forced-offline.invalid") is False
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "0")
    assert announcer.synthesis_reachable(host="forced-online.invalid") is True


def test_reachability_probe_is_cached_for_its_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(announcer.SPEECH_OFFLINE_ENV, "auto")
    attempts: List[Tuple[str, int]] = []

    def refuse(address: Tuple[str, int], timeout: Optional[float] = None) -> Any:
        attempts.append(address)
        raise OSError("no route to host")

    monkeypatch.setattr(socket, "create_connection", refuse)
    host = "ttl-probe.invalid"
    assert announcer.synthesis_reachable(host=host, ttl_sec=30.0) is False
    assert announcer.synthesis_reachable(host=host, ttl_sec=30.0) is False
    assert attempts == [(host, 443)]


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"host": ""}, ValueError),
        ({"host": 5}, TypeError),
        ({"port": 0}, ValueError),
        ({"port": "443"}, TypeError),
        ({"timeout_sec": 0.0}, ValueError),
        ({"ttl_sec": -1.0}, ValueError),
    ],
)
def test_reachability_rejects_bad_arguments(kwargs: Dict[str, Any], error: type) -> None:
    with pytest.raises(error):
        announcer.synthesis_reachable(**kwargs)


def _speech_events(text: str) -> List[Dict[str, Any]]:
    return [
        json.loads(line[len(announcer.SPEECH_EVENT_PREFIX):])
        for line in text.splitlines()
        if line.startswith(announcer.SPEECH_EVENT_PREFIX)
    ]


def test_daemon_reports_cache_backed_voice_and_cache_gaps(
    offline: OfflineRig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance, _device, opened = offline
    announcer.PhraseCache().put("Já em cache.", True, PCM)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(announcer, "get_announcer", lambda: instance)
    requests = [
        {"op": "cache", "texts": ["Já em cache.", "Falta um.", "Falta dois."]},
        {"op": "quit"},
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))

    assert announcer.serve_stdin() == 0

    events = _speech_events(capsys.readouterr().out)
    ready = next(event for event in events if event["event"] == "ready")
    cache = next(event for event in events if event["event"] == "cache")
    assert ready["voice_ready"] is True
    assert ready["cache_entries"] == 1
    assert ready["offline"] is True
    assert ready["detail"] == "pronto (voz em cache, sem rede)"
    assert cache["scheduled"] == 0
    assert cache["requested"] == 3
    assert cache["missing"] == 2
    assert cache["offline"] is True
    assert opened == []


def test_stale_verdict_is_returned_while_one_refresh_runs(hanging_probe: List[Tuple[str, int]]) -> None:
    host = "stale-refresh.invalid"
    announcer._reachability[(host, 443)] = (time.monotonic() - 100.0, False)

    started = time.monotonic()
    verdicts = [announcer.synthesis_reachable(host=host, ttl_sec=0.0) for _ in range(5)]
    elapsed = time.monotonic() - started

    assert verdicts == [False] * 5
    assert elapsed < 0.1
    deadline = time.monotonic() + 1.0
    while not hanging_probe and time.monotonic() < deadline:
        time.sleep(0.01)
    assert hanging_probe == [(host, 443)]


def test_first_verdict_waits_at_most_the_probe_timeout(hanging_probe: List[Tuple[str, int]]) -> None:
    started = time.monotonic()
    assert announcer.synthesis_reachable(host="first-probe.invalid", timeout_sec=0.2) is False
    assert time.monotonic() - started < 0.5


def test_cache_miss_resolves_fast_while_a_stale_probe_hangs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    hanging_probe: List[Tuple[str, int]],
    announcer_records: List[logging.LogRecord],
) -> None:
    endpoint = (announcer.SYNTHESIS_HOST, announcer.SYNTHESIS_PORT)
    announcer._reachability[endpoint] = (time.monotonic() - 100.0, False)
    instance, device, opened = _build_rig(monkeypatch, tmp_path, stub_reachability=False)
    try:
        started = time.monotonic()
        spoken = instance.announce("Linha fora do cache.", wait=True, verbatim=True)
        elapsed = time.monotonic() - started
    finally:
        instance.close()

    assert spoken is False
    assert elapsed < 0.5
    assert opened == []
    assert device.played == []
    assert any(r.getMessage().startswith("[SPEECH_MISS]") for r in announcer_records)


def test_a_whole_buffer_reports_its_start_and_length(offline: OfflineRig) -> None:
    """The station types a finding over the audio: it needs when it starts and how long it lasts."""
    instance, device, _opened = offline
    statement = "Linha com duração conhecida."
    announcer.PhraseCache().put(statement, True, PCM)
    starts: List[Optional[float]] = []

    assert instance.announce(statement, wait=True, verbatim=True, on_start=starts.append) is True

    assert device.played == [PCM]
    assert starts == [pytest.approx(len(PCM) / (2.0 * device.sample_rate) * 1000.0)]


def test_a_line_never_played_never_reports_a_start(offline: OfflineRig) -> None:
    instance, _device, _opened = offline
    starts: List[Optional[float]] = []
    assert instance.announce("Linha fora do cache, sem rede.", wait=True, verbatim=True, on_start=starts.append) is False
    assert starts == []


def test_daemon_says_when_a_line_starts_and_for_how_long(
    offline: OfflineRig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance, _device, _opened = offline
    statement = "Vítima consciente."
    announcer.PhraseCache().put(statement, True, PCM)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(announcer, "get_announcer", lambda: instance)
    requests = [{"id": 7, "text": statement, "verbatim": True}, {"op": "quit"}]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))

    assert announcer.serve_stdin() == 0

    events = [event for event in _speech_events(capsys.readouterr().out) if event.get("id") == 7]
    assert [event["event"] for event in events] == ["start", "done"]
    assert events[0]["audio_ms"] == round(len(PCM) / (2.0 * 24000) * 1000.0)
    assert events[1]["ok"] is True
