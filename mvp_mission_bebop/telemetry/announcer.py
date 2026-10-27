"""Acoustic telemetry and synthesized speech notification system.

Provides asynchronous, non-blocking telemetry voice notifications for
ground operators during autonomous mission execution.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import hashlib
import json
import logging
import math
import os
import pathlib
import queue
import random
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Final, Iterable, List, Optional, Sequence, Tuple

from mvp_mission_bebop.telemetry.inspection_report import (
    FORENSIC_CLOSERS,
    FORENSIC_FINDINGS,
    FORENSIC_LINKERS,
    FORENSIC_OPENERS,
    HISTORY_FILENAME,
    _decapitalise,
    build_inspection_report,
    victim_status_of,
)

#: ``google.genai`` and its ``types``, loaded by :func:`_load_genai` on the
#: first synthesis. Importing them costs ~1.8 s, and a mission under a ground
#: station session never synthesizes (``station_narrates``), so the import is
#: no longer paid at module load (7.1).
genai: Any = None
types: Any = None
_genai_loaded = False
_genai_lock = threading.Lock()


def _load_genai() -> Tuple[Any, Any]:
    """``(google.genai, google.genai.types)``, imported once; ``(None, None)`` if absent."""
    global genai, types, _genai_loaded
    with _genai_lock:
        if not _genai_loaded:
            try:
                from google import genai as genai_module
                from google.genai import types as types_module
            except ImportError:
                genai_module = types_module = None
            genai, types = genai_module, types_module
            _genai_loaded = True
        return genai, types

logger = logging.getLogger("TelemetryAnnouncer")

PCM_SAMPLE_RATE = 24_000
PCM_CHANNELS = 1
PCM_DTYPE = "int16"
SYNTHESIZER_MODEL = os.environ.get("SPEECH_MODEL", "gemini-3.1-flash-live-preview")
SYNTHESIZER_VOICE = os.environ.get("SPEECH_VOICE", "Orbit")
SYNTHESIZER_LANGUAGE: Final[str] = "pt-BR"

#: Least audio that counts as a spoken line: 0.1 s of 24 kHz mono int16.
_MIN_AUDIO_BYTES: Final[int] = 4800
#: Wait for a line's first audio chunk. Measured first-chunk latency is 1.6 to
#: 3.2 s; a session that has said nothing after 6 s is stalled, and a fresh
#: one answers sooner than it would.
_FIRST_AUDIO_TIMEOUT_SEC: Final[float] = 6.0
#: Wait between chunks once audio is flowing. Server gaps of 1.3 s were
#: measured mid-sentence, which the former 1.5 s cut left no margin for.
_CHUNK_GAP_TIMEOUT_SEC: Final[float] = 3.0
#: Lines synthesized ahead of their turn, oldest evicted first.
_PREFETCH_LIMIT: Final[int] = 4
#: Age past which the idle warm Live session is recycled rather than used.
_WARM_MAX_AGE_SEC: Final[float] = 40.0
#: How often the idle warm session is checked. The station starts the daemon
#: with the app, minutes before the first line, so a session opened once at
#: start-up would be long stale by then; checking on this period keeps the
#: one on hand at most _WARM_MAX_AGE_SEC + _WARM_REFRESH_SEC old.
_WARM_REFRESH_SEC: Final[float] = 10.0

# The Live model infers accent from the prompt as much as from the session
# locale, and without an explicit ban it drifts to European Portuguese on
# short, formal sentences. Pace is set here too: the SDK (google-genai 2.25)
# has no speaking-rate field, and Part.speech_metadata.style was measured to
# change nothing on a Live session, while this wording measured about 14 %
# faster (0.51 s per word against 0.58-0.60) with every line synthesized.
_ACCENT_DIRECTIVE: Final[str] = (
    "Speak in Brazilian Portuguese (pt-BR) with a native Brazilian accent and cadence; "
    "never use European Portuguese pronunciation, vocabulary or rhythm. "
    "Sound like a confident human presenter running a live demonstration: brisk, energetic, "
    "naturally varied intonation, never robotic, no drawn-out pauses between words or sentences. "
    "Speak about 20 percent faster than an ordinary conversation, keeping every word clear."
)


def synthesis_instruction(statement: str, verbatim: bool) -> str:
    """Build the prompt that makes the Live model speak one statement.

    Parameters
    ----------
    statement : str
        Sentence to be spoken, already in Portuguese.
    verbatim : bool
        When true the sentence is read exactly as written. The station shows
        that same string on screen while it is spoken, so any paraphrase would
        desynchronise the card from the narration. When false the model may
        rephrase the operational status, bounded to a flight-call length.

    Returns
    -------
    str
        Instruction sent as the user turn of the synthesis session.

    Raises
    ------
    TypeError
        If ``statement`` is not a string.
    ValueError
        If ``statement`` is empty or whitespace.
    """
    if not isinstance(statement, str):
        raise TypeError(f"statement must be str, got {type(statement).__name__}")
    if not statement.strip():
        raise ValueError("statement must not be empty")

    if verbatim:
        return (
            f"Read the following sentence aloud, exactly as written, "
            f"with no additions, no preamble and no rewording: '{statement}'. "
            f"{_ACCENT_DIRECTIVE} You are the autonomous flight copilot presenting the mission."
        )
    # Spoken pt-BR at this register runs near 2.5 words per second, so twelve
    # words keep a flight call within three to four seconds.
    return (
        f"Vocalize this operational status concisely, similar to: '{statement}'. "
        f"{_ACCENT_DIRECTIVE} Use at most twelve words, three to four seconds of speech. "
        "You are the autonomous flight copilot presenting the mission. "
        "Never add greetings, conversational filler, or address spectators."
    )


#: Station override of the synthesis reachability probe: ``"1"`` forces the
#: copilot offline (cache only), ``"0"`` never treats it as offline, and an
#: absent, empty or ``"auto"`` value probes the endpoint.
SPEECH_OFFLINE_ENV: Final[str] = "BMG_SPEECH_OFFLINE"
SYNTHESIS_HOST: Final[str] = "generativelanguage.googleapis.com"
SYNTHESIS_PORT: Final[int] = 443

#: Last probe per endpoint: ``(monotonic instant, reachable)``.
_reachability: Dict[Tuple[str, int], Tuple[float, bool]] = {}
#: Probe running per endpoint, set when its verdict is stored. One at a time,
#: so concurrent callers never stack connects or repeat the transition log.
_probes_in_flight: Dict[Tuple[str, int], threading.Event] = {}
_reachability_lock = threading.Lock()


def _probe_endpoint(endpoint: Tuple[str, int], timeout_sec: float, done: threading.Event) -> None:
    """Connect once to ``endpoint``, store the verdict and log a change of state."""
    failure: Optional[OSError] = None
    try:
        with socket.create_connection(endpoint, timeout=timeout_sec):
            reachable = True
    except OSError as exc:
        reachable = False
        failure = exc
    with _reachability_lock:
        previous = _reachability.get(endpoint)
        _reachability[endpoint] = (time.monotonic(), reachable)
        if _probes_in_flight.get(endpoint) is done:
            del _probes_in_flight[endpoint]
    done.set()
    if not reachable and (previous is None or previous[1]):
        # Once per streak: the daemon's stderr reaches the station's log as
        # "[copiloto]" lines, and from here on only cached lines speak.
        logger.warning("Speech synthesis unreachable (%s:%d: %s); serving cached lines only.", *endpoint, failure)
    elif reachable and previous is not None and not previous[1]:
        logger.warning("Speech synthesis reachable again (%s:%d).", *endpoint)


def synthesis_reachable(
    host: str = SYNTHESIS_HOST,
    port: int = SYNTHESIS_PORT,
    timeout_sec: float = 1.0,
    ttl_sec: float = 30.0,
) -> bool:
    """Whether the Live synthesis endpoint accepts a TCP connection.

    Without a route every session attempt costs its 3 s connect timeout, twice
    per line, so a line missing from the phrase cache stayed silent for up to
    the 14 s speech ceiling. One TCP connect answers the same question.

    The probe runs on a background thread. Once an endpoint has a verdict it
    is returned immediately, and a verdict older than ``ttl_sec`` is refreshed
    in the background (one probe per endpoint at a time), so no caller after
    the first waits on name resolution, which no socket timeout bounds and
    which stalls for seconds on a network without DNS such as the Bebop's
    Wi-Fi. The first call waits for the first verdict at most ``timeout_sec``
    and reads an unanswered probe as unreachable.

    Parameters
    ----------
    host : str
        Endpoint host name.
    port : int
        Endpoint TCP port, 1 to 65535.
    timeout_sec : float
        Connect timeout of one probe, and the longest the first call waits for
        a verdict, seconds; must be positive.
    ttl_sec : float
        Age past which a returned verdict also starts a background refresh;
        zero refreshes on every call.

    Returns
    -------
    bool
        False when :data:`SPEECH_OFFLINE_ENV` is ``"1"``, True when it is
        ``"0"``. Otherwise the last stored verdict, or for an endpoint without
        one, whether the first probe connected within ``timeout_sec``. Any
        other value of the variable behaves as ``"auto"``.

    Raises
    ------
    TypeError
        If ``host`` is not a string, ``port`` is not an integer, or a duration
        is not a real number.
    ValueError
        If ``host`` is empty, ``port`` is out of range, ``timeout_sec`` is not
        positive or ``ttl_sec`` is negative.
    """
    if not isinstance(host, str):
        raise TypeError(f"host must be str, got {type(host).__name__}")
    if not host.strip():
        raise ValueError("host must not be empty")
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError(f"port must be int, got {type(port).__name__}")
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be in [1, 65535], got {port}")
    for name, value in (("timeout_sec", timeout_sec), ("ttl_sec", ttl_sec)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    if timeout_sec <= 0.0:
        raise ValueError(f"timeout_sec must be positive, got {timeout_sec}")
    if ttl_sec < 0.0:
        raise ValueError(f"ttl_sec must not be negative, got {ttl_sec}")

    mode = os.environ.get(SPEECH_OFFLINE_ENV, "auto").strip().lower()
    if mode == "1":
        return False
    if mode == "0":
        return True

    endpoint = (host.strip(), port)
    with _reachability_lock:
        previous = _reachability.get(endpoint)
        pending = _probes_in_flight.get(endpoint)
        stale = previous is None or (time.monotonic() - previous[0]) >= ttl_sec
        if stale and pending is None:
            pending = threading.Event()
            _probes_in_flight[endpoint] = pending
            threading.Thread(
                target=_probe_endpoint,
                args=(endpoint, float(timeout_sec), pending),
                name="SpeechReachabilityProbe",
                daemon=True,
            ).start()
    if previous is not None:
        return previous[1]
    if pending is None or not pending.wait(timeout=float(timeout_sec)):
        return False
    with _reachability_lock:
        verdict = _reachability.get(endpoint)
    return bool(verdict and verdict[1])


def voice_readiness(
    *,
    genai_available: bool,
    token: bool,
    playback: bool,
    cache_entries: int,
) -> Tuple[bool, str]:
    """Whether the copilot can speak, and the reason shown to the operator.

    The voice is ready when there is an output device and either live
    synthesis (the ``google.genai`` client plus an API key) or a non-empty
    phrase cache. The cache case covers the station without network or key:
    every line it says is synthesized ahead of the flight, so reporting it as
    "sem voz" would block a launch whose narration is already on disk.

    Parameters
    ----------
    genai_available : bool
        Whether ``google.genai`` imports.
    token : bool
        Whether an API key was resolved.
    playback : bool
        Whether the audio output device started.
    cache_entries : int
        Usable phrase cache entries on disk; must not be negative.

    Returns
    -------
    Tuple[bool, str]
        ``(ready, detail)``. ``detail`` is ``"pronto"`` with live synthesis,
        ``"pronto (voz em cache, sem rede)"`` when only the cache serves, and
        otherwise names the first missing piece in the order synthesis client,
        API key, audio device.

    Raises
    ------
    TypeError
        If ``cache_entries`` is not an integer.
    ValueError
        If ``cache_entries`` is negative.
    """
    if isinstance(cache_entries, bool) or not isinstance(cache_entries, int):
        raise TypeError(f"cache_entries must be int, got {type(cache_entries).__name__}")
    if cache_entries < 0:
        raise ValueError(f"cache_entries must not be negative, got {cache_entries}")

    synthesis = bool(genai_available) and bool(token)
    if playback and synthesis:
        return True, "pronto"
    if playback and cache_entries > 0:
        return True, "pronto (voz em cache, sem rede)"
    if not genai_available:
        return False, "google-genai não instalado"
    if not token:
        return False, "sem chave de API (SPEECH_API_KEY / GEMINI_API_KEY)"
    return False, "sem dispositivo de áudio (PortAudio)"


_cached_client: Optional[Any] = None
_client_lock = threading.Lock()


def _get_synthesis_client(api_key: str) -> Any:
    global _cached_client
    with _client_lock:
        if _cached_client is None:
            genai_module, _ = _load_genai()
            if genai_module is None:
                raise RuntimeError("Speech synthesis client dependencies not installed.")
            _cached_client = genai_module.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        return _cached_client


def _resolve_auth_token() -> Optional[str]:
    """Resolve authentication credentials from environment or dot-env files."""
    key = (
        os.environ.get("SPEECH_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )
    if key:
        return key

    # The workspace root is the natural place for this file, and the daemon's
    # working directory is the package rather than the workspace, so `getcwd()`
    # alone never finds it. The path is derived from this module rather than
    # hardcoded: the previous list named another machine's home directory.
    workspace_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(workspace_root, ".env"),
        os.path.expanduser("~/ros2_ws/.env"),
        os.path.expanduser("~/jarvis/.env"),
        os.path.expanduser("~/.env"),
    ]

    try:
        from dotenv import load_dotenv

        for path in env_paths:
            if os.path.isfile(path):
                load_dotenv(path, override=False)
                key = (
                    os.environ.get("SPEECH_API_KEY", "").strip()
                    or os.environ.get("GOOGLE_API_KEY", "").strip()
                    or os.environ.get("GEMINI_API_KEY", "").strip()
                )
                if key:
                    return key
    except ImportError:
        pass

    for path in env_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k in ("SPEECH_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY") and v:
                            os.environ[k] = v
                            return v
            except Exception:
                pass

    return None


#: Output written per call; a stop takes effect within one block.
_PLAYBACK_BLOCK_SEC: Final[float] = 0.05


class AudioPlaybackDevice:
    """Non-blocking PCM audio playback worker using sounddevice."""

    def __init__(
        self,
        sample_rate: int = PCM_SAMPLE_RATE,
        channels: int = PCM_CHANNELS,
        dtype: str = PCM_DTYPE,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype

        self._queue: "queue.Queue[Optional[Tuple[int, bytes]]]" = queue.Queue()
        # Output level, applied to the samples on their way to the device.
        # Scaling PCM here rather than touching the host mixer keeps the
        # station's voice independent of everything else the machine is
        # playing, which is what an operator adjusting "the copilot" means.
        self._volume: float = 1.0
        self._muted: bool = False
        self._thread: Optional[threading.Thread] = None
        self._active: bool = False
        self._is_playing: bool = False
        #: Bumped by :meth:`stop_current`; buffers queued under an older value
        #: are dropped, and the line being written is abandoned.
        self._generation: int = 0
        self._playback_finished: threading.Event = threading.Event()
        self._playback_finished.set()
        self._lock = threading.Lock()

    def start(self) -> bool:
        """Start audio playback worker thread."""
        with self._lock:
            if self._active:
                return True
            try:
                import sounddevice  # noqa: F401
                self._active = True
                self._thread = threading.Thread(
                    target=self._playback_worker,
                    name="AudioPlaybackWorker",
                    daemon=True,
                )
                self._thread.start()
                return True
            except Exception as exc:
                logger.warning("Audio device initialization unavailable (%s). Audio notifications disabled.", exc)
                self._active = False
                return False

    def set_level(self, volume: Optional[float] = None, muted: Optional[bool] = None) -> None:
        """Set output gain and mute state. Takes effect on the next buffer."""
        with self._lock:
            if volume is not None:
                self._volume = max(0.0, min(1.0, float(volume)))
            if muted is not None:
                self._muted = bool(muted)

    def get_level(self) -> Dict[str, Any]:
        with self._lock:
            return {"volume": self._volume, "muted": self._muted}

    def play_audio(self, pcm_data: bytes) -> None:
        """Enqueue PCM audio for playback, after whatever is already queued.

        Consecutive buffers -- the lines of a report, or the chunks of one line
        as synthesis streams them -- are written to one open output stream, so
        a line can start on its first chunk and there is no device reopen
        between them.
        """
        if not pcm_data or not self._active:
            return
        self._playback_finished.clear()
        self._queue.put((self._generation, pcm_data))

    def _open_stream(self) -> Any:
        import sounddevice as sd

        stream = sd.RawOutputStream(samplerate=self.sample_rate, channels=self.channels, dtype=self.dtype)
        stream.start()
        return stream

    @staticmethod
    def _close_stream(stream: Any, *, abort: bool) -> None:
        if stream is None:
            return
        try:
            if abort:
                stream.abort()
            else:
                stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001 - a dead stream is replaced on the next buffer
            logger.debug("Output stream close: %s", exc)

    def _playback_worker(self) -> None:
        import numpy as np

        stream = None
        block_bytes = max(1, int(self.sample_rate * _PLAYBACK_BLOCK_SEC)) * 2 * self.channels

        while self._active:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._is_playing:
                    self._is_playing = False
                    self._playback_finished.set()
                continue
            if item is None:
                self._queue.task_done()
                break
            generation, data = item
            try:
                if generation != self._generation or not data:
                    continue
                self._is_playing = True
                self._playback_finished.clear()
                with self._lock:
                    gain = 0.0 if self._muted else self._volume
                # Written in blocks of _PLAYBACK_BLOCK_SEC and checked between
                # them, so a cancel or an urgent line silences this one within
                # one block (the target is under 100 ms).
                if gain <= 0.0:
                    # Nothing to play, but the timing still has to be the
                    # timing: the station reveals a card per line read, and a
                    # muted copilot returning instantly would collapse the
                    # report into one frame. The buffer's duration is the wait.
                    remaining = len(data) / (2.0 * self.channels * self.sample_rate)
                    while remaining > 0.0 and generation == self._generation and self._active:
                        step = min(_PLAYBACK_BLOCK_SEC, remaining)
                        time.sleep(step)
                        remaining -= step
                    continue
                if gain < 1.0:
                    samples = np.frombuffer(data, dtype=np.int16)
                    data = (samples.astype(np.float32) * gain).astype(np.int16).tobytes()
                if stream is None:
                    stream = self._open_stream()
                for offset in range(0, len(data), block_bytes):
                    if generation != self._generation or not self._active:
                        self._close_stream(stream, abort=True)
                        stream = None
                        break
                    try:
                        if stream is not None:
                            stream.write(data[offset:offset + block_bytes])
                    except Exception as stream_err:
                        logger.warning("Sounddevice stream write error (%s); retrying reopen once", stream_err)
                        self._close_stream(stream, abort=True)
                        stream = self._open_stream()
                        if stream is not None:
                            try:
                                stream.write(data[offset:offset + block_bytes])
                            except Exception as retry_err:
                                logger.warning("Sounddevice stream write retry failed: %s", retry_err)
                                self._close_stream(stream, abort=True)
                                stream = None
                                break
                        else:
                            break
            finally:
                self._queue.task_done()

        self._close_stream(stream, abort=True)
        self._is_playing = False
        self._playback_finished.set()

    def wait_until_done(self, timeout: Optional[float] = None) -> bool:
        """Block until the queue is empty and playback finishes."""
        if not self._active:
            return True

        start_time = time.time()
        while not self._queue.empty():
            if timeout and (time.time() - start_time) > timeout:
                return False
            time.sleep(0.02)

        remaining = None
        if timeout:
            remaining = max(0.0, timeout - (time.time() - start_time))
        return self._playback_finished.wait(timeout=remaining)

    def stop_current(self) -> None:
        """Halt active playback and drop the queued audio.

        Bumps the playback generation: the worker drops every buffer queued
        under the previous one and abandons the line it is writing at the next
        block boundary. Nothing needs clearing afterwards, so a buffer queued
        right after the stop plays normally.
        """
        with self._lock:
            self._generation += 1
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except (queue.Empty, ValueError):
                break
        self._is_playing = False
        self._playback_finished.set()

    def close(self) -> None:
        """Shut down the playback worker and wait for it to leave PortAudio.

        The worker reads the gain under ``_lock``, so the lock is released
        before joining it. It notices ``_active`` at the next block boundary
        and aborts its stream; returning only once it has is what keeps a
        thread from still being inside PortAudio when the interpreter
        finalizes, which crashed the process on exit.
        """
        with self._lock:
            self._active = False
            self._generation += 1
            thread = self._thread
            self._thread = None
        self._queue.put(None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


_global_playback_device: Optional[AudioPlaybackDevice] = None
_playback_lock = threading.Lock()


_audio_exit_registered: bool = False


def _close_audio_at_exit() -> None:
    """Stop the announcer and the playback worker before interpreter shutdown."""
    announcer_instance = _global_announcer if "_global_announcer" in globals() else None
    if announcer_instance is not None:
        try:
            announcer_instance.close()
        except Exception:  # noqa: BLE001 - exit path
            pass
    device = _global_playback_device
    if device is not None:
        try:
            device.close()
        except Exception:  # noqa: BLE001 - exit path
            pass


def get_audio_playback_device() -> AudioPlaybackDevice:
    """Retrieve or initialize singleton audio playback device.

    The first call registers :func:`_close_audio_at_exit`: a playback thread
    left running inside PortAudio when the interpreter finalizes crashed the
    mission process on exit (SIGSEGV, SIGABRT ``malloc_consolidate``).
    """
    global _global_playback_device, _audio_exit_registered
    with _playback_lock:
        if _global_playback_device is None:
            _global_playback_device = AudioPlaybackDevice()
            _global_playback_device.start()
        if not _audio_exit_registered:
            atexit.register(_close_audio_at_exit)
            _audio_exit_registered = True
        return _global_playback_device


#: Failures that happen before or without flight: each names itself and its
#: real reason. None of them lands anything unless the payload says the
#: aircraft is airborne (``details["em_voo"]``).
_GROUND_FAULT_ACTIONS: Final[Tuple[str, ...]] = (
    "falha na inicialização",
    "falha na calibração",
    "falha na decolagem",
    "falha na etapa",
    "nivelamento sem confirmação",
)

#: Appended to a fault sentence only when the aircraft is airborne.
_LANDING_CLAUSE: Final[str] = "Executando pouso seguro imediatamente."


def _sentence(text: str) -> str:
    """``text`` closed by exactly one period."""
    return text.strip().rstrip(".").strip() + "."


#: Forced return on a critical charge (``steps/rtl.py``), said by the mission
#: only when it narrates alone. Each is the first variant of the station's pool
#: for the profile (``PHRASE_POOLS['mission.battery_return']`` and
#: ``['mission.battery_return_simple']`` in ``src/lib/copilotPhrases.ts``), so
#: the phrase cache holds one audio for both voices.
BATTERY_RETURN_ACTION: Final[str] = "Retorno por bateria crítica"
_BATTERY_RETURN_SENTENCE: Final[str] = "Bateria crítica. Missão interrompida. Retornando à base para pouso."
_BATTERY_RETURN_SIMPLE_SENTENCE: Final[str] = "Bateria crítica. Missão interrompida. Pouso na base iniciado."


def _format_telemetry_statement(
    action: str,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Format structured telemetry action into concise aeronautical phrase.

    A fault states its real reason (``details["erro"]`` or ``details["etapa"]``)
    and claims a landing only when ``details["em_voo"]`` is true: a failure on
    the ground used to be announced with "Executando pouso seguro" while the
    aircraft sat on the pad. :data:`BATTERY_RETURN_ACTION` is said in the
    wording of the profile in ``details["modo"]`` (``"simple"`` or
    ``"advanced"``, the latter when absent).
    """
    details = details or {}
    action_lower = action.strip().lower()
    airborne = details.get("em_voo") is True
    reason = details.get("erro", details.get("etapa"))

    if action_lower == BATTERY_RETURN_ACTION.lower():
        if details.get("modo") == "simple":
            return _BATTERY_RETURN_SIMPLE_SENTENCE
        return _BATTERY_RETURN_SENTENCE

    if action_lower in _GROUND_FAULT_ACTIONS:
        head = action.strip() if not reason else f"{action.strip()}: {str(reason).strip()}"
        statement = _sentence(head)
        return f"{statement} {_LANDING_CLAUSE}" if airborne else statement

    is_fault = "erro" in details or "erro" in action_lower or "falha" in action_lower
    if is_fault:
        err = str(details.get("erro", action)).strip()
        if airborne:
            return f"{_sentence(f'Alerta de voo: {err}')} {_LANDING_CLAUSE}"
        return _sentence(f"Alerta de segurança: {err}")

    if "abortar" in action_lower or "abortada" in action_lower or "abort" in action_lower:
        if details and "etapa" in details:
            return _sentence(str(details["etapa"]).capitalize())
        if details and "acao" in details:
            return f"Missão abortada, {details['acao']}."
        return "Missão abortada, pousando drone."
    if "iniciando missão" in action_lower:
        plan = details.get("plano")
        if isinstance(plan, str) and plan.strip():
            return f"Missão iniciada. Parâmetros de voo carregados, {plan.strip()}."
        return "Missão iniciada. Parâmetros de voo carregados."
    if "decolagem autorizada" in action_lower or "contagem" in action_lower:
        return "Decolagem autorizada. Iniciando voo autônomo."
    if "decolagem concluída" in action_lower:
        return "Decolagem concluída. Iniciando varredura da pista."
    if "acidente detectado" in action_lower or "alvo detectado" in action_lower:
        return "Alvo detectado na pista. Iniciando aproximação."
    if "alvo centrado" in action_lower or "alinhamento" in action_lower:
        return "Alvo centrado. Iniciando inspeção estacionária."
    if "evidência" in action_lower or "foto" in action_lower:
        return "Registro fotográfico concluído. Evidência armazenada."
    if "pouso no local" in action_lower:
        return "Pouso no local iniciado. Inspeção concluída."
    if "retorno à base" in action_lower or "rtl" in action_lower:
        return "Retorno à base iniciado. Navegando para o ponto de lançamento."
    if "pouso seguro" in action_lower or "pouso concluído" in action_lower:
        return "Pouso seguro concluído com sucesso."

    if details and "etapa" in details:
        return _sentence(str(details["etapa"]).capitalize())

    return f"Notificação de missão: {action}."


