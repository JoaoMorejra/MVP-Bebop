"""Acoustic telemetry and synthesized speech notification system.

Provides asynchronous, non-blocking telemetry voice notifications for
ground operators during autonomous mission execution.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import queue
import sys
import threading
import time
from typing import Any, Dict, Optional

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore
    types = None  # type: ignore

logger = logging.getLogger("TelemetryAnnouncer")

PCM_SAMPLE_RATE = 24_000
PCM_CHANNELS = 1
PCM_DTYPE = "int16"
SYNTHESIZER_MODEL = os.environ.get("SPEECH_MODEL", "gemini-3.1-flash-live-preview")
SYNTHESIZER_VOICE = os.environ.get("SPEECH_VOICE", "Orbit")

_cached_client: Optional[Any] = None
_client_lock = threading.Lock()


def _get_synthesis_client(api_key: str) -> Any:
    global _cached_client
    with _client_lock:
        if _cached_client is None:
            if genai is None:
                raise RuntimeError("Speech synthesis client dependencies not installed.")
            _cached_client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
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

    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        "/home/jv/ros2_ws/.env",
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

        self._queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._active: bool = False
        self._is_playing: bool = False
        self._stop_requested: threading.Event = threading.Event()
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
                self._stop_requested.clear()
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

    def play_audio(self, pcm_data: bytes) -> None:
        """Enqueue PCM audio buffer for sequential playback."""
        if not pcm_data or not self._active:
            return
        self._playback_finished.clear()
        self._queue.put(pcm_data)

    def _playback_worker(self) -> None:
        import numpy as np
        import sounddevice as sd

        while self._active:
            try:
                data = self._queue.get(timeout=0.1)
                if data is None:
                    break

                if self._stop_requested.is_set():
                    self._queue.task_done()
                    continue

                if self._active and len(data) > 0:
                    self._is_playing = True
                    self._playback_finished.clear()
                    try:
                        samples = np.frombuffer(data, dtype=np.int16)
                        sd.play(samples, samplerate=self.sample_rate)
                        sd.wait()
                    except Exception as err:
                        logger.debug("Sounddevice playback error: %s", err)

                self._queue.task_done()
            except queue.Empty:
                if self._is_playing:
                    self._is_playing = False
                    self._playback_finished.set()
                continue
            except Exception as e:
                logger.debug("Playback worker exception: %s", e)
                break

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
            time.sleep(0.04)

        remaining = None
        if timeout:
            remaining = max(0.0, timeout - (time.time() - start_time))
        return self._playback_finished.wait(timeout=remaining)

    def stop_current(self) -> None:
        """Halt active playback and drain remaining audio queue."""
        self._stop_requested.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except (queue.Empty, ValueError):
                break

        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

        self._is_playing = False
        self._playback_finished.set()
        self._stop_requested.clear()

    def close(self) -> None:
        """Shut down playback worker."""
        with self._lock:
            self._active = False
            self._queue.put(None)
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
                self._thread = None


_global_playback_device: Optional[AudioPlaybackDevice] = None
_playback_lock = threading.Lock()


def get_audio_playback_device() -> AudioPlaybackDevice:
    """Retrieve or initialize singleton audio playback device."""
    global _global_playback_device
    with _playback_lock:
        if _global_playback_device is None:
            _global_playback_device = AudioPlaybackDevice()
            _global_playback_device.start()
        return _global_playback_device


def _format_telemetry_statement(
    action: str,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Format structured telemetry action into concise aeronautical phrase."""
    is_fault = (
        (details and "erro" in details)
        or ("erro" in action.lower())
        or ("falha" in action.lower())
    )

    if is_fault:
        err = details.get("erro", action) if details else action
        return f"Alerta de voo: {err}. Executando pouso seguro imediatamente."

    action_lower = action.lower()
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
        self._lock = threading.Lock()
        self._seq: int = 0
        self._active: bool = True

        if types is not None:
            self.session_config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=SYNTHESIZER_VOICE)
                    )
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
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _ensure_warm_session(self) -> None:
        if not self._active or self.session_config is None:
            return
        if self._warm_session is not None:
            if (time.time() - self._warm_session_time) > 40.0:
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
        except Exception as exc:
            logger.debug("Warm session initialization check: %s", exc)
            self._warm_session = None
            self._warm_ctx = None
        finally:
            self._is_warming = False

    async def _consumer_worker(self) -> None:
        await self._ensure_warm_session()

        while self._active:
            try:
                item = await self._queue.get()
            except Exception:
                break

            pri_int, seq, action, details, done_fut = item

            # Preemption on urgent priority
            if pri_int == 0:
                self.device.stop_current()
                while not self._queue.empty():
                    try:
                        q_item = self._queue.get_nowait()
                        self._queue.task_done()
                        if q_item[4] and not q_item[4].done():
                            q_item[4].set_result(False)
                    except (asyncio.QueueEmpty, ValueError):
                        break

            statement = _format_telemetry_statement(action, details)
            logger.info("Acoustic announcement: '%s' (Priority: %s)", statement, "URGENT" if pri_int == 0 else "NORMAL")

            session = self._warm_session
            ctx = self._warm_ctx
            self._warm_session = None
            self._warm_ctx = None

            asyncio.create_task(self._ensure_warm_session())

            if session is None:
                token = self.auth_token or _resolve_auth_token()
                if not token:
                    self._queue.task_done()
                    if done_fut and not done_fut.done():
                        done_fut.set_result(False)
                    continue
                try:
                    client = _get_synthesis_client(token)
                    ctx = client.aio.live.connect(model=SYNTHESIZER_MODEL, config=self.session_config)
                    session = await ctx.__aenter__()
                except Exception as exc:
                    logger.debug("Speech synthesis session failure: %s", exc)
                    self._queue.task_done()
                    if done_fut and not done_fut.done():
                        done_fut.set_result(False)
                    continue

            audio_buffer = bytearray()
            try:
                instruction = (
                    f"Vocalize this operational status in Portuguese concisely, similar to: '{statement}'. "
                    "Speak clearly, calmly and directly as an autonomous flight copilot. "
                    "Never add greetings, conversational filler, or address spectators."
                )
                await session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=instruction)],
                    ),
                    turn_complete=True,
                )

                async def _read_audio() -> None:
                    stream = session.receive()
                    while True:
                        step_timeout = 1.5 if len(audio_buffer) >= 4800 else 12.0
                        try:
                            response = await asyncio.wait_for(stream.__anext__(), timeout=step_timeout)
                        except (asyncio.TimeoutError, StopAsyncIteration):
                            break

                        if response.go_away:
                            break
                        sc = response.server_content
                        if not sc:
                            continue
                        if sc.model_turn and sc.model_turn.parts:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    if "audio" in (part.inline_data.mime_type or "audio/pcm"):
                                        audio_buffer.extend(part.inline_data.data)
                        if sc.turn_complete or sc.generation_complete:
                            break

                await _read_audio()

            except Exception as exc:
                logger.debug("Speech streaming exception: %s", exc)
            finally:
                if ctx is not None:
                    try:
                        await ctx.__aexit__(None, None, None)
                    except Exception:
                        pass

            if len(audio_buffer) >= 4800:
                if pri_int != 0:
                    self.device.wait_until_done()
                self.device.play_audio(bytes(audio_buffer))

            self._queue.task_done()
            if done_fut and not done_fut.done():
                done_fut.set_result(True)

    def announce(
        self,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        priority: str = "NORMAL",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> bool:
        """Enqueue flight telemetry announcement asynchronously without blocking."""
        if not self._active or self._loop is None or self._queue is None:
            return False

        is_urgent = (
            priority.upper() in ("URGENT", "CRITICAL", "EMERGENCY")
            or (details and "erro" in details)
            or ("erro" in action.lower())
            or ("falha" in action.lower())
        )
        pri_int = 0 if is_urgent else 1

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
            (pri_int, seq, action, details, done_fut),
        )

        if wait and done_event:
            success = done_event.wait(timeout=timeout or 15.0)
            if success:
                self.device.wait_until_done(timeout=timeout or 15.0)
            return success

        return True

    def close(self) -> None:
        """Terminate announcer and clean up worker threads."""
        self._active = False
        if self._warm_ctx and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._warm_ctx.__aexit__(None, None, None), self._loop)
        if self.device:
            self.device.close()


