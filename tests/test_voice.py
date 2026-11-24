"""Tests for the voice pipeline (Builder B).

No real audio, no microphone, no network: engines are fakes or the
Null adapters, and "installed" libraries are simulated by stubbing
``sys.modules``. Run with: .venv/bin/pytest -q
"""

import io
import sys
import types
from pathlib import Path

import pytest

from kairo.config import Settings
from kairo.state import AssistantState
from kairo.voice.base import (
    NotAvailable,
    NullSTT,
    NullTTS,
    NullWakeWord,
)
from kairo.voice.loop import VoiceLoop
from kairo.voice.stt import FasterWhisperSTT, VoskSTT
from kairo.voice.tts import EdgeTTS, PiperTTS, pick_tts
from kairo.voice.wake import OpenWakeWordAdapter, PorcupineAdapter


# -- fakes ---------------------------------------------------------------
class FakeBoss:
    """Stand-in for Boss (the real one is Builder A's, still a stub)."""

    def __init__(self):
        self.handled: list[str] = []

    def handle(self, text: str) -> str:
        self.handled.append(text)
        return f"Got it, boss: {text}"


class FakeSTT:
    def __init__(self, transcript: str = "hello boss"):
        self.transcript = transcript
        self.calls: list[bytes] = []

    def available(self) -> bool:
        return True

    def transcribe(self, audio: bytes) -> str:
        self.calls.append(audio)
        return self.transcript


class FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []
        self.speaking = False
        self.stop_calls = 0

    def available(self) -> bool:
        return True

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        self.speaking = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.speaking = False


def _settings(**kwargs) -> Settings:
    return Settings(data_dir=kwargs.pop("data_dir", Path("/tmp")), **kwargs)


def _loop(boss=None, settings=None, **engines):
    return VoiceLoop(boss or FakeBoss(), settings or _settings(), **engines)


# -- Null adapters ---------------------------------------------------------
def test_null_wake_word_reports_unavailable_and_raises():
    wake = NullWakeWord()
    assert wake.available() is False
    with pytest.raises(NotAvailable):
        wake.listen_once()


def test_null_stt_reports_unavailable_and_raises():
    stt = NullSTT()
    assert stt.available() is False
    with pytest.raises(NotAvailable):
        stt.transcribe(b"\x00\x00")


def test_null_tts_reports_unavailable_and_raises_on_speak():
    tts = NullTTS()
    assert tts.available() is False
    with pytest.raises(NotAvailable):
        tts.speak("hello boss")
    tts.stop()  # stopping nothing is a no-op, never an error


# -- real adapters with missing libraries ---------------------------------
def test_wake_adapters_unavailable_without_libraries():
    assert OpenWakeWordAdapter().available() is False
    assert PorcupineAdapter().available() is False
    with pytest.raises(NotAvailable):
        OpenWakeWordAdapter().listen_once()
    with pytest.raises(NotAvailable):
        PorcupineAdapter().listen_once()


def test_stt_adapters_unavailable_without_libraries():
    assert FasterWhisperSTT().available() is False
    assert FasterWhisperSTT().model_name == "tiny"  # CPU-friendly default
    assert VoskSTT().available() is False
    with pytest.raises(NotAvailable):
        FasterWhisperSTT().transcribe(b"\x00\x00")
    with pytest.raises(NotAvailable):
        VoskSTT().transcribe(b"\x00\x00")


def test_tts_adapters_unavailable_without_libraries():
    assert PiperTTS().available() is False
    assert EdgeTTS().available() is False
    with pytest.raises(NotAvailable):
        PiperTTS().speak("hello boss")
    with pytest.raises(NotAvailable):
        EdgeTTS().speak("hello boss")


def test_pick_tts_returns_null_when_nothing_installed(monkeypatch):
    for lib in ("piper", "edge_tts"):
        monkeypatch.delitem(sys.modules, lib, raising=False)
    assert isinstance(pick_tts(_settings(), "en"), NullTTS)
    assert isinstance(pick_tts(_settings(), "ur"), NullTTS)