#: Environment override of the phrase cache directory (tests, alternate stations).
SPEECH_CACHE_ENV: Final[str] = "BMG_SPEECH_CACHE_DIR"


class PhraseCache:
    """Synthesized PCM on disk, one file per sentence, voice and model.

    Every sentence the station speaks is known before the flight: the phrase
    pools with the numbers already resolved from the mission's own parameters
    (D2), the forensic report's finite combinations (D1) and the fixed alerts.
    A line read from here starts in milliseconds instead of paying a Live turn.
    The key covers the prompt mode as well, because a verbatim and a free
    rendering of the same text are different audio.
    """

    def __init__(self, directory: Optional[pathlib.Path] = None) -> None:
        override = os.environ.get(SPEECH_CACHE_ENV)
        default = pathlib.Path.home() / ".cache" / "bmg" / "speech"
        self.directory = directory or (pathlib.Path(override) if override else default)

    def path_for(self, statement: str, verbatim: bool) -> pathlib.Path:
        """Cache file of ``statement`` in the given prompt mode."""
        key = "\x1f".join((statement.strip(), "verbatim" if verbatim else "free", SYNTHESIZER_VOICE, SYNTHESIZER_MODEL))
        return self.directory / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.pcm"

    def get(self, statement: str, verbatim: bool) -> Optional[bytes]:
        """The cached PCM, or None when absent or too short to be speech."""
        try:
            data = self.path_for(statement, verbatim).read_bytes()
        except OSError:
            return None
        return data if len(data) >= _MIN_AUDIO_BYTES else None

    def has(self, statement: str, verbatim: bool) -> bool:
        """Whether a usable entry exists."""
        return self.get(statement, verbatim) is not None

    def put(self, statement: str, verbatim: bool, pcm: bytes) -> None:
        """Store ``pcm`` atomically; a failed write only costs the next synthesis."""
        if len(pcm) < _MIN_AUDIO_BYTES:
            return
        path = self.path_for(statement, verbatim)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(f".{os.getpid()}.part")
            partial.write_bytes(pcm)
            os.replace(partial, path)
        except OSError as exc:
            logger.debug("Phrase cache write failed (%s): %s", path, exc)

    def missing(self, statements: Iterable[str], verbatim: bool = True) -> List[str]:
        """The statements without a usable entry, stripped and deduplicated.

        Parameters
        ----------
        statements : Iterable[str]
            Sentences the station may speak. Empty or whitespace-only entries
            are ignored.
        verbatim : bool
            Prompt mode the entries are looked up in.

        Returns
        -------
        List[str]
            Absent statements in first-seen order.

        Raises
        ------
        TypeError
            If ``statements`` is a bare string or yields a non-string item.
        """
        if isinstance(statements, (str, bytes)):
            raise TypeError("statements must be an iterable of str, not a single string")
        absent: List[str] = []
        seen: set[str] = set()
        for item in statements:
            if not isinstance(item, str):
                raise TypeError(f"statement must be str, got {type(item).__name__}")
            statement = item.strip()
            if not statement or statement in seen:
                continue
            seen.add(statement)
            if not self.has(statement, verbatim):
                absent.append(statement)
        return absent

    def entries(self) -> int:
        """Number of entries on disk long enough to be speech.

        Counted by file, so entries of another voice or model in the same
        directory are included; the key hash does not reveal them.
        """
        try:
            return sum(
                1 for path in self.directory.glob("*.pcm") if path.is_file() and path.stat().st_size >= _MIN_AUDIO_BYTES
            )
        except OSError:
            return 0


