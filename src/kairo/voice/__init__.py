"""Voice pipeline — wake word, speech-to-text, text-to-speech, voice loop.

Default stack (plan section 3): openWakeWord -> faster-whisper -> Piper,
all local, CPU-friendly, free. Porcupine / Whisper tiny-base / Vosk are
fallbacks. Every heavy import is guarded: on a machine without the audio
libraries the pipeline reports itself unavailable instead of crashing, and
the assistant falls back to text mode.
"""

from .base import STT, TTS, WakeWord
from .loop import VoiceLoop

__all__ = ["STT", "TTS", "WakeWord", "VoiceLoop"]
