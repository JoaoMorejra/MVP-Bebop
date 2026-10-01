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
import os
import pathlib
import queue
import random
import sys
import tempfile
import threading
import time
from typing import Any, Awaitable, Callable, Dict, Final, List, Optional, Tuple

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
                    stream.write(data[offset:offset + block_bytes])
            except Exception as err:  # noqa: BLE001 - one bad buffer must not end playback
                logger.debug("Sounddevice playback error: %s", err)
                self._close_stream(stream, abort=True)
                stream = None
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


def _format_telemetry_statement(
    action: str,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Format structured telemetry action into concise aeronautical phrase.

    A fault states its real reason (``details["erro"]`` or ``details["etapa"]``)
    and claims a landing only when ``details["em_voo"]`` is true: a failure on
    the ground used to be announced with "Executando pouso seguro" while the
    aircraft sat on the pad.
    """
    details = details or {}
    action_lower = action.strip().lower()
    airborne = details.get("em_voo") is True
    reason = details.get("erro", details.get("etapa"))

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
            return str(details["etapa"]).capitalize()
        if details and "acao" in details:
            return f"Missão abortada, {details['acao']}."
        return "Missão abortada, pousando drone."
    if "iniciando missão" in action_lower:
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
    if "retorno à base" in action_lower or "rtl" in action_lower:
        return "Retorno à base iniciado. Navegando para o ponto de lançamento."
    if "pouso seguro" in action_lower or "pouso concluído" in action_lower:
        return "Pouso seguro concluído com sucesso."

    if details and "etapa" in details:
        return str(details["etapa"]).capitalize()

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
            client = _get_synthesis_client(token)
            ctx = client.aio.live.connect(model=SYNTHESIZER_MODEL, config=self.session_config)
            session = await ctx.__aenter__()
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
            session = await ctx.__aenter__()
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
        start on the first one instead of after the whole turn.
        """
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
        if len(audio) < _MIN_AUDIO_BYTES and reason != "no_session":
            logger.debug("Synthesis produced no audio (%s); retrying on a fresh session.", reason)
            audio, reason = await self._synthesize_once(statement, verbatim, on_chunk)
        logger.debug("Synthesis ended: %s, %d bytes.", reason, len(audio))
        return audio

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
        waiting for. Returns how many sentences were scheduled.
        """
        pending: List[str] = []
        for text in texts:
            statement = str(text or "").strip()
            if statement and statement not in pending and not self._cache.has(statement, True):
                pending.append(statement)
        if not pending or self._loop is None or self.session_config is None or not self._active:
            return 0

        async def fill() -> None:
            for statement in pending:
                while self._active and (not self._queue.empty() or self._synthesis_task is not None):
                    await asyncio.sleep(0.1)
                if not self._active:
                    return
                if self._cache.has(statement, True):
                    continue
                try:
                    audio = await self._synthesize(statement, True)
                except Exception as exc:  # noqa: BLE001 - the cache is an optimisation
                    logger.debug("Cache warm-up failed for '%s': %s", statement[:40], exc)
                    continue
                self._cache.put(statement, True, audio)

        self._loop.call_soon_threadsafe(lambda: self._loop.create_task(fill()))
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

            # Preemption on urgent priority: narration is cut and dropped, but
            # an alert never cuts or drops another alert -- "step failed"
            # followed by "aborting" is heard in full, in order.
            if pri_int == 0:
                if time.monotonic() >= self._playing_urgent_until:
                    self.device.stop_current()
                kept = []
                while not self._queue.empty():
                    try:
                        q_item = self._queue.get_nowait()
                        self._queue.task_done()
                    except (asyncio.QueueEmpty, ValueError):
                        break
                    if q_item[0] == 0:
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
            spoke = len(audio_buffer) >= _MIN_AUDIO_BYTES
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
                if pri_int == 0:
                    self._playing_urgent_until = time.monotonic() + audio_sec
                # Paired with the `[SPEECH]` start line: start plus audio_ms is
                # when the line stops sounding (scripts/bench_rehearsal.py).
                logger.info(
                    "[SPEECH_DONE] '%s' audio_ms=%d priority=%s",
                    statement[:48],
                    int(audio_sec * 1000.0),
                    "URGENT" if pri_int == 0 else "NORMAL",
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

        pri_int = 0 if _is_urgent(action, details, priority) else 1

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
_URGENT_PRIORITIES: Final = ("URGENT", "CRITICAL", "EMERGENCY")


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
# can be closed remotely. Each exists in several wordings and the order is drawn
# fresh per flight, so two consecutive missions never produce the same-looking
# report -- a demonstration that reads as a live assessment rather than a canned
# slide. The ground station holds the same table in
# `src/lib/forensics.ts` and is the one that decides the order for a given
# flight; this copy is what the mission side uses when it narrates on its own.
# -----------------------------------------------------------------------------

FORENSIC_FINDINGS: Dict[str, List[str]] = {
    "police": [
        "Sem necessidade de polícia",
        "Acionamento policial dispensado",
        "Sem demanda para autoridade policial",
        "Segurança pública não requisitada",
    ],
    "samu": [
        "Sem necessidade de Samu",
        "Socorro médico dispensado",
        "Atendimento emergencial não necessário",
        "SAMU dispensado para a ocorrência",
    ],
    "victim": [
        "Estado do acidentado não é grave",
        "Vítima consciente e sem gravidade",
        "Acidentado sem ferimentos críticos",
        "Condição da vítima considerada leve",
    ],
    "vehicle": [
        "Veículo não danificado",
        "Sem danos estruturais aparentes no veículo",
        "Integridade do automóvel preservada",
        "Veículo sem avarias mecânicas",
    ],
}

#: What leads each finding when read, so the report is talked through rather
#: than listed. Same pools as ``src/lib/forensics.ts``; every finding is a
#: clearance, so the connectors add and never contrast.
FORENSIC_OPENERS: Final[tuple] = (
    "Para começar,",
    "De início,",
    "Logo de cara,",
    "Começando pelo essencial,",
    "De saída,",
    "Abrindo a análise,",
)
FORENSIC_LINKERS: Final[tuple] = (
    "Além disso,",
    "Na sequência,",
    "Somado a isso,",
    "Em seguida,",
    "Outro ponto:",
    "Também observamos:",
    "E mais:",
)
FORENSIC_CLOSERS: Final[tuple] = (
    "Por fim,",
    "Para fechar,",
    "Para arrematar,",
    "E, para concluir,",
    "Encerrando,",
    "Por último,",
)


def _decapitalise(line: str) -> str:
    """Lower the first letter so a line reads on after an ordinal.

    An acronym is left alone: "SAMU dispensado" must not become "sAMU".
    """
    if len(line) > 1 and line[1].isupper():
        return line
    return line[0].lower() + line[1:]

FORENSIC_INTRO = "Inspeção do sinistro concluída. Apresentando laudo preliminar."
FORENSIC_OUTRO = "Relatório pericial emitido e pronto para exportação."


def build_forensic_report(seed: Optional[int] = None) -> List[Dict[str, str]]:
    """Draw one report: a wording per topic, in a shuffled order.

    Returns one dict per finding with ``topic``, ``card`` (the line the station
    displays) and ``speech`` (the same line as the copilot reads it, led by an
    opener, a linker or a closer; the two linkers of a report differ).
    """
    rng = random.Random(seed)
    topics = list(FORENSIC_FINDINGS)
    rng.shuffle(topics)
    linkers = rng.sample(FORENSIC_LINKERS, max(0, len(topics) - 2))
    report: List[Dict[str, str]] = []
    for index, topic in enumerate(topics):
        card = rng.choice(FORENSIC_FINDINGS[topic])
        if index == 0:
            connector = rng.choice(FORENSIC_OPENERS)
        elif index == len(topics) - 1:
            connector = rng.choice(FORENSIC_CLOSERS)
        else:
            connector = linkers[index - 1]
        report.append(
            {
                "topic": topic,
                "card": card,
                "speech": f"{connector} {_decapitalise(card)}.",
            }
        )
    return report


def announce_forensic_report(
    report: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """Read a whole report aloud, one finding at a time.

    Blocks for the length of the narration, so a caller that also paints the
    screen stays in step with it. The ground station does not use this path --
    it drives the cadence itself over the daemon below, so that each card lands
    on the sentence that describes it -- but the mission process can narrate a
    report with one call.
    """
    findings = report if report is not None else build_forensic_report()
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
    # from the handshake rather than from four silent requests.
    _emit_speech_event(
        {
            "event": "ready",
            "voice": SYNTHESIZER_VOICE,
            "model": SYNTHESIZER_MODEL,
            "synthesis": _load_genai()[0] is not None and bool(announcer.auth_token),
            "playback": announcer.device._active,
            **announcer.device.get_level(),
            "detail": (
                "google-genai não instalado"
                if _load_genai()[0] is None
                else "sem chave de API (SPEECH_API_KEY / GEMINI_API_KEY)"
                if not announcer.auth_token
                else "sem dispositivo de áudio (PortAudio)"
                if not announcer.device._active
                else "pronto"
            ),
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
                texts = request.get("texts")
                if isinstance(texts, list) and not announcer.device.get_level()["muted"]:
                    scheduled = announcer.warm_cache([t for t in texts if isinstance(t, str)])
                    _emit_speech_event({"event": "cache", "scheduled": scheduled, "requested": len(texts)})
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
        findings = announce_forensic_report()
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

