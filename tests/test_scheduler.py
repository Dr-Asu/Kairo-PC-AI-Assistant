"""Tests for the scheduler worker (Builder D).

Tests use tmp_path for the data dir and never touch the real ~/.kairo.
No network is used. Run with: .venv/bin/pytest -q
"""

import time
from datetime import date

import pytest

from kairo.memory import MemoryStore
from kairo.workers.base import TaskContext, WorkerResult
from kairo.workers.scheduler import SchedulerWorker


@pytest.fixture
def memory(tmp_path):
    mem = MemoryStore(tmp_path)
    mem.add_routine("morning mode", [
        {"text": "open chrome"},
        {"text": "open excel"},
    ])
    return mem


@pytest.fixture
def worker(memory):
    return SchedulerWorker(memory=memory)


# -- timers ---------------------------------------------------------------
def test_start_timer_returns_message(worker):
    assert worker.start_timer(10) == "Timer set for 10 minutes, boss."


def test_start_timer_message_uses_minutes(worker):
    assert worker.start_timer(1) == "Timer set for 1 minutes, boss."


def test_timer_fires_and_records_note(worker):
    worker.start_timer(0)
    time.sleep(0.3)
    assert worker.last_timer_note is not None
    assert "Timer done" in worker.last_timer_note
    assert "boss" in worker.last_timer_note.lower()


def test_timer_speaks_when_callback_available(memory):
    spoken = []
    w = SchedulerWorker(memory=memory, speak=spoken.append)
    w.start_timer(0)
    time.sleep(0.3)
    assert spoken and "Timer done" in spoken[0]


# -- routines -------------------------------------------------------------
def test_run_routine_runs_two_steps(worker):
    summary = worker.run_routine("morning mode")
    assert "morning mode" in summary
    assert "open chrome" in summary
    assert "open excel" in summary
    assert "2 step" in summary


def test_run_routine_missing(worker):
    summary = worker.run_routine("no such routine")
    assert "could not find" in summary
    assert "no such routine" in summary


def test_run_routine_uses_boss_callback(memory):
    called = []

    def fake_boss(step_text):
        called.append(step_text)
        return f"did: {step_text}"

    w = SchedulerWorker(memory=memory, boss=fake_boss)
    summary = w.run_routine("morning mode")
    assert called == ["open chrome", "open excel"]
    assert "did: open chrome" in summary


# -- briefings ------------------------------------------------------------
def test_morning_briefing_mentions_boss_and_today(memory):
    today = date.today().isoformat()
    memory.add_job("AI Engineer", "ImagineArt", deadline=today)
    memory.log_task("prepare CV", status="open")
    w = SchedulerWorker(memory=memory)
    text = w.morning_briefing()
    assert text.startswith("Good morning, boss.")
    assert "ImagineArt" in text
    assert "AI Engineer" in text
    assert "prepare CV" in text
    assert "Weather: check Open-Meteo when online" in text


def test_morning_briefing_no_memory():
    text = SchedulerWorker().morning_briefing()
    assert text.startswith("Good morning, boss.")
    assert "Weather: check Open-Meteo when online" in text


def test_evening_briefing_mentions_boss_and_today(memory):
    memory.log_task("finished the report")
    memory.log_usage(date.today().isoformat(), "Chrome", 45)
    memory.log_task("waiting on reply", status="open")
    w = SchedulerWorker(memory=memory)
    text = w.evening_briefing()
    assert text.startswith("Good evening, boss.")
    assert "finished the report" in text
    assert "45" in text
    assert "waiting on reply" in text


def test_evening_briefing_no_memory():
    text = SchedulerWorker().evening_briefing()
    assert text.startswith("Good evening, boss.")


# -- dispatch -------------------------------------------------------------
def test_run_dispatches_timer(worker, memory):
    ctx = TaskContext(
        text="timer for 10 minutes",
        slots={"intent": "start_timer", "minutes": "10"},
        memory=memory,
    )
    result = worker.run(ctx)
    assert isinstance(result, WorkerResult)
    assert result.ok
    assert result.summary == "Timer set for 10 minutes, boss."


def test_run_dispatches_morning_briefing(worker, memory):
    ctx = TaskContext(
        text="morning briefing",
        slots={"intent": "morning_briefing"},
        memory=memory,
    )
    result = worker.run(ctx)
    assert result.ok
    assert result.summary.startswith("Good morning, boss.")


def test_run_dispatches_routine(worker, memory):
    ctx = TaskContext(
        text="run morning mode routine",
        slots={"intent": "run_routine", "routine": "morning mode"},
        memory=memory,
    )
    result = worker.run(ctx)
    assert result.ok
    assert "open chrome" in result.summary


def test_run_dispatches_evening_briefing(worker, memory):
    ctx = TaskContext(
        text="evening briefing",
        slots={"intent": "evening_briefing"},
        memory=memory,
    )
    result = worker.run(ctx)
    assert result.ok
    assert result.summary.startswith("Good evening, boss.")


def test_run_unknown_intent_fails(worker, memory):
    ctx = TaskContext(text="do something weird", slots={}, memory=memory)
    result = worker.run(ctx)
    assert not result.ok


# -- job-hunt drafting (drafts only) --------------------------------------
def test_draft_application_contains_company(memory):
    jid = memory.add_job("AI Engineer", "ImagineArt", deadline="2026-11-26")
    w = SchedulerWorker(memory=memory)
    draft = w.draft_application(jid, "BS Computer Science, CNN brain-tumor project")
    assert "ImagineArt" in draft
    assert "AI Engineer" in draft


def test_draft_application_contains_no_sending(memory):
    jid = memory.add_job("AI Engineer", "ImagineArt", deadline="2026-11-26")
    w = SchedulerWorker(memory=memory)
    draft = w.draft_application(jid, "BS Computer Science")
    lowered = draft.lower()
    assert "submit" not in lowered
    assert "send" not in lowered
    assert "sent" not in lowered


def test_draft_application_marks_itself_a_draft(memory):
    jid = memory.add_job("AI Engineer", "ImagineArt")
    w = SchedulerWorker(memory=memory)
    draft = w.draft_application(jid, "BS Computer Science")
    assert "draft" in draft.lower()


def test_no_submit_or_send_method_on_worker(worker):
    assert not hasattr(worker, "submit_application")
    assert not hasattr(worker, "send_application")
    assert not hasattr(worker, "submit_job")
    assert not hasattr(worker, "send_job")