class MissionAudioAnnouncer:
    """Asynchronous priority-based audio announcer for mission flight milestones."""

    def __init__(self) -> None:
        self.auth_token = _resolve_auth_token()
        self.device = get_audio_playback_device()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.PriorityQueue] = None
        self._warm_session: Optional[Any] = None
        self._warm_ctx: Optional[Any] = None
        self._warm_session_time: float = 0.0
        self._is_warming: bool = False
        #: Whether the last warm-up failed, so a failure is reported once per
        #: streak rather than on every refresh.
        self._warm_failed: bool = False
        self._lock = threading.Lock()
        self._seq: int = 0
        self._active: bool = True
        #: Lines synthesized ahead of their request, by statement. Touched only
        #: on the announcer's event loop thread.
        self._prefetched: Dict[str, "asyncio.Future[bytes]"] = {}
        #: Bumped by every cancel. The consumer drops audio synthesized under
        #: an older generation instead of playing it after the cancel.
        self._generation: int = 0
        #: Synthesis of the line inside the consumer, cancelled by a cancel.
        self._synthesis_task: Optional["asyncio.Task[bytes]"] = None
        #: Monotonic instant until which an urgent line is playing; an urgent
        #: line arriving before it waits instead of cutting it.
        self._playing_urgent_until: float = 0.0
        self._warm_cache_task: Optional[asyncio.Task] = None
        self._cache = PhraseCache()

        _, types_module = _load_genai()
        if types_module is not None:
            self.session_config = types_module.LiveConnectConfig(
                response_modalities=[types_module.Modality.AUDIO],
                speech_config=types_module.SpeechConfig(
                    voice_config=types_module.VoiceConfig(
                        prebuilt_voice_config=types_module.PrebuiltVoiceConfig(voice_name=SYNTHESIZER_VOICE)
                    ),
                    language_code=SYNTHESIZER_LANGUAGE,
                ),
            )
        else:
            self.session_config = None

        self._thread = threading.Thread(target=self._run_event_loop, name="TelemetryAnnouncerLoop", daemon=True)
        self._thread.start()

        while self._loop is None or self._queue is None:
            time.sleep(0.01)

    def _run_event_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.PriorityQueue()
        self._loop.create_task(self._consumer_worker())
        self._loop.create_task(self._keep_warm())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _keep_warm(self) -> None:
        """Keep a fresh Live session on hand while the copilot is idle."""
        while self._active:
            await asyncio.sleep(_WARM_REFRESH_SEC)
            await self._ensure_warm_session()

    async def _ensure_warm_session(self) -> None:
        if not self._active or self.session_config is None:
            return
        if self._warm_session is not None:
            if (time.time() - self._warm_session_time) > _WARM_MAX_AGE_SEC:
                try:
                    await self._warm_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                self._warm_session = None
                self._warm_ctx = None
            else:
                return

        if self._is_warming:
            return

        self._is_warming = True
        try:
            token = self.auth_token or _resolve_auth_token()
            if not token:
                return
            # Off the loop thread: a probe blocks for up to its connect timeout
            # and a cached line may be playing from this loop meanwhile.
            if not await asyncio.to_thread(synthesis_reachable):
                return
            client = _get_synthesis_client(token)
            ctx = client.aio.live.connect(model=SYNTHESIZER_MODEL, config=self.session_config)
            session = await asyncio.wait_for(ctx.__aenter__(), timeout=3.0)
            self._warm_ctx = ctx
            self._warm_session = session
            self._warm_session_time = time.time()
            if self._warm_failed:
                logger.warning("Speech session warm-up recovered.")
            self._warm_failed = False
        except Exception as exc:  # noqa: BLE001 - reported, then retried on the next refresh
            if not self._warm_failed:
                # A warning, not debug: this is the copilot going quiet, and the
                # station forwards the daemon's stderr as "[copiloto]" lines.
                logger.warning("Speech session warm-up failed, lines will open cold: %s", exc)
            self._warm_failed = True
            self._warm_session = None
            self._warm_ctx = None
        finally:
            self._is_warming = False

    async def _open_session(self) -> Tuple[Optional[Any], Optional[Any]]:
        """Take the warm session, or open one. Returns ``(session, ctx)`` or ``(None, None)``."""
        session, ctx = self._warm_session, self._warm_ctx
        age = time.time() - self._warm_session_time
        self._warm_session = None
        self._warm_ctx = None
        asyncio.create_task(self._ensure_warm_session())
        if session is not None and age <= _WARM_MAX_AGE_SEC + _WARM_REFRESH_SEC:
            return session, ctx
        if session is not None and ctx is not None:
            # Past its refresh window: the server may already have closed it,
            # and a dead session costs a failed turn and a retry.
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

        token = self.auth_token or _resolve_auth_token()
        if not token:
            return None, None
        try:
            client = _get_synthesis_client(token)
            ctx = client.aio.live.connect(model=SYNTHESIZER_MODEL, config=self.session_config)
            session = await asyncio.wait_for(ctx.__aenter__(), timeout=3.0)
        except Exception as exc:  # noqa: BLE001 - reported as an unspoken line
            logger.debug("Speech synthesis session failure: %s", exc)
            return None, None
        return session, ctx

    async def _synthesize_once(
        self,
        statement: str,
        verbatim: bool,
        on_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None,
    ) -> Tuple[bytes, str]:
        """One Live turn for ``statement``. Returns the PCM and why the read ended.

        ``on_chunk`` receives each audio chunk as it arrives, so playback can
        start on the first one instead of after the whole turn. Without a route
        to the endpoint it returns ``(b"", "offline")`` without opening a
        session.
        """
        if not await asyncio.to_thread(synthesis_reachable):
            return b"", "offline"
        session, ctx = await self._open_session()
        if session is None:
            return b"", "no_session"

        audio = bytearray()
        reason = "error"
        try:
            _, types_module = _load_genai()
            await session.send_client_content(
                turns=types_module.Content(
                    role="user",
                    parts=[types_module.Part(text=synthesis_instruction(statement, verbatim))],
                ),
                turn_complete=True,
            )
            stream = session.receive()
            while True:
                timeout = _CHUNK_GAP_TIMEOUT_SEC if len(audio) >= _MIN_AUDIO_BYTES else _FIRST_AUDIO_TIMEOUT_SEC
                try:
                    response = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
                except asyncio.TimeoutError:
                    reason = "timeout"
                    break
                except StopAsyncIteration:
                    reason = "closed"
                    break
                if response.go_away:
                    reason = "go_away"
                    break
                sc = response.server_content
                if not sc:
                    continue
                if sc.model_turn and sc.model_turn.parts:
                    for part in sc.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            if "audio" in (part.inline_data.mime_type or "audio/pcm"):
                                audio.extend(part.inline_data.data)
                                if on_chunk is not None:
                                    await on_chunk(bytes(part.inline_data.data))
                if sc.turn_complete or sc.generation_complete:
                    reason = "complete"
                    break
        except Exception as exc:  # noqa: BLE001 - reported as an unspoken line
            logger.debug("Speech streaming exception: %s", exc)
        finally:
            if ctx is not None:
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
        return bytes(audio), reason

    async def _synthesize(
        self,
        statement: str,
        verbatim: bool,
        on_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None,
    ) -> bytes:
        """Synthesize ``statement``, retrying once on a fresh session if it produced nothing.

        A Live session occasionally stalls before its first chunk; waiting it
        out cost a silent line after 12 s. One retry on a new session answers
        in the usual two seconds when it does.
        """
        audio, reason = await self._synthesize_once(statement, verbatim, on_chunk)
        if len(audio) < _MIN_AUDIO_BYTES and reason not in ("no_session", "offline"):
            logger.debug("Synthesis produced no audio (%s); retrying on a fresh session.", reason)
            audio, reason = await self._synthesize_once(statement, verbatim, on_chunk)
        logger.debug("Synthesis ended: %s, %d bytes.", reason, len(audio))
        return audio

    def synthesize_blocking(self, statement: str, verbatim: bool = True, timeout: float = 30.0) -> bytes:
        """Synthesize ``statement`` on the announcer's loop and wait for the PCM.

        Used by the pre-cache tool from a plain thread. The turn runs on this
        announcer's own event loop, so it shares the warm Live session and the
        retry of :meth:`_synthesize` with the narration instead of opening a
        second client. Nothing is played and nothing is cached here.

        Parameters
        ----------
        statement : str
            Sentence to synthesize.
        verbatim : bool
            Prompt mode, as in :func:`synthesis_instruction`.
        timeout : float
            Longest wait for the turn, seconds; must be positive. On expiry the
            turn is cancelled.

        Returns
        -------
        bytes
            The PCM of the turn, 24 kHz mono int16. Empty when the endpoint is
            unreachable or the session produced no audio.

        Raises
        ------
        TypeError
            If ``statement`` is not a string or ``timeout`` is not a real number.
        ValueError
            If ``statement`` is blank or ``timeout`` is not positive.
        RuntimeError
            If no synthesis session can be opened: ``google.genai`` missing, no
            API key, or the announcer loop stopped; or if called on the
            announcer's own loop thread, where waiting would block the loop
            that has to run the turn.
        TimeoutError
            If the turn did not finish within ``timeout``.
        """
        if not isinstance(statement, str):
            raise TypeError(f"statement must be str, got {type(statement).__name__}")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError(f"timeout must be a real number, got {type(timeout).__name__}")
        if timeout <= 0.0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        loop = self._loop
        if self.session_config is None or loop is None or loop.is_closed() or not self._active:
            raise RuntimeError("no speech synthesis session: google-genai missing or announcer closed")
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise RuntimeError("synthesize_blocking called on the announcer loop thread; the turn could never run")
        if not (self.auth_token or _resolve_auth_token()):
            raise RuntimeError("no speech synthesis session: no API key (SPEECH_API_KEY / GEMINI_API_KEY)")
        text = statement.strip()
        if not text:
            raise ValueError("statement must not be empty")

        future = asyncio.run_coroutine_threadsafe(self._synthesize(text, bool(verbatim)), loop)
        try:
            return future.result(timeout=float(timeout))
        except BaseException:
            future.cancel()
            raise

    def prefetch(self, text: str) -> bool:
        """Synthesize a verbatim line ahead of its request.

        The station reads one line at a time and asks for the next only once
        the current one has been heard, so every line used to open with its
        own synthesis latency as dead air. Asking for the next line here while
        the current one plays lets its request find the audio ready.

        Parameters
        ----------
        text : str
            The exact sentence the next ``announce(..., verbatim=True)`` will carry.

        Returns
        -------
        bool
            False when synthesis is unavailable and nothing was scheduled.

        Raises
        ------
        TypeError
            If ``text`` is not a string.
        """
        if not isinstance(text, str):
            raise TypeError(f"text must be str, got {type(text).__name__}")
        statement = text.strip()
        if not statement or self._loop is None or self.session_config is None or not self._active:
            return False

        def start() -> None:
            if statement in self._prefetched or self._cache.has(statement, True):
                return
            while len(self._prefetched) >= _PREFETCH_LIMIT:
                oldest = next(iter(self._prefetched))
                self._prefetched.pop(oldest).cancel()
            self._prefetched[statement] = asyncio.ensure_future(self._synthesize(statement, True))

        self._loop.call_soon_threadsafe(start)
        return True

    def warm_cache(self, texts: List[str]) -> int:
        """Synthesize, in the background, every verbatim sentence not yet cached.

        Runs one sentence at a time and only while nothing is queued or being
        synthesized for playback, so it never delays a line the operator is
        waiting for. Returns how many sentences were scheduled; none without a
        route to the synthesis endpoint (:func:`synthesis_reachable`).
        """
        pending: List[str] = []
        for text in texts:
            statement = str(text or "").strip()
            if statement and statement not in pending and not self._cache.has(statement, True):
                pending.append(statement)
        if not pending or self._loop is None or self.session_config is None or not self._active:
            return 0
        if not synthesis_reachable():
            return 0

        async def fill() -> None:
            try:
                for statement in pending:
                    while self._active and (not self._queue.empty() or self._synthesis_task is not None):
                        await asyncio.sleep(0.1)
                    if not self._active:
                        return
                    if self._cache.has(statement, True):
                        continue
                    try:
                        audio = await self._synthesize(statement, True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - the cache is an optimisation
                        logger.debug("Cache warm-up failed for '%s': %s", statement[:40], exc)
                        continue
                    self._cache.put(statement, True, audio)
            except asyncio.CancelledError:
                logger.debug("Cache warm-up task cancelled")
                return
            finally:
                if getattr(self, "_warm_cache_task", None) is asyncio.current_task():
                    self._warm_cache_task = None

        def start_fill() -> None:
            if getattr(self, "_warm_cache_task", None) and not self._warm_cache_task.done():
                self._warm_cache_task.cancel()
            self._warm_cache_task = self._loop.create_task(fill())

        self._loop.call_soon_threadsafe(start_fill)
        return len(pending)

    def _drop_prefetched(self) -> None:
        for future in self._prefetched.values():
            future.cancel()
        self._prefetched.clear()

    async def _consumer_worker(self) -> None:
        await self._ensure_warm_session()

        while self._active:
            try:
                item = await self._queue.get()
            except Exception:
                break

            pri_int, seq, action, details, verbatim, done_fut, enqueued_at = item
            generation = self._generation

            # Preemption:
            # - LAND priority (pri_int < 0) cuts ANY speech currently playing
            # - URGENT priority (pri_int == 0) cuts narration, but lets another alert finish
            if pri_int < 0:
                self.device.stop_current()
                self._playing_urgent_until = 0.0
                kept = []
                while not self._queue.empty():
                    try:
                        q_item = self._queue.get_nowait()
                        self._queue.task_done()
                    except (asyncio.QueueEmpty, ValueError):
                        break
                    if q_item[0] <= 0:
                        kept.append(q_item)
                    elif q_item[5] and not q_item[5].done():
                        q_item[5].set_result(False)
                for q_item in kept:
                    self._queue.put_nowait(q_item)
            elif pri_int == 0:
                if time.monotonic() >= self._playing_urgent_until:
                    self.device.stop_current()
                kept = []
                while not self._queue.empty():
                    try:
                        q_item = self._queue.get_nowait()
                        self._queue.task_done()
                    except (asyncio.QueueEmpty, ValueError):
                        break
                    if q_item[0] <= 0:
                        kept.append(q_item)
                    elif q_item[5] and not q_item[5].done():
                        q_item[5].set_result(False)
                for q_item in kept:
                    self._queue.put_nowait(q_item)

            statement = action if verbatim else _format_telemetry_statement(action, details)
            # The statement is final here: the phrase mapper already composed
            # it, and it is what the log line below records. Synthesizing it
            # verbatim makes the spoken sentence the logged one; the free
            # prompt let the model reword it.
            verbatim = True
            logger.info("Acoustic announcement: '%s' (Priority: %s)", statement, "URGENT" if pri_int == 0 else "NORMAL")

            prefetched = self._prefetched.pop(statement, None) if verbatim else None
            audio: bytes = self._cache.get(statement, verbatim) or b""
            source = "cache"
            if len(audio) < _MIN_AUDIO_BYTES and prefetched is not None:
                source = "prefetch"
                try:
                    audio = await prefetched
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    audio = b""

            if len(audio) < _MIN_AUDIO_BYTES and not await asyncio.to_thread(synthesis_reachable):
                # Offline a cache miss has no voice to fall back on. Resolving
                # it now lets the station move on at its own cadence instead of
                # waiting out two session timeouts and the 14 s speech ceiling.
                logger.warning("[SPEECH_MISS] '%s'", statement[:48])
                self._queue.task_done()
                if done_fut and not done_fut.done():
                    done_fut.set_result(False)
                continue

            streamed = False

            def log_start(started_source: str) -> None:
                # Request to audio start, on the monotonic clock. The
                # station's latency targets (cold, warm, urgent after cancel)
                # are read from these lines.
                logger.info(
                    "[SPEECH] '%s' latency_ms=%d source=%s priority=%s",
                    statement[:48],
                    int((time.monotonic() - enqueued_at) * 1000.0),
                    started_source,
                    "URGENT" if pri_int == 0 else "NORMAL",
                )

            async def feed(chunk: bytes) -> None:
                nonlocal streamed
                if generation != self._generation or not chunk:
                    return
                if not streamed:
                    streamed = True
                    if pri_int != 0:
                        await asyncio.to_thread(self.device.wait_until_done, 20.0)
                        if generation != self._generation:
                            return
                    log_start("stream")
                self.device.play_audio(chunk)

            if len(audio) < _MIN_AUDIO_BYTES:
                source = "synth"
                task = asyncio.ensure_future(self._synthesize(statement, verbatim, on_chunk=feed))
                self._synthesis_task = task
                try:
                    audio = await task
                except asyncio.CancelledError:
                    audio = b""
                finally:
                    self._synthesis_task = None
            audio_buffer = audio

            if generation != self._generation:
                # Cancelled while it was being synthesized: the operator asked
                # for silence, and this line arriving afterwards was exactly
                # the defect (a cancelled line played in full, 12 s later).
                logger.info("Dropping cancelled line: '%s'", statement)
                self._queue.task_done()
                if done_fut and not done_fut.done():
                    done_fut.set_result(False)
                continue

            # Whether this line was actually spoken, as opposed to having been
            # processed. Reaching the end of this block proves nothing: the
            # streaming above is wrapped in a `try` that swallows every failure,
            # so a missing API key, a dead network or an absent `google.genai`
            # all arrive here having produced nothing at all. Reporting those as
            # success told the ground station the operator had heard a sentence
            # that was never uttered, and its forensic report -- which reveals a
            # card per line read -- then flashed all four at once.
            spoke = len(audio_buffer) >= _MIN_AUDIO_BYTES and getattr(self.device, "_active", True)
            if spoke and source != "cache":
                self._cache.put(statement, verbatim, audio_buffer)
            if spoke and not streamed:
                if pri_int != 0:
                    # Bounded. This runs on the announcer's event loop thread,
                    # so a playback worker wedged inside sounddevice would
                    # otherwise stall every later line and the warm-session
                    # task with it -- the whole copilot, on one stuck buffer.
                    self.device.wait_until_done(timeout=20.0)
                self.device.play_audio(bytes(audio_buffer))
                log_start(source)
            if spoke:
                rate = float(getattr(self.device, "sample_rate", 24000) or 24000)
                audio_sec = len(audio_buffer) / (2.0 * rate)
                if pri_int <= 0:
                    self._playing_urgent_until = time.monotonic() + audio_sec
                # Paired with the `[SPEECH]` start line: start plus audio_ms is
                # when the line stops sounding (scripts/bench_rehearsal.py).
                logger.info(
                    "[SPEECH_DONE] '%s' audio_ms=%d priority=%s",
                    statement[:48],
                    int(audio_sec * 1000.0),
                    "LAND" if pri_int < 0 else "URGENT" if pri_int == 0 else "NORMAL",
                )

            self._queue.task_done()
            if done_fut and not done_fut.done():
                done_fut.set_result(spoke)

    def announce(
        self,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        priority: str = "NORMAL",
        wait: bool = False,
        timeout: Optional[float] = None,
        verbatim: bool = False,
    ) -> bool:
        """Enqueue flight telemetry announcement asynchronously without blocking.

        ``verbatim`` suppresses the phrase mapper in
        :func:`_format_telemetry_statement` and speaks ``action`` as written.
        The ground station uses it for the forensic report, where the sentence
        on screen and the sentence in the operator's ear must be identical.
        """
        if not self._active or self._loop is None or self._queue is None:
            return False

        if str(priority).upper() == "LAND" or "abort" in action.lower():
            pri_int = -1
        elif _is_urgent(action, details, priority):
            pri_int = 0
        else:
            pri_int = 1

        if pri_int <= 0 and getattr(self, "_warm_cache_task", None) and not self._warm_cache_task.done():
            self._loop.call_soon_threadsafe(self._warm_cache_task.cancel)

        with self._lock:
            self._seq += 1
            seq = self._seq

        done_event = threading.Event() if wait else None
        done_fut: Optional[asyncio.Future] = None

        if wait:
            done_fut = self._loop.create_future()
            done_fut.add_done_callback(lambda _: done_event.set())

        self._loop.call_soon_threadsafe(
            self._queue.put_nowait,
            (pri_int, seq, action, details, verbatim, done_fut, time.monotonic()),
        )

        if wait and done_event:
            # One deadline for synthesis and playback together, so a caller's
            # ceiling (the station's SPEECH_CEILING_SEC) is the ceiling.
            deadline = time.monotonic() + (timeout or 14.0)
            settled = done_event.wait(timeout=max(0.0, deadline - time.monotonic()))
            if not settled:
                return False
            self.device.wait_until_done(timeout=max(0.0, deadline - time.monotonic()))
            # The future carries the verdict; the event only says one arrived.
            try:
                return bool(done_fut.result()) if done_fut else True
            except Exception:  # noqa: BLE001
                return False

        return True

    def cancel_pending(self) -> int:
        """Drop every queued line and silence what is playing.

        Stopping the playback device alone leaves the announcer's own priority
        queue loaded, so a cancelled report went quiet and then resumed reading
        itself over whatever came next. Callers waiting on a dropped line are
        resolved false rather than left to time out.

        Returns the number of lines dropped. The line already inside the
        consumer is not one of them -- it is mid-synthesis and cannot be taken
        back -- but its audio is cut by ``stop_current`` below.
        """
        dropped = 0
        if self._loop is None or self._queue is None:
            return dropped

        done = threading.Event()

        def drain() -> None:
            nonlocal dropped
            self._generation += 1
            if self._synthesis_task is not None and not self._synthesis_task.done():
                self._synthesis_task.cancel()
            while True:
                try:
                    item = self._queue.get_nowait()
                except (asyncio.QueueEmpty, ValueError):
                    break
                self._queue.task_done()
                dropped += 1
                future = item[5]
                if future is not None and not future.done():
                    future.set_result(False)
            self._drop_prefetched()
            done.set()

        self._loop.call_soon_threadsafe(drain)
        done.wait(timeout=2.0)
        self.device.stop_current()
        return dropped

    def close(self) -> None:
        """Terminate announcer and clean up worker threads."""
        self._active = False
        if self._warm_ctx and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._warm_ctx.__aexit__(None, None, None), self._loop)
        if self.device:
            self.device.close()