# -- VoiceLoop with fake engines ------------------------------------------
def test_listen_once_returns_fake_transcript():
    stt = FakeSTT("open notepad")
    loop = _loop(stt=stt, tts=FakeTTS())
    assert loop.listen_once() == "open notepad"
    assert stt.calls  # the (empty, no-mic) audio was handed to STT
    assert loop.state.state is AssistantState.IDLE


def test_speak_calls_fake_tts_and_enters_speaking_state():
    tts = FakeTTS()
    loop = _loop(stt=FakeSTT(), tts=tts)
    loop.speak("Done, boss.")
    assert tts.spoken == ["Done, boss."]
    assert tts.speaking is True
    assert loop.state.state is AssistantState.SPEAKING
    assert "speaking" in loop.state.history[-1]


def test_interrupt_stops_speech_and_moves_to_listening():
    tts = FakeTTS()
    loop = _loop(stt=FakeSTT(), tts=tts)
    loop.speak("Done, boss.")
    assert loop.interrupt() is True
    assert tts.stop_calls == 1
    assert tts.speaking is False
    assert loop.state.state is AssistantState.LISTENING


def test_interrupt_returns_false_when_not_speaking():
    loop = _loop(stt=FakeSTT(), tts=FakeTTS())
    assert loop.state.state is AssistantState.IDLE
    assert loop.interrupt() is False
    assert loop.state.state is AssistantState.IDLE
    loop.speak("hi")
    assert loop.interrupt() is True
    assert loop.interrupt() is False  # already listening, nothing to stop


def test_speak_in_text_mode_prints_reply_prefixed(capsys):
    loop = _loop(stt=NullSTT(), tts=NullTTS())
    assert loop.text_mode is True
    loop.speak("Done, boss.")
    assert "Kairo: Done, boss." in capsys.readouterr().out
    assert loop.state.state is AssistantState.SPEAKING


def test_defaults_fall_back_to_null_adapters_when_nothing_installed():
    loop = _loop()
    assert isinstance(loop.stt, NullSTT)
    assert isinstance(loop.tts, NullTTS)
    assert isinstance(loop.wake, NullWakeWord)
    assert loop.text_mode is True


# -- text-mode run() --------------------------------------------------------
def test_run_text_mode_handles_one_line_and_quits(monkeypatch, capsys):
    boss = FakeBoss()
    loop = _loop(boss=boss, stt=NullSTT(), tts=NullTTS())
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\nquit\n"))
    loop.run()
    assert boss.handled == ["hello"]
    out = capsys.readouterr().out
    assert "Kairo: Got it, boss: hello" in out
    assert "Goodbye, boss." in out
    assert loop.state.state is AssistantState.IDLE
    # Full turn drove the state machine: listening -> thinking -> speaking -> idle.
    assert any("listening -> thinking" in h for h in loop.state.history)
    assert any("thinking -> speaking" in h for h in loop.state.history)
    assert any("speaking -> idle" in h for h in loop.state.history)


def test_run_text_mode_quits_on_eof(monkeypatch, capsys):
    loop = _loop(stt=NullSTT(), tts=NullTTS())
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    loop.run()  # EOF ends the loop instead of hanging
    assert loop.state.state is AssistantState.IDLE


def test_run_text_mode_ignores_blank_lines(monkeypatch):
    boss = FakeBoss()
    loop = _loop(boss=boss, stt=NullSTT(), tts=NullTTS())
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n   \nquit\n"))
    loop.run()
    assert boss.handled == []


# -- hands-free run() ------------------------------------------------------
class FakeWake:
    """Scripted wake word: returns the given answers, then raises
    KeyboardInterrupt so the loop exits (like the boss pressing Ctrl+C)."""

    def __init__(self, answers: list[bool]):
        self.answers = list(answers)
        self.calls = 0

    def available(self) -> bool:
        return True

    def listen_once(self, timeout: float = 10.0) -> bool:
        self.calls += 1
        if not self.answers:
            raise KeyboardInterrupt
        return self.answers.pop(0)


