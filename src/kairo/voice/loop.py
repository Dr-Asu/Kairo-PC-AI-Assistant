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

import struct
import time
from collections import deque

from ..boss import Boss
from ..config import Settings
from ..state import AssistantState, StateMachine
from .base import NotAvailable, NullSTT, NullTTS, NullWakeWord
from .stt import FasterWhisperSTT, VoskSTT
from .tts import pick_tts
from .wake import OpenWakeWordAdapter, PorcupineAdapter

QUIT_WORDS = {"quit", "exit", "bye", "q"}
SAMPLE_RATE = 16000

# Conversation mode: after the wake word, talk back and forth with no
# wake word between turns. This many empty listens (~30 s of quiet) end
# the session and Kairo goes back to waiting for the wake word.
CONVERSATION_QUIET_TURNS = 2
# Barge-in: while Kairo speaks, this loud a mic signal counts as the boss
# interrupting. Higher than the 500 speech threshold in _record_utterance
# so Kairo's own speaker output does not constantly trip it; this many
# loud blocks in a row (~0.2 s) must agree before it stops.
BARGE_IN_RMS = 2000
BARGE_IN_BLOCKS = 3
# How much of the boss's opening words to keep when they interrupt — the
# last ~1 s of mic audio (16 blocks of 1024 samples at 16 kHz).
BARGE_IN_KEEP_BLOCKS = 16


def _rms_int16(raw: bytes) -> float:
    """Root-mean-square of 16-bit mono PCM bytes (0 for empty input).

    Pure stdlib, so the barge-in watch needs no numpy.
    """
    if len(raw) < 2:
        return 0.0
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    return (sum(s * s for s in samples) / count) ** 0.5


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


