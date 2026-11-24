"""Speech-to-text adapters — faster-whisper (default) and Vosk (offline).

Both are optional and guarded: if the library is not installed,
``available()`` is False and ``transcribe()`` raises ``NotAvailable`` —
construction never raises, so the assistant can probe for engines and
fall back to text mode instead of crashing at import time.
Builder B owns this file.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import wave
from pathlib import Path

from .base import NotAvailable

SAMPLE_RATE = 16000


def _audio_to_wav_bytes(audio: bytes) -> bytes:
    """Return WAV bytes: pass WAV through, wrap raw 16 kHz mono PCM."""
    if audio[:4] == b"RIFF":
        return audio
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # int16
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(audio)
    return buf.getvalue()


def _wav_to_pcm_bytes(audio: bytes) -> bytes:
    """Return raw PCM frames: unwrap WAV, pass raw PCM through."""
    if audio[:4] == b"RIFF":
        with wave.open(io.BytesIO(audio), "rb") as wav:
            return wav.readframes(wav.getnframes())
    return audio


class FasterWhisperSTT:
    """faster-whisper — default STT, local, CPU-friendly.

    ``model`` defaults to ``"tiny"``: the fastest Whisper model, the
    right pick for an old CPU with no GPU. The model is loaded lazily
    on the first transcription, so merely constructing the adapter
    costs nothing.
    """

    def __init__(self, model: str = "tiny") -> None:
        self.model_name = model
        self._model = None
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"faster-whisper is not installed ({exc})."
        else:
            self._available = True
            self._error = ""

    def available(self) -> bool:
        return self._available

    def transcribe(self, audio: bytes) -> str:
        """Turn recorded audio bytes (WAV, or raw 16 kHz mono int16 PCM) into text."""
        if not self._available:
            raise NotAvailable(self._error or "faster-whisper is not installed.")
        if not audio:
            return ""
        from faster_whisper import WhisperModel

        if self._model is None:
            self._model = WhisperModel(
                self.model_name, device="cpu", compute_type="int8"
            )
        wav_bytes = _audio_to_wav_bytes(audio)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        try:
            segments, _info = self._model.transcribe(tmp_path)
            return " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            os.unlink(tmp_path)


class VoskSTT:
    """Vosk — fully offline STT, small models, good for short commands.

    ``model_path`` points at a downloaded Vosk model directory; without
    it, ``VOSK_MODEL_PATH`` or ``~/.kairo/vosk-model`` is tried. The
    model is loaded lazily on the first transcription.
    """

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or os.environ.get("VOSK_MODEL_PATH", "")
        self._model = None
        try:
            import vosk  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"vosk is not installed ({exc})."
        else:
            self._available = True
            self._error = ""

    def available(self) -> bool:
        return self._available

    def _resolve_model_path(self) -> str:
        candidates = [
            self.model_path,
            str(Path.home() / ".kairo" / "vosk-model"),
            str(Path.cwd() / "vosk-model"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_dir():
                return candidate
        raise NotAvailable(
            "No Vosk model found — download one from alphacephei.com/vosk/models "
            "and set VOSK_MODEL_PATH (or put it in ~/.kairo/vosk-model)."
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        from vosk import Model

        self._model = Model(self._resolve_model_path())
        return self._model

    def transcribe(self, audio: bytes) -> str:
        """Turn recorded audio bytes (WAV, or raw 16 kHz mono int16 PCM) into text."""
        if not self._available:
            raise NotAvailable(self._error or "vosk is not installed.")
        if not audio:
            return ""
        from vosk import KaldiRecognizer

        model = self._load_model()
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        recognizer.AcceptWaveform(_wav_to_pcm_bytes(audio))
        result = json.loads(recognizer.FinalResult())
        return result.get("text", "").strip()