class ScriptSTT:
    """STT that returns one canned transcript per call, then ''."""

    def __init__(self, transcripts: list[str]):
        self.transcripts = list(transcripts)
        self.calls: list[bytes] = []

    def available(self) -> bool:
        return True

    def transcribe(self, audio: bytes) -> str:
        self.calls.append(audio)
        return self.transcripts.pop(0) if self.transcripts else ""


def test_run_hands_free_does_one_turn_then_quits_on_quit_word(capsys):
    boss = FakeBoss()
    tts = FakeTTS()
    loop = _loop(
        boss=boss,
        stt=ScriptSTT(["open notepad", "quit"]),
        tts=tts,
        wake=FakeWake([True, True]),
    )
    loop.run_hands_free()
    assert boss.handled == ["open notepad"]
    assert "Got it, boss: open notepad" in tts.spoken
    assert "Goodbye, boss." in capsys.readouterr().out
    assert loop.state.state is AssistantState.IDLE


def test_run_hands_free_keeps_waiting_through_timeouts():
    boss = FakeBoss()
    loop = _loop(
        boss=boss,
        stt=ScriptSTT(["hello"]),
        tts=FakeTTS(),
        wake=FakeWake([False, False, True]),
    )
    loop.run_hands_free()  # third wake hit, then KeyboardInterrupt ends it
    assert boss.handled == ["hello"]


def test_run_hands_free_ignores_empty_utterances():
    boss = FakeBoss()
    loop = _loop(
        boss=boss,
        stt=ScriptSTT(["", "what time is it"]),
        tts=FakeTTS(),
        wake=FakeWake([True, True]),
    )
    loop.run_hands_free()
    assert boss.handled == ["what time is it"]


def test_run_hands_free_raises_without_a_wake_engine():
    loop = _loop(stt=FakeSTT(), tts=FakeTTS(), wake=NullWakeWord())
    with pytest.raises(NotAvailable):
        loop.run_hands_free()


def test_run_hands_free_raises_without_audio_engines():
    loop = _loop(stt=NullSTT(), tts=NullTTS(), wake=FakeWake([True]))
    with pytest.raises(NotAvailable):
        loop.run_hands_free()


# -- pick_tts ---------------------------------------------------------------
def _stub_module(monkeypatch, name: str):
    module = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_pick_tts_returns_piper_for_english(monkeypatch):
    _stub_module(monkeypatch, "piper")
    monkeypatch.delitem(sys.modules, "edge_tts", raising=False)
    chosen = pick_tts(_settings(language="en"), "en")
    assert isinstance(chosen, PiperTTS)


def test_pick_tts_returns_edge_tts_for_roman_urdu(monkeypatch):
    _stub_module(monkeypatch, "piper")
    _stub_module(monkeypatch, "edge_tts")
    chosen = pick_tts(_settings(language="ur"), "ur")
    assert isinstance(chosen, EdgeTTS)
    assert chosen.voice == "ur-PK-AsadNeural"


def test_pick_tts_urdu_falls_back_to_piper_without_edge_tts(monkeypatch):
    _stub_module(monkeypatch, "piper")
    monkeypatch.delitem(sys.modules, "edge_tts", raising=False)
    chosen = pick_tts(_settings(language="ur"), "ur")
    assert isinstance(chosen, PiperTTS)


def test_pick_tts_english_falls_back_to_edge_tts_without_piper(monkeypatch):
    monkeypatch.delitem(sys.modules, "piper", raising=False)
    _stub_module(monkeypatch, "edge_tts")
    chosen = pick_tts(_settings(language="en"), "en")
    assert isinstance(chosen, EdgeTTS)
    assert chosen.voice == "en-US-AriaNeural"


def test_pick_tts_uses_settings_language_when_no_language_given(monkeypatch):
    _stub_module(monkeypatch, "piper")
    _stub_module(monkeypatch, "edge_tts")
    assert isinstance(pick_tts(_settings(language="ur")), EdgeTTS)
    assert isinstance(pick_tts(_settings(language="en")), PiperTTS)
