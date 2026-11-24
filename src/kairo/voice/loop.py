"""The voice loop — push-to-talk first, wake word later (plan section 3).

States: IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE, with barge-in
(SPEAKING -> LISTENING) when the boss speaks while the assistant speaks.

Push-to-talk: press Enter to talk. In text mode (no audio engines
installed) the typed line *is* the utterance, so the loop works on any
machine with no microphone. In audio mode, Enter starts one recorded
utterance, which is transcribed and answered. A global hotkey (e.g. the
key below Esc, via ``keyboard`` / ``pynput``) is an optional later step —
deliberately not a hard dependency here, so the loop never breaks on a
machine without it. Always-on wake-word listening is switched on later,
once the pipeline is solid (plan lesson 7).

Builder B owns this file.
"""

from __future__ import annotations

import time

from ..boss import Boss
from ..config import Settings
from ..state import AssistantState, StateMachine
from .base import NotAvailable, NullSTT, NullTTS, NullWakeWord
from .stt import FasterWhisperSTT, VoskSTT
from .tts import pick_tts
from .wake import OpenWakeWordAdapter, PorcupineAdapter

QUIT_WORDS = {"quit", "exit", "bye", "q"}
SAMPLE_RATE = 16000


def _default_stt():
    """First STT engine that is actually installed, else NullSTT."""
    for candidate in (FasterWhisperSTT, VoskSTT):
        try:
            engine = candidate()
        except NotAvailable:
            continue
        if engine.available():
            return engine
    return NullSTT()


def _default_tts(settings: Settings):
    """TTS picked for the configured reply language, else NullTTS."""
    return pick_tts(settings, getattr(settings, "language", "en"))


def _default_wake(settings: Settings):
    """First wake-word engine that is actually installed, else NullWakeWord."""
    for candidate in (OpenWakeWordAdapter, PorcupineAdapter):
        try:
            engine = candidate(getattr(settings, "wake_word", "assistant"))
        except NotAvailable:
            continue
        if engine.available():
            return engine
    return NullWakeWord()