_global_announcer: Optional[MissionAudioAnnouncer] = None
_announcer_lock = threading.Lock()


#: Priorities the playback queue serves first, and that the ground station
#: receives as alerts rather than as nothing.
_URGENT_PRIORITIES: Final = ("URGENT", "CRITICAL", "EMERGENCY", "LAND")


def _is_urgent(action: str, details: Optional[Dict[str, Any]], priority: str) -> bool:
    """Whether an announcement is a failure or alert rather than narration."""
    return (
        priority.upper() in _URGENT_PRIORITIES
        or bool(details and "erro" in details)
        or "erro" in action.lower()
        or "falha" in action.lower()
    )


#: Set to ``1`` by ``electron/main.cjs`` in the mission process's environment,
#: and only there -- not in the speech daemon it also spawns.
GCS_SESSION_ENV: Final[str] = "BMG_GCS_SESSION"

#: Alert key for each urgent action a call site passes. Anything urgent not
#: listed goes out as ``mission.alert``.
_ALERT_KEY_BY_ACTION: Final[Dict[str, str]] = {
    "missão abortada": "mission.abort",
    "falha na etapa": "mission.step_failed",
    "falha de segurança": "mission.failsafe",
    "falha na inicialização": "mission.init_failed",
    "falha na calibração": "mission.calibration_failed",
    "falha na decolagem": "mission.takeoff_failed",
}