def pick_voice_mode(loop: "VoiceLoop", wake_requested: bool) -> str:
    """Choose the voice mode: real-time conversation when a wake engine
    is installed (or asked for); otherwise push-to-talk (Enter to talk)."""
    if wake_requested or loop.wake.available():
        return "conversation"
    return "push_to_talk"


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

    def listen_once(self, prefix: bytes = b"") -> str:
        """Transcribe one utterance (or read one typed line in text mode).

        ``prefix`` is audio already heard — the boss's opening words
        captured while Kairo was speaking (barge-in) — prepended to the
        fresh recording so an interruption loses no words.
        """
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
                return self.stt.transcribe(prefix + audio).strip()
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

    # -- real-time conversation (v0.3.10) ----------------------------------
    def _watch_for_speech_while_speaking(self) -> tuple[bool, bytes]:
        """Watch the mic while Kairo speaks. Returns (barged_in, audio).

        Loud mic audio means the boss started talking: stop the speech
        at once and return the audio heard from the moment they started,
        so the next listen does not lose their first words. Only the
        last ~1 s of mic audio is kept — the buffer is a rolling window,
        so Kairo's own speaker output never piles up in it. Returns
        (False, b"") when the reply finishes uninterrupted, or when
        there is no microphone to watch with.
        """
        tts = self.tts
        if not getattr(tts, "speaking", False):
            return False, b""
        try:
            import sounddevice as sd
        except ImportError:
            # No mic to watch with: wait for the reply to finish on its
            # own (capped, so a stuck player can never hang the loop).
            deadline = time.monotonic() + 120.0
            while getattr(tts, "speaking", False) and time.monotonic() < deadline:
                time.sleep(0.05)
            return False, b""
        heard: deque[bytes] = deque(maxlen=BARGE_IN_KEEP_BLOCKS)
        loud_runs = 0
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=1024
            ) as stream:
                while getattr(tts, "speaking", False):
                    block, _overflow = stream.read(1024)
                    raw = bytes(block)
                    rms = _rms_int16(raw)
                    if rms >= 500:  # the boss is saying something — keep it
                        heard.append(raw)
                    if rms >= BARGE_IN_RMS:
                        loud_runs += 1
                        if loud_runs >= BARGE_IN_BLOCKS:
                            tts.stop()
                            return True, b"".join(heard)
                    else:
                        loud_runs = 0
        except Exception:
            # Mic trouble mid-speech must never kill the reply — just
            # let it finish.
            return False, b""
        return False, b""

    def _speak_with_barge_in(self, text: str) -> tuple[bool, bytes]:
        """Speak a reply while listening for the boss. Returns
        (barged_in, audio_heard): True + the boss's opening words when
        they interrupted, False + b"" when the reply played out fully."""
        self.state.transition(AssistantState.SPEAKING, note="speak")
        if self.tts.available():
            try:
                self.tts.speak(text)
            except NotAvailable as exc:
                print(f"[kairo] {exc}")
                print(f"Kairo: {text}", flush=True)
                return False, b""
            return self._watch_for_speech_while_speaking()
        print(f"Kairo: {text}", flush=True)
        return False, b""

    def _one_turn_conversational(self, heard: str) -> tuple[bool, bytes]:
        """One conversational turn: think, then speak while listening
        for an interruption. Returns (barged_in, audio_heard) — the
        next listen picks up where the boss's interruption left off."""
        if not heard.strip():
            return False, b""
        self.state.transition(AssistantState.THINKING, note="think")
        try:
            reply = self.boss.handle(heard)
        except Exception as exc:  # never kill the loop on one bad turn
            reply = f"Sorry, boss — that didn't work: {exc}"
        barged_in, audio = self._speak_with_barge_in(reply)
        if self.state.state is AssistantState.SPEAKING:
            self.state.transition(AssistantState.IDLE, note="done")
        return barged_in, audio

    def _conversation_session(self) -> str:
        """One open conversation after the wake word: turns back and
        forth with no wake word between them, until the boss says quit
        or goes quiet. Returns "quit" or "timeout"."""
        pending_audio = b""
        quiet_turns = 0
        while True:
            heard = self.listen_once(prefix=pending_audio)
            pending_audio = b""
            if not heard.strip():
                quiet_turns += 1
                if quiet_turns >= CONVERSATION_QUIET_TURNS:
                    print(
                        "[kairo] Going quiet, boss — say the wake word "
                        "when you need me."
                    )
                    return "timeout"
                continue
            quiet_turns = 0
            if heard.strip().lower() in QUIT_WORDS:
                print("Kairo: Goodbye, boss.")
                return "quit"
            _barged_in, pending_audio = self._one_turn_conversational(heard)

    def run_conversation(self) -> None:
        """Real-time conversation: wake once, then just talk.

        Say the wake word and Kairo listens; after answering it keeps
        listening — no wake word, no Enter key between turns. Start
        talking while Kairo speaks and it stops at once to hear you
        (barge-in). Say 'quit' (or press Ctrl+C) to leave, or just go
        quiet for ~30 seconds and it goes back to waiting for the wake
        word.
        """
        if not self.wake.available():
            raise NotAvailable(
                "Conversation mode needs a wake-word engine — install "
                "openWakeWord (`pip install openwakeword`), or use "
                "push-to-talk voice mode."
            )
        if self.text_mode:
            raise NotAvailable(
                "Conversation mode needs a microphone and audio libraries — "
                "install faster-whisper, sounddevice and edge-tts first, "
                "or use text mode."
            )
        print(
            "[kairo] Conversation mode — say the wake word, then just "
            "talk, boss. No wake word between turns, and you can "
            "interrupt me any time. Say 'quit' to leave, or press Ctrl+C."
        )
        try:
            while True:
                if not self.wake.listen_once(timeout=30.0):
                    continue  # nothing heard yet — keep waiting
                print("[kairo] Yes, boss?")
                if self._conversation_session() == "quit":
                    break
        except KeyboardInterrupt:
            print("\nKairo: Goodbye, boss.")
        finally:
            if self.state.state is not AssistantState.IDLE:
                self.state.transition(AssistantState.IDLE, note="exit")

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
