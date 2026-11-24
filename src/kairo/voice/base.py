"""Voice pipeline protocols and null adapters.

Every engine implements one small protocol. Swapping an engine never
touches the rest of the assistant (research lesson 2). When the real
library is missing, ``available()`` returns False and the adapter raises
``NotAvailable`` instead of crashing at import time.
"""

from __future__ import annotations

from typing import Protocol


class NotAvailable(RuntimeError):
    """A voice engine is not installed or not usable on this machine."""


class WakeWord(Protocol):
    def available(self) -> bool: ...
    def listen_once(self, timeout: float = 10.0) -> bool:
        """Block until the wake word is heard, or the timeout passes."""
        ...


class STT(Protocol):
    def available(self) -> bool: ...
    def transcribe(self, audio: bytes) -> str:
        """Turn recorded audio bytes into text."""
        ...


class TTS(Protocol):
    def available(self) -> bool: ...
    def speak(self, text: str) -> None:
        """Speak the text out loud. Returns when speaking starts."""
        ...

    def stop(self) -> None:
        """Stop speaking immediately (barge-in)."""
        ...

    @property
    def speaking(self) -> bool:
        """True while speech is still playing."""
        ...


class NullWakeWord:
    """Wake word engine that is never available (no library installed)."""

    def available(self) -> bool:
        return False

    def listen_once(self, timeout: float = 10.0) -> bool:
        raise NotAvailable("No wake-word engine is installed (openWakeWord / Porcupine).")


class NullSTT:
    def available(self) -> bool:
        return False

    def transcribe(self, audio: bytes) -> str:
        raise NotAvailable("No speech-to-text engine is installed (faster-whisper / Vosk).")


class NullTTS:
    def available(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        raise NotAvailable("No text-to-speech engine is installed (Piper / edge-tts).")

    def stop(self) -> None:
        pass

    @property
    def speaking(self) -> bool:
        return False