#: File the station's copilot daemon (``--serve``) holds while it runs.
PLAYER_LOCK_NAME: Final[str] = "bmg-announcer.lock"


def _player_lock_path() -> pathlib.Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return pathlib.Path(runtime) / PLAYER_LOCK_NAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    cmdline_path = pathlib.Path(f"/proc/{pid}/cmdline")
    if cmdline_path.exists():
        try:
            cmd = cmdline_path.read_text(encoding="utf-8", errors="replace").lower()
            if "python" not in cmd:
                return False
        except (OSError, PermissionError):
            pass
    return True


def acquire_player_lock() -> Optional[pathlib.Path]:
    """Record this process as the station's one audio player. Returns the lock path, or None."""
    path = _player_lock_path()
    try:
        path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        logger.debug("Player lock not written (%s): %s", path, exc)
        return None
    return path


def release_player_lock(path: Optional[pathlib.Path]) -> None:
    """Remove the lock if it is still this process's."""
    if path is None:
        return
    try:
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def _another_player_holds_the_lock() -> bool:
    try:
        pid = int(_player_lock_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return pid != os.getpid() and _pid_alive(pid)


def station_narrates() -> bool:
    """Whether the ground station is this mission's only voice.

    Under the station the system speaks through one player, the station's
    copilot, fed by one ordered queue. A second announcer in the mission
    process talked over it and repeated its milestones through a second
    synthesis session and a second hold on the audio device. So here no
    player is ever built: flight narration is left to the milestones the
    station already receives, and failures reach it as alerts.

    True when the station launched this process (``BMG_GCS_SESSION=1``), or
    when a live copilot daemon holds the player lock: a ``mission.py`` started
    from a terminal the station did not configure must not open a second voice
    either.
    """
    if os.environ.get(GCS_SESSION_ENV, "").strip() == "1":
        return True
    return _another_player_holds_the_lock()


def _hand_to_station(
    action: str, details: Optional[Dict[str, Any]], priority: str, verbatim: bool
) -> bool:
    """Route one announcement to the station instead of speaking it.

    Returns True when it went out as an alert, False when it was narration
    the station's milestones already cover and was dropped.
    """
    if not _is_urgent(action, details, priority):
        logger.debug("Narration left to the ground station: %s", action)
        return False

    from mvp_mission_bebop.telemetry.milestones import emit_alert

    key = _ALERT_KEY_BY_ACTION.get(action.strip().lower(), "mission.alert")
    level = priority.upper() if priority.upper() in _URGENT_PRIORITIES else "URGENT"
    text = action if verbatim else _format_telemetry_statement(action, details)
    return emit_alert(key, {"text": text, "priority": level})


def get_announcer() -> MissionAudioAnnouncer:
    """Retrieve or initialize the global mission audio announcer."""
    global _global_announcer
    with _announcer_lock:
        if _global_announcer is None:
            _global_announcer = MissionAudioAnnouncer()
        return _global_announcer


#: Shortest speech accepted per word of a pre-cached line. Lines measured
#: 0.51 s per word with the pace of ``_ACCENT_DIRECTIVE``; a turn cut by a
#: dropped session or a server ``go_away`` returns a fraction of that and would
#: otherwise be played at the fair as half a sentence.
_MIN_SPEECH_SEC_PER_WORD: Final[float] = 0.25
#: First wait between attempts of one pre-cached line, doubled per retry.
_PRECACHE_BACKOFF_SEC: Final[float] = 2.0
#: Longest wait for one pre-cached turn: two Live attempts of
#: ``_FIRST_AUDIO_TIMEOUT_SEC`` plus a 20-word line streamed at speech pace.
_PRECACHE_TURN_TIMEOUT_SEC: Final[float] = 30.0


@dataclass(frozen=True)
class PrecacheReport:
    """Outcome of :func:`precache_phrases`.

    Attributes
    ----------
    synthesized : int
        Statements synthesized and stored in this run.
    skipped : int
        Statements already in the cache, not synthesized again.
    failed : Tuple[str, ...]
        Statements with no accepted audio after every attempt, in order.
    """

    synthesized: int
    skipped: int
    failed: Tuple[str, ...]


def _minimum_speech_bytes(statement: str) -> int:
    """Least PCM accepted as a complete reading of ``statement``.

    Parameters
    ----------
    statement : str
        Sentence the audio should read.

    Returns
    -------
    int
        ``_MIN_SPEECH_SEC_PER_WORD`` per word of 24 kHz mono int16, and never
        less than ``_MIN_AUDIO_BYTES``.

    Raises
    ------
    TypeError
        If ``statement`` is not a string.
    """
    if not isinstance(statement, str):
        raise TypeError(f"statement must be str, got {type(statement).__name__}")
    bytes_per_sec = PCM_SAMPLE_RATE * PCM_CHANNELS * 2
    return max(_MIN_AUDIO_BYTES, math.ceil(len(statement.split()) * _MIN_SPEECH_SEC_PER_WORD * bytes_per_sec))


def _entry_is_complete(cache: PhraseCache, statement: str) -> bool:
    """Whether ``statement`` has a verbatim entry at least :func:`_minimum_speech_bytes` long.

    ``PhraseCache.has`` accepts any entry of ``_MIN_AUDIO_BYTES``, and the
    daemon's ``warm_cache`` stores whatever a turn returned, so a cut turn can
    sit in the cache as a fraction of its sentence. The pre-cache treats such
    an entry as missing and overwrites it.
    """
    data = cache.get(statement, True)
    return data is not None and len(data) >= _minimum_speech_bytes(statement)


def _discard_partial(cache: PhraseCache, statement: str) -> None:
    """Remove this process's ``.part`` of ``statement`` left by an interrupted :meth:`PhraseCache.put`."""
    partial = cache.path_for(statement, True).with_suffix(f".{os.getpid()}.part")
    try:
        partial.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Partial cache entry not removed (%s): %s", partial, exc)


def precache_phrases(
    statements: Sequence[str],
    *,
    cache: Optional[PhraseCache] = None,
    attempts: int = 3,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> PrecacheReport:
    """Synthesize into the phrase cache every verbatim statement not yet there.

    Each statement is synthesized by the global announcer
    (:meth:`MissionAudioAnnouncer.synthesize_blocking`, voice
    ``SYNTHESIZER_VOICE``, model ``SYNTHESIZER_MODEL``) and stored with
    :meth:`PhraseCache.put`, which writes a ``.part`` file and renames it, so an
    entry is either complete or absent. A run interrupted by a dropped network
    or Ctrl-C leaves only complete entries, its own ``.part`` removed, and the
    next run synthesizes only what is still missing. An existing entry shorter
    than :func:`_minimum_speech_bytes` counts as missing and is overwritten.

    Parameters
    ----------
    statements : Sequence[str]
        Sentences the station speaks verbatim. Stripped and deduplicated;
        blank entries are ignored.
    cache : Optional[PhraseCache]
        Target cache; a default :class:`PhraseCache` when None.
    attempts : int
        Synthesis attempts per statement, at least 1. Retries wait
        ``_PRECACHE_BACKOFF_SEC``, doubled each time; with the endpoint
        unreachable the statement fails without further retries.
    on_progress : Optional[Callable[[int, int, str], None]]
        Called after each statement that needed synthesis with
        ``(index, pending, statement)``, ``index`` from 1.

    Returns
    -------
    PrecacheReport
        Synthesized, skipped and failed statements. Audio shorter than
        :func:`_minimum_speech_bytes` (a truncated turn) is never stored.

    Raises
    ------
    TypeError
        If ``statements`` is a bare string or holds a non-string, ``cache`` is
        not a :class:`PhraseCache`, ``attempts`` is not an integer or
        ``on_progress`` is not callable.
    ValueError
        If ``attempts`` is below 1, or statements are missing from the cache
        and no API key resolves (:func:`_resolve_auth_token`).
    RuntimeError
        If the announcer cannot open a synthesis session at all.
    """
    if isinstance(statements, (str, bytes)) or not isinstance(statements, Sequence):
        raise TypeError("statements must be a sequence of str, not a single string")
    if cache is not None and not isinstance(cache, PhraseCache):
        raise TypeError(f"cache must be a PhraseCache, got {type(cache).__name__}")
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise TypeError(f"attempts must be int, got {type(attempts).__name__}")
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    if on_progress is not None and not callable(on_progress):
        raise TypeError("on_progress must be callable")

    store = cache if cache is not None else PhraseCache()
    absent = set(store.missing(statements, verbatim=True))
    pending: List[str] = []
    seen: set[str] = set()
    for item in statements:
        statement = item.strip()
        if not statement or statement in seen:
            continue
        seen.add(statement)
        if statement in absent or not _entry_is_complete(store, statement):
            pending.append(statement)
    distinct = len(seen)
    if not pending:
        return PrecacheReport(synthesized=0, skipped=distinct, failed=())
    if not _resolve_auth_token():
        raise ValueError("no API key for speech synthesis (SPEECH_API_KEY / GEMINI_API_KEY)")

    speaker = get_announcer()
    synthesized = 0
    failed: List[str] = []
    for index, statement in enumerate(pending, start=1):
        floor = _minimum_speech_bytes(statement)
        accepted: Optional[bytes] = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(_PRECACHE_BACKOFF_SEC * (2 ** (attempt - 1)))
            try:
                audio = speaker.synthesize_blocking(statement, True, timeout=_PRECACHE_TURN_TIMEOUT_SEC)
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001 - one failed turn, retried
                logger.warning("Synthesis attempt %d/%d failed for '%s': %s", attempt + 1, attempts, statement[:60], exc)
                audio = b""
            if len(audio) >= floor:
                accepted = audio
                break
            if audio:
                logger.warning(
                    "Truncated synthesis rejected for '%s': %d bytes, at least %d expected.",
                    statement[:60],
                    len(audio),
                    floor,
                )
            if not synthesis_reachable():
                break
        if accepted is None:
            failed.append(statement)
        else:
            try:
                store.put(statement, True, accepted)
            except BaseException:
                _discard_partial(store, statement)
                raise
            if _entry_is_complete(store, statement):
                synthesized += 1
            else:
                failed.append(statement)
        if on_progress is not None:
            on_progress(index, len(pending), statement)

    return PrecacheReport(synthesized=synthesized, skipped=distinct - len(pending), failed=tuple(failed))


_alerts_raised: int = 0


def alerts_raised() -> int:
    """How many urgent announcements this process has raised so far.

    The runner compares it across a step to tell whether the step already
    alerted about its own failure, so the generic ``step_failed`` alert is not
    spoken on top of the specific one.
    """
    return _alerts_raised


def announce(
    action: str,
    details: Optional[Dict[str, Any]] = None,
    priority: str = "NORMAL",
    wait: bool = False,
    timeout: Optional[float] = None,
    verbatim: bool = False,
) -> bool:
    """Emit telemetry notification.

    Under the ground station (:func:`station_narrates`) nothing is played in
    this process: urgent announcements become alerts on stdout, the rest is
    dropped. Standalone, it plays through the local announcer as before.
    """
    global _alerts_raised
    if _is_urgent(action, details, priority):
        _alerts_raised += 1
    if station_narrates():
        return _hand_to_station(action, details, priority, verbatim)
    try:
        return get_announcer().announce(
            action,
            details,
            priority=priority,
            wait=wait,
            timeout=timeout,
            verbatim=verbatim,
        )
    except Exception as exc:
        logger.debug("Announce dispatch exception: %s", exc)
        return False


def announce_sync(
    action: str,
    details: Optional[Dict[str, Any]] = None,
    wait: bool = False,
    priority: str = "NORMAL",
    verbatim: bool = False,
) -> bool:
    """Synchronous interface for mission step hooks."""
    return announce(action, details=details, priority=priority, wait=wait, verbatim=verbatim)


def speak(
    text: str,
    priority: str = "NORMAL",
    wait: bool = False,
    timeout: Optional[float] = None,
) -> bool:
    """Say one line exactly as written.

    The counterpart to :func:`announce`, which maps a mission milestone onto a
    fixed aeronautical phrase. Here the caller already holds the sentence — the
    ground station's forensic report, for one, where the same string is on
    screen — so nothing rewrites it.
    """
    return announce(text, priority=priority, wait=wait, timeout=timeout, verbatim=True)


# -----------------------------------------------------------------------------
# Forensic report
#
# Four findings, one per topic the operator is required to clear before a claim
# can be closed remotely. The tables, the order rules and the cross-flight memory
# live in `telemetry/inspection_report.py`; the victim line follows the victim
# assessment of the inspection frame. The ground station holds the same tables in
# `src/lib/forensics.ts` and is the one that decides the report for a given
# flight; this side is what the mission uses when it narrates on its own.
# -----------------------------------------------------------------------------

FORENSIC_INTRO = "Inspeção da ocorrência concluída. Apresentando laudo preliminar."
FORENSIC_OUTRO = "Relatório pericial emitido e pronto para exportação."


def build_forensic_report(
    seed: Optional[int] = None,
    assessment: Optional[Any] = None,
    history_path: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Draw one report: a wording per topic, in the order rules of the engine.

    Returns one dict per finding with ``topic``, ``card`` (the line the station
    displays) and ``speech`` (the same line as the copilot reads it, led by an
    opener, a linker or a closer; the two linkers of a report differ). Without
    ``assessment`` the victim line is the neutral one (``unknown``); without
    ``history_path`` the report is drawn without cross-flight memory.
    """
    return build_inspection_report(
        victim_status_of(assessment),
        history_path=history_path,
        rng=random.Random(seed),
    )


def announce_forensic_report(
    report: Optional[List[Dict[str, str]]] = None,
    assessment: Optional[Any] = None,
    history_path: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Read a whole report aloud, one finding at a time.

    Blocks for the length of the narration, so a caller that also paints the
    screen stays in step with it. The ground station does not use this path --
    it drives the cadence itself over the daemon below, so that each card lands
    on the sentence that describes it -- but the mission process can narrate a
    report with one call.
    """
    findings = (
        report
        if report is not None
        else build_forensic_report(assessment=assessment, history_path=history_path)
    )
    lines = [FORENSIC_INTRO, *(finding["speech"] for finding in findings), FORENSIC_OUTRO]
    # No pause between lines: the next two are synthesized while one plays, as
    # the station does, so the report runs on without dead air.
    narrating_locally = not station_narrates()
    for index, line in enumerate(lines):
        if narrating_locally:
            for ahead in lines[index + 1 : index + 3]:
                get_announcer().prefetch(ahead)
        speak(line, wait=True, timeout=25.0)
    return findings


# -----------------------------------------------------------------------------
# Line daemon
#
# One long-lived process the ground station writes requests to, instead of a
# fresh interpreter per sentence. Starting Python, importing `google.genai` and
# opening a Live session costs seconds; the post-landing report wants a card
# every two, so the session has to already be warm when the line arrives.
#
# Requests are JSON objects, one per line, on stdin:
#
#   {"id": 7, "text": "...", "priority": "NORMAL", "verbatim": true}
#   {"op": "cancel"}   -- drop what is queued and silence the current line
#   {"op": "quit"}
#
# Replies are one line each on stdout, prefixed so they survive anything the
# libraries below decide to print:
#
#   BMG_SPEECH:{"id": 7, "event": "done", "ok": true}
# -----------------------------------------------------------------------------

SPEECH_EVENT_PREFIX = "BMG_SPEECH:"


def _emit_speech_event(payload: Dict[str, Any]) -> None:
    sys.stdout.write(SPEECH_EVENT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve_stdin() -> int:
    """Run the announcer as a line-oriented daemon. Returns a process exit code.

    Each request is spoken to completion before the next is taken, which is what
    lets the caller treat ``done`` as "the operator has heard this" and only
    then reveal the matching card.
    """
    announcer = get_announcer()
    lock = acquire_player_lock()
    # Said once, at startup, so a station whose copilot cannot speak learns it
    # from the handshake rather than from four silent requests. `voice_ready`
    # is what the station gates on; `synthesis` keeps its former meaning for a
    # station that predates it.
    genai_available = _load_genai()[0] is not None
    playback = bool(announcer.device._active)
    cache_entries = announcer._cache.entries()
    voice_ready, detail = voice_readiness(
        genai_available=genai_available,
        token=bool(announcer.auth_token),
        playback=playback,
        cache_entries=cache_entries,
    )
    _emit_speech_event(
        {
            "event": "ready",
            "voice": SYNTHESIZER_VOICE,
            "model": SYNTHESIZER_MODEL,
            "synthesis": genai_available and bool(announcer.auth_token),
            "playback": playback,
            "voice_ready": voice_ready,
            "cache_entries": cache_entries,
            "offline": not synthesis_reachable(),
            **announcer.device.get_level(),
            "detail": detail,
        }
    )

    work: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()

    def worker() -> None:
        while True:
            request = work.get()
            if request is None:
                return
            request_id = request.get("id")
            text = str(request.get("text") or "").strip()
            if not text:
                _emit_speech_event({"id": request_id, "event": "done", "ok": False})
                continue
            if announcer.device.get_level()["muted"]:
                # Synthesising a line that will not be played spends an API call
                # and several seconds on nothing. The station paces itself off
                # its own beat when a line comes back unspoken, so reporting
                # this immediately is both cheaper and correct.
                _emit_speech_event({"id": request_id, "event": "done", "ok": False, "muted": True})
                continue
            try:
                ok = announcer.announce(
                    text,
                    details=None,
                    priority=str(request.get("priority") or "NORMAL"),
                    wait=True,
                    timeout=float(request.get("timeout") or 30.0),
                    verbatim=bool(request.get("verbatim", True)),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Daemon announce failure: %s", exc)
                ok = False
            _emit_speech_event({"id": request_id, "event": "done", "ok": bool(ok)})

    thread = threading.Thread(target=worker, name="SpeechDaemonWorker", daemon=True)
    thread.start()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                # A bare line is a sentence. Nothing upstream should send one,
                # but refusing to speak because of a missing brace would be the
                # worse failure.
                request = {"text": line}
            if not isinstance(request, dict):
                continue

            op = request.get("op")
            if op == "quit":
                break
            if op == "level":
                announcer.device.set_level(
                    volume=request.get("volume"),
                    muted=request.get("muted"),
                )
                _emit_speech_event({"event": "level", **announcer.device.get_level()})
                continue
            if op == "cache":
                # Every sentence this mission can speak, sent at launch: the
                # numbers are the mission's own parameters (D2) and the report
                # combinations are finite (D1), so all of it can be on disk
                # before the flight. Filled in the background, idle time only.
                # `missing` is what stays silent if the route never returns.
                texts = request.get("texts")
                if isinstance(texts, list) and not announcer.device.get_level()["muted"]:
                    statements = [t for t in texts if isinstance(t, str)]
                    scheduled = announcer.warm_cache(statements)
                    _emit_speech_event(
                        {
                            "event": "cache",
                            "scheduled": scheduled,
                            "requested": len(texts),
                            "missing": len(announcer._cache.missing(statements)),
                            "offline": not synthesis_reachable(),
                        }
                    )
                continue
            if op == "prepare":
                text = request.get("text")
                if isinstance(text, str) and text.strip() and not announcer.device.get_level()["muted"]:
                    announcer.prefetch(text)
                continue
            if op == "cancel":
                # Three layers hold work at this moment: this daemon's own
                # backlog, the announcer's priority queue behind it, and the
                # audio device. Draining only the first left the other two to
                # finish reading a report the operator had dismissed.
                while not work.empty():
                    try:
                        pending_request = work.get_nowait()
                        if pending_request:
                            _emit_speech_event(
                                {"id": pending_request.get("id"), "event": "done", "ok": False}
                            )
                    except queue.Empty:
                        break
                dropped = announcer.cancel_pending()
                _emit_speech_event({"event": "cancelled", "dropped": dropped})
                continue

            work.put(request)
    except KeyboardInterrupt:
        pass
    finally:
        work.put(None)
        thread.join(timeout=2.0)
        announcer.close()
        release_player_lock(lock)

    return 0


# Compatibility aliases
falar_acao = announce
falar_acao_sync = announce_sync
DEFAULT_MODEL = SYNTHESIZER_MODEL
DEFAULT_VOICE = SYNTHESIZER_VOICE
get_audio_speaker = get_audio_playback_device
get_speech_arbiter = get_announcer
_resolve_api_key = _resolve_auth_token


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acoustic mission telemetry announcer CLI")
    parser.add_argument("action", nargs="?", default="Missão abortada", help="Action or milestone to announce")
    parser.add_argument("--priority", choices=["NORMAL", "URGENT", "CRITICAL"], default="NORMAL", help="Queue priority")
    parser.add_argument("--details", default=None, help="Details or explanation")
    parser.add_argument("--wait", action="store_true", default=True, help="Wait for audio playback to complete")
    parser.add_argument(
        "--verbatim",
        action="store_true",
        help="Speak the text exactly as given, bypassing the milestone phrase mapper.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run as a line daemon reading JSON requests on stdin (used by the ground station).",
    )
    parser.add_argument(
        "--forensic-report",
        action="store_true",
        help="Narrate one randomised forensic report and print it as JSON.",
    )
    args = parser.parse_args()

    if args.serve:
        sys.exit(serve_stdin())

    if args.forensic_report:
        findings = announce_forensic_report(history_path=os.path.join(".", HISTORY_FILENAME))
        print(json.dumps(findings, ensure_ascii=False))
        get_announcer().close()
        sys.exit(0)

    details_dict = {"etapa": args.details} if args.details else None
    success = announce_sync(
        args.action,
        details=details_dict,
        priority=args.priority,
        wait=args.wait,
        verbatim=args.verbatim,
    )
    get_announcer().close()
    sys.exit(0 if success else 1)