def _record_utterance(max_seconds: float = 15.0, silence_seconds: float = 1.5) -> bytes:
    """Record one utterance from the microphone; b"" when no mic is available.

    Records 16 kHz mono int16 PCM and stops after ``silence_seconds`` of
    quiet following speech (or ``max_seconds``). When ``sounddevice`` /
    ``numpy`` is missing there is no recorder, so b"" is returned and the
    caller decides what to do with it — the loop never crashes for it.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        return b""
    block = 1024
    chunks: list[bytes] = []
    heard_speech = False
    quiet_since: float | None = None
    started = time.monotonic()
    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block
    ) as stream:
        while time.monotonic() - started < max_seconds:
            data, _overflow = stream.read(block)
            chunks.append(bytes(data))
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            if rms > 500:  # speech, not room noise
                heard_speech = True
                quiet_since = None
            elif heard_speech:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= silence_seconds:
                    break
    return b"".join(chunks)


class VoiceLoop:
    """Voice mode. Push-to-talk loop around ``boss.handle``."""

    def __init__(self, boss: Boss, settings: Settings, stt=None, tts=None, wake=None) -> None:
        self.boss = boss
        self.settings = settings
        self.state = StateMachine()
        self.stt = stt if stt is not None else _default_stt()
        self.tts = tts if tts is not None else _default_tts(settings)
        self.wake = wake if wake is not None else _default_wake(settings)

    @property
    def text_mode(self) -> bool:
        """True when no audio engines are available — typed lines instead."""
        return not self.stt.available() and not self.tts.available()

    def listen_once(self) -> str:
        """Transcribe one utterance (or read one typed line in text mode)."""
        self.state.transition(AssistantState.LISTENING, note="listen")
        try:
            if self.text_mode:
                try:
                    line = input("You, boss: ")
                except EOFError:
                    return ""
                return line.strip()
            audio = _record_utterance()
            try:
                return self.stt.transcribe(audio).strip()
            except NotAvailable:
                # STT vanished mid-flight: fall back to a typed line.
                print("[kairo] Speech-to-text is not available; type instead, boss.")
                try:
                    return input("You, boss: ").strip()
                except EOFError:
                    return ""
        finally:
            if self.state.state is AssistantState.LISTENING:
                self.state.transition(AssistantState.IDLE, note="heard")

    def speak(self, text: str) -> None:
        """Speak a reply; records the transition to SPEAKING.

        Uses TTS when available, otherwise prints the reply prefixed
        with ``Kairo: ``. The state stays SPEAKING until the caller (or
        ``interrupt()``) moves it on, so barge-in can find it.
        """
        self.state.transition(AssistantState.SPEAKING, note="speak")
        if self.tts.available():
            try:
                self.tts.speak(text)
                return
            except NotAvailable as exc:
                print(f"[kairo] {exc}")
        print(f"Kairo: {text}", flush=True)

    def interrupt(self) -> bool:
        """Barge-in: stop speaking and start listening. Returns True if stopped."""
        if self.state.state is not AssistantState.SPEAKING:
            return False
        try:
            self.tts.stop()
        except Exception:
            pass
        return self.state.interrupt()

    def _one_turn(self, heard: str) -> None:
        """LISTENING -> THINKING -> SPEAKING -> IDLE for one utterance."""
        if not heard.strip():
            return
        self.state.transition(AssistantState.THINKING, note="think")
        try:
            reply = self.boss.handle(heard)
        except Exception as exc:  # never kill the loop on one bad turn
            reply = f"Sorry, boss — that didn't work: {exc}"
        self.speak(reply)
        self.state.transition(AssistantState.IDLE, note="done")

    def run_hands_free(self) -> None:
        """Hands-free loop: wake word -> listen -> act -> speak -> wait again.

        Say the wake word any time and Kairo listens; after answering it
        goes back to waiting for the wake word. Say 'quit' (or press
        Ctrl+C) to leave. Needs a wake-word engine (openWakeWord) plus
        STT/TTS — without a wake engine it raises NotAvailable so the
        caller can fall back to push-to-talk instead of hanging silently.
        """
        if not self.wake.available():
            raise NotAvailable(
                "Hands-free needs a wake-word engine — install openWakeWord "
                "(`pip install openwakeword`), or use push-to-talk voice mode."
            )
        if self.text_mode:
            raise NotAvailable(
                "Hands-free needs a microphone and audio libraries — "
                "install faster-whisper, sounddevice and edge-tts first, "
                "or use text mode."
            )
        print(
            "[kairo] Hands-free mode — say the wake word any time, boss. "
            "(Built-in openWakeWord models: 'Hey Jarvis' or 'Alexa'.) "
            "Say 'quit' to leave, or press Ctrl+C."
        )
        try:
            while True:
                if not self.wake.listen_once(timeout=30.0):
                    continue  # nothing heard yet — keep waiting
                print("[kairo] Yes, boss?")
                try:
                    heard = self.listen_once()
                except NotAvailable as exc:
                    print(f"[kairo] {exc}")
                    continue
                if heard.strip().lower() in QUIT_WORDS:
                    print("Kairo: Goodbye, boss.")
                    break
                self._one_turn(heard)
        except KeyboardInterrupt:
            print("\nKairo: Goodbye, boss.")
        finally:
            if self.state.state is not AssistantState.IDLE:
                self.state.transition(AssistantState.IDLE, note="exit")

    def run(self) -> None:
        """Push-to-talk loop until the boss says quit (or EOF / Ctrl-C)."""
        if self.text_mode:
            print(
                "[kairo] Voice mode (text mode — no microphone or audio "
                "libraries here, so type instead, boss). "
                "Type 'quit' to leave."
            )
            while True:
                try:
                    line = input("You, boss: ")
                except (EOFError, KeyboardInterrupt):
                    print("\nKairo: Goodbye, boss.")
                    break
                text = line.strip()
                if not text:
                    continue
                if text.lower() in QUIT_WORDS:
                    print("Kairo: Goodbye, boss.")
                    break
                self.state.transition(AssistantState.LISTENING, note="listen")
                self._one_turn(text)
            if self.state.state is not AssistantState.IDLE:
                self.state.transition(AssistantState.IDLE, note="exit")
            return

        print(
            "[kairo] Voice mode — press Enter to talk, or type 'quit' to leave. "
            "(A global talk hotkey via keyboard/pynput is a later step; "
            "Enter is the push-to-talk key for now, boss.)"
        )
        while True:
            try:
                line = input("Press Enter to talk: ")
            except (EOFError, KeyboardInterrupt):
                print("\nKairo: Goodbye, boss.")
                break
            if line.strip().lower() in QUIT_WORDS:
                print("Kairo: Goodbye, boss.")
                break
            try:
                heard = self.listen_once()
            except NotAvailable as exc:
                print(f"[kairo] {exc}")
                continue
            self._one_turn(heard)
        if self.state.state is not AssistantState.IDLE:
            self.state.transition(AssistantState.IDLE, note="exit")
