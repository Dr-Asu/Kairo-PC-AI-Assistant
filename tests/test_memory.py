"""Tests for the memory store (Builder D).

Tests use tmp_path for the data dir and never touch the real ~/.kairo.
Run with: .venv/bin/pytest -q
"""

from datetime import date, timedelta

import pytest

from kairo.brain import BrainError, BrainReply, LocalBrain
from kairo.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


# -- facts ----------------------------------------------------------------
def test_add_and_get_fact(store):
    store.add_fact("boss_name", "Muhammad Asif", source="cv")
    assert store.get_fact("boss_name") == "Muhammad Asif"
    assert store.get_fact("missing_key") is None


def test_add_fact_upserts(store):
    store.add_fact("city", "Multan")
    store.add_fact("city", "Lahore")
    assert store.get_fact("city") == "Lahore"
    assert len(store.search_facts("Lahore")) == 1


def test_search_facts_finds_word_inside_value(store):
    store.add_fact("job", "IT Officer at TABZ Logistics in Multan")
    store.add_fact("hobby", "plays cricket on weekends")
    results = store.search_facts("TABZ")
    assert len(results) == 1
    assert results[0]["key"] == "job"
    assert "TABZ" in results[0]["value"]


def test_search_facts_by_key_word(store):
    store.add_fact("favourite_food", "biryani")
    results = store.search_facts("favourite")
    assert any(r["key"] == "favourite_food" for r in results)


def test_search_facts_no_match(store):
    store.add_fact("city", "Multan")
    assert store.search_facts("zzzqqq") == []


def test_search_facts_empty_query(store):
    assert store.search_facts("") == []
    assert store.search_facts("   ") == []


# -- tasks ----------------------------------------------------------------
def test_log_and_list_tasks_newest_first(store):
    first = store.log_task("open excel")
    second = store.log_task("check email")
    assert isinstance(first, int) and isinstance(second, int) and second > first
    tasks = store.list_tasks()
    assert [t["text"] for t in tasks] == ["check email", "open excel"]
    assert all(t["status"] == "done" for t in tasks)


def test_list_tasks_status_filter(store):
    store.log_task("done thing", status="done")
    store.log_task("waiting thing", status="open")
    done = store.list_tasks(status="done")
    assert [t["text"] for t in done] == ["done thing"]
    open_tasks = store.list_tasks(status="open")
    assert [t["text"] for t in open_tasks] == ["waiting thing"]


def test_list_tasks_limit(store):
    for i in range(5):
        store.log_task(f"task {i}")
    assert len(store.list_tasks(limit=2)) == 2


# -- learnings ------------------------------------------------------------
def test_learning_approve_keep(store):
    lid = store.add_learning("Boss prefers PDFs in landscape", kind="preference")
    assert store.list_learnings()[0]["approved"] == 0
    store.approve_learning(lid, keep=True)
    assert store.list_learnings()[0]["approved"] == 1


def test_learning_approve_delete(store):
    lid = store.add_learning("something wrong")
    store.approve_learning(lid, keep=False)
    assert store.list_learnings() == []


def test_learning_delete(store):
    lid = store.add_learning("to be deleted")
    store.delete_learning(lid)
    assert store.list_learnings() == []


def test_list_learnings_newest_first(store):
    store.add_learning("first")
    store.add_learning("second")
    assert [l["text"] for l in store.list_learnings()] == ["second", "first"]


# -- usage ----------------------------------------------------------------
def test_usage_logging_adds_minutes(store):
    today = date.today().isoformat()
    store.log_usage(today, "Chrome", 30)
    store.log_usage(today, "Chrome", 15)
    store.log_usage(today, "Word", 10)
    assert store.usage_today() == {"Chrome": 45, "Word": 10}


def test_usage_today_ignores_other_days(store):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.log_usage(yesterday, "Chrome", 120)
    store.log_usage(date.today().isoformat(), "Excel", 5)
    assert store.usage_today() == {"Excel": 5}


# -- routines -------------------------------------------------------------
def test_routines_round_trip(store):
    steps = [{"text": "open chrome"}, {"text": "open excel"}]
    store.add_routine("morning mode", steps)
    assert store.get_routine("morning mode") == steps
    assert store.list_routines() == ["morning mode"]


def test_get_routine_missing_returns_none(store):
    assert store.get_routine("no such routine") is None


def test_routine_overwrite(store):
    store.add_routine("r", [{"text": "a"}])
    store.add_routine("r", [{"text": "b"}, {"text": "c"}])
    assert store.get_routine("r") == [{"text": "b"}, {"text": "c"}]


# -- jobs -----------------------------------------------------------------
def test_jobs_add_list_update(store):
    jid = store.add_job("AI Engineer", "ImagineArt", deadline="2026-11-26")
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "AI Engineer"
    assert jobs[0]["company"] == "ImagineArt"
    assert jobs[0]["deadline"] == "2026-11-26"
    assert jobs[0]["status"] == "open"
    store.update_job(jid, status="applied")
    assert store.list_jobs(status="applied")[0]["id"] == jid
    assert store.list_jobs(status="open") == []
    store.update_job(jid, title="Senior AI Engineer", company="ImagineArt")
    assert store.list_jobs()[0]["title"] == "Senior AI Engineer"


def test_jobs_default_deadline_empty(store):
    jid = store.add_job("Dev", "Acme")
    assert store.list_jobs()[0]["deadline"] == ""
    assert isinstance(jid, int)


# -- nightly learning job -------------------------------------------------
def test_run_learning_job_repeated_task_writes_learning(store):
    store.log_task("open excel and check stock")
    store.log_task("open excel and check stock")
    written = store.run_learning_job(LocalBrain())
    assert written == 1
    learnings = store.list_learnings()
    assert len(learnings) == 1
    assert "routine" in learnings[0]["text"].lower()
    assert "open excel and check stock" in learnings[0]["text"]


def test_run_learning_job_one_off_writes_nothing(store):
    store.log_task("do a one-off thing")
    assert store.run_learning_job(LocalBrain()) == 0
    assert store.list_learnings() == []


def test_run_learning_job_no_duplicates(store):
    store.log_task("open excel and check stock")
    store.log_task("open excel and check stock")
    assert store.run_learning_job(LocalBrain()) == 1
    assert store.run_learning_job(LocalBrain()) == 0
    assert len(store.list_learnings()) == 1


def test_run_learning_job_format_preference(store):
    store.log_task("make the report in pdf format")
    assert store.run_learning_job(LocalBrain()) == 1
    assert "pdf" in store.list_learnings()[0]["text"].lower()


def test_run_learning_job_no_tasks(store):
    assert store.run_learning_job(LocalBrain()) == 0


class _FakeCloudBrain:
    name = "fake-cloud"

    def complete(self, system, user):
        return BrainReply(
            text="Boss prefers morning briefings\nBoss likes Excel reports",
            model="fake-cloud/test",
        )


def test_run_learning_job_with_cloud_brain(store):
    store.log_task("check email")
    written = store.run_learning_job(_FakeCloudBrain())
    assert written == 2
    texts = [l["text"] for l in store.list_learnings()]
    assert "Boss prefers morning briefings" in texts
    assert "Boss likes Excel reports" in texts


class _FailingBrain:
    name = "failing-cloud"

    def complete(self, system, user):
        raise BrainError("cloud unreachable")


def test_run_learning_job_brain_error_falls_back_to_heuristics(store):
    store.log_task("open excel and check stock")
    store.log_task("open excel and check stock")
    written = store.run_learning_job(_FailingBrain())
    assert written == 1
    assert "routine" in store.list_learnings()[0]["text"].lower()
