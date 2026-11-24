"""Text-to-speech adapters — Piper (default, local) and edge-tts (cloud).

Piper is the safe default: light neural voices, fast on CPU, fully
offline. edge-tts sounds very natural but needs internet and is
unofficial (it can break if Microsoft changes it). Roman Urdu replies
use edge-tts with an Urdu voice until a good local Urdu voice exists.

Both are optional and guarded: if the library is not installed,
``available()`` is False and ``speak()`` raises ``NotAvailable`` —
construction never raises, so the assistant can probe for engines and
fall back to printing replies instead of crashing at import time.
Builder B owns this file.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import tempfile
import threading

from .base import NotAvailable, NullTTS

# Voice used for Roman Urdu replies — no good local Urdu voice exists
# yet, so the natural-sounding Edge voice carries this lane.
URDU_VOICE = "ur-PK-AsadNeural"
ENGLISH_VOICE = "en-US-AriaNeural"


def _play_wav_bytes(wav_bytes: bytes, sample_rate: int) -> subprocess.Popen | None:
    """Play WAV bytes. Returns the player process, or None.

    Prefers sounddevice (in-process); falls back to a system player
    (aplay / paplay); returns None when nothing can play audio.
    """
    try:
        import numpy as np
        import sounddevice as sd

        audio = np.frombuffer(wav_bytes, dtype=np.int16)
        sd.play(audio, samplerate=sample_rate)
        return None
    except ImportError:
        pass
    for player in ("aplay", "paplay"):
        if shutil.which(player):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name
            try:
                return subprocess.Popen(
                    [player, tmp_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                os.unlink(tmp_path)
                return None
    return None


def _play_audio_file(path: str) -> subprocess.Popen | None:
    """Play an audio file (e.g. edge-tts MP3) with the first player found.

    On Windows the built-in MCI player is tried first — stock Windows has
    no mpv/ffplay/mpg123, so without it edge-tts audio was synthesized
    and then never heard. Falls back to mpv / ffplay / mpg123, and to
    None when nothing can play the file.
    """
    if platform.system() == "Windows":
        try:
            return _MCIPlayer(path)
        except OSError:
            pass  # fall through to the external players below
    for player, args in (
        ("mpv", ["--no-video", "--really-quiet"]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ("mpg123", ["-q"]),
    ):
        if shutil.which(player):
            try:
                return subprocess.Popen(
                    [player, *args, path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return None
    return None


class _MCIPlayer:
    """Windows-native audio player via winmm MCI — no extra installs.

    Plays an MP3 with the media control interface built into every
    Windows. Mimics the ``subprocess.Popen`` surface EdgeTTS uses:
    ``wait()`` blocks until the file finishes, ``poll()`` is None while
    playing and 0 once done, ``terminate()`` stops playback (barge-in).
    """

    def __init__(self, path: str) -> None:
        import ctypes

        self._winmm = ctypes.windll.winmm
        self.alias = f"kairo{id(self) % 10**8}"
        self._done = False
        self._winmm.mciSendStringW(f"close {self.alias}", None, 0, None)
        err = self._winmm.mciSendStringW(
            f'open "{path}" alias {self.alias}', None, 0, None
        )
        if err != 0:
            raise OSError(f"MCI could not open {path} (error {err})")

    def wait(self) -> None:
        self._winmm.mciSendStringW(f"play {self.alias} wait", None, 0, None)
        self._done = True
        self._winmm.mciSendStringW(f"close {self.alias}", None, 0, None)

    def poll(self) -> int | None:
        return 0 if self._done else None

    def terminate(self) -> None:
        self._winmm.mciSendStringW(f"stop {self.alias}", None, 0, None)
        self._winmm.mciSendStringW(f"close {self.alias}", None, 0, None)
        self._done = True


class PiperTTS:
    """Piper — default TTS, local, CPU-friendly, fully offline.

    ``voice`` is the path to a Piper ``.onnx`` voice model (or empty,
    which means "no model configured yet" — ``speak()`` then says so
    plainly instead of crashing). The model is loaded lazily on the
    first speak, so merely constructing the adapter costs nothing.
    ``speak()`` returns once speaking has started; ``stop()`` cancels it.
    """

    def __init__(self, voice: str = "") -> None:
        self.voice = voice
        self._piper_voice = None
        self._player: subprocess.Popen | None = None
        self._stop_event = threading.Event()
        self._speak_thread: threading.Thread | None = None
        try:
            import piper  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"piper-tts is not installed ({exc})."
        else:
            self._available = True
            self._error = ""

    def available(self) -> bool:
        return self._available

    @property
    def speaking(self) -> bool:
        """True while the speak thread is still running."""
        thread = self._speak_thread
        return thread is not None and thread.is_alive()

    def _load_voice(self):
        if self._piper_voice is not None:
            return self._piper_voice
        from piper import PiperVoice

        self._piper_voice = PiperVoice.load(self.voice)
        return self._piper_voice

    def _speak_sync(self, text: str) -> None:
        voice = self._load_voice()
        chunks: list[bytes] = []
        sample_rate = 22050
        for chunk in voice.synthesize(text):
            if self._stop_event.is_set():
                return
            sample_rate = chunk.sample_rate
            chunks.append(chunk.audio_int16_bytes)
        if self._stop_event.is_set() or not chunks:
            return
        wav = _wav_wrap(b"".join(chunks), sample_rate)
        self._player = _play_wav_bytes(wav, sample_rate)
        if self._player is None and not _sounddevice_available():
            # No player at all: keep the audio instead of dropping it silently.
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False, prefix="kairo-piper-"
            ) as tmp:
                tmp.write(wav)
            print(f"[kairo] No audio player found; Piper audio saved to {tmp.name}")

    def speak(self, text: str) -> None:
        if not self._available:
            raise NotAvailable(self._error or "piper-tts is not installed.")
        if not text.strip():
            return
        if not self.voice:
            raise NotAvailable(
                "No Piper voice model configured — set KAIRO_TTS_VOICE to a "
                "Piper .onnx model path (download one from rhasspy.github.io/piper)."
            )
        self._stop_event.clear()
        # Speak on a thread so speak() returns once speaking has started.
        self._speak_thread = threading.Thread(
            target=self._speak_sync, args=(text,), daemon=True
        )
        self._speak_thread.start()

    def stop(self) -> None:
        """Stop current speech (barge-in)."""
        self._stop_event.set()
        try:
            import sounddevice as sd

            sd.stop()
        except ImportError:
            pass
        if self._player is not None and self._player.poll() is None:
            self._player.terminate()
        self._player = None


def _sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        return False
    return True


def _wav_wrap(pcm_bytes: bytes, sample_rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buf.getvalue()


class EdgeTTS:
    """edge-tts — natural voices, free, needs internet (unofficial).

    ``speak()`` returns once speaking has started; ``stop()`` cancels
    the download/playback. The audio is synthesized to a temp MP3 and
    played with the first available player (Windows MCI, then mpv /
    ffplay / mpg123).
    """

    def __init__(self, voice: str = ENGLISH_VOICE) -> None:
        self.voice = voice
        self._player: subprocess.Popen | None = None
        self._stop_event = threading.Event()
        self._speak_thread: threading.Thread | None = None
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"edge-tts is not installed ({exc})."
        else:
            self._available = True
            self._error = ""

    def available(self) -> bool:
        return self._available

    @property
    def speaking(self) -> bool:
        """True while the speak thread is still running (synthesis +
        playback — the thread waits for the player to finish)."""
        thread = self._speak_thread
        return thread is not None and thread.is_alive()

    async def _synthesize(self, text: str, path: str) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(path)

    def _speak_sync(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            asyncio.run(self._synthesize(text, tmp_path))
            if self._stop_event.is_set():
                return
            self._player = _play_audio_file(tmp_path)
            if self._player is None:
                print(f"[kairo] No audio player found; edge-tts audio saved to {tmp_path}")
                return
            self._player.wait()
        finally:
            if self._player is None or self._player.poll() is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def speak(self, text: str) -> None:
        if not self._available:
            raise NotAvailable(self._error or "edge-tts is not installed.")
        if not text.strip():
            return
        self._stop_event.clear()
        # Speak on a thread so speak() returns once speaking has started.
        self._speak_thread = threading.Thread(
            target=self._speak_sync, args=(text,), daemon=True
        )
        self._speak_thread.start()

    def stop(self) -> None:
        """Stop current speech (barge-in): cancel synthesis and playback."""
        self._stop_event.set()
        if self._player is not None and self._player.poll() is None:
            self._player.terminate()
        self._player = None


def pick_tts(settings, language: str | None = None):
    """Pick the TTS engine for a reply language. Never raises.

    * Roman Urdu (``"ur"`` / ``"urdu"`` / ``"roman-urdu"``) -> edge-tts
      with the Urdu voice ``ur-PK-AsadNeural`` while no good local Urdu
      voice exists; falls back to Piper when edge-tts is unavailable.
    * English (default) -> Piper (local, offline); falls back to
      edge-tts when Piper is unavailable.
    * Nothing installed -> ``NullTTS`` (the loop prints the reply).
    """
    lang = (language or getattr(settings, "language", "en") or "en").strip().lower()
    is_urdu = lang in {"ur", "ur-pk", "urdu", "roman-urdu", "roman urdu"}
    tts_voice = getattr(settings, "tts_voice", "") or ""

    if is_urdu:
        edge = EdgeTTS(URDU_VOICE)
        if edge.available():
            return edge
        piper = PiperTTS(tts_voice)
        if piper.available():
            return piper
        return NullTTS()

    piper = PiperTTS(tts_voice)
    if piper.available():
        return piper
    edge = EdgeTTS(ENGLISH_VOICE)
    if edge.available():
        return edge
    return NullTTS()