_global_announcer: Optional[MissionAudioAnnouncer] = None
_announcer_lock = threading.Lock()


def get_announcer() -> MissionAudioAnnouncer:
    """Retrieve or initialize the global mission audio announcer."""
    global _global_announcer
    with _announcer_lock:
        if _global_announcer is None:
            _global_announcer = MissionAudioAnnouncer()
        return _global_announcer


def announce(
    action: str,
    details: Optional[Dict[str, Any]] = None,
    priority: str = "NORMAL",
    wait: bool = False,
    timeout: Optional[float] = None,
) -> bool:
    """Emit telemetry notification."""
    try:
        return get_announcer().announce(action, details, priority=priority, wait=wait, timeout=timeout)
    except Exception as exc:
        logger.debug("Announce dispatch exception: %s", exc)
        return False


def announce_sync(
    action: str,
    details: Optional[Dict[str, Any]] = None,
    wait: bool = False,
    priority: str = "NORMAL",
) -> bool:
    """Synchronous interface for mission step hooks."""
    return announce(action, details=details, priority=priority, wait=wait)


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
    args = parser.parse_args()

    details_dict = {"etapa": args.details} if args.details else None
    success = announce_sync(args.action, details=details_dict, priority=args.priority, wait=args.wait)
    get_announcer().close()
    sys.exit(0 if success else 1)

