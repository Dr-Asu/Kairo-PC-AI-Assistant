"""Tests for opening files by name — the v0.3.1 file-open fix.

Matching and search run everywhere; launching a real file needs a
Windows desktop (or xdg-open on Linux) and is covered by the dry-run
paths where possible.
"""

from pathlib import Path

import pytest

from kairo.file_open import find_best, find_files, score_file, search_roots
from kairo.intents import default_registry
from kairo.workers.base import TaskContext
from kairo.workers.files import FileOrganizer


def _make_home(tmp_path: Path) -> Path:
    downloads = tmp_path / "Downloads"
    desktop = tmp_path / "Desktop"
    downloads.mkdir()
    desktop.mkdir()
    (downloads / "TABZ-TV-Dashboard-Complete-Report-v2.7.21.pdf").write_text("pdf")
    (downloads / "TABZ-TV-Footer-Fix-Report-v2.7.21.pdf").write_text("pdf")
    (downloads / "Staff_Attendance_Sheet_TABZ.xlsx").write_text("xls")
    (desktop / "notes.txt").write_text("hi")
    return tmp_path


def test_score_counts_matching_words():
    p = Path("TABZ-TV-Dashboard-Complete-Report-v2.7.21.pdf")
    assert score_file("tabz dashboard report", p) == 3
    assert score_file("tabz", p) == 1
    assert score_file("nothing here", p) == 0
    assert score_file("", p) == 0


def test_find_best_picks_the_report(tmp_path):
    home = _make_home(tmp_path)
    best, ties = find_best("tabz dashboard report", home=home)
    assert best is not None and best.name.startswith("TABZ-TV-Dashboard-Complete")
    assert ties == []


def test_find_best_ties_when_both_match_equally(tmp_path):
    home = _make_home(tmp_path)
    best, ties = find_best("tabz report", home=home)
    assert best is not None
    assert len(ties) == 1  # both TABZ reports score 2


def test_find_best_returns_none_when_nothing_matches(tmp_path):
    home = _make_home(tmp_path)
    best, ties = find_best("definitely not a real file xyz", home=home)
    assert best is None and ties == []


def test_folder_hint_restricts_the_search(tmp_path):
    home = _make_home(tmp_path)
    best, _ = find_best("notes", folder_hint="downloads", home=home)
    assert best is None
    best, _ = find_best("notes", folder_hint="desktop", home=home)
    assert best is not None and best.name == "notes.txt"


def test_search_roots_skips_missing_folders(tmp_path):
    roots = search_roots(home=tmp_path)
    assert roots == []  # no Downloads/Desktop/Documents under tmp_path


def test_find_files_sorted_best_first(tmp_path):
    home = _make_home(tmp_path)
    matches = find_files("tabz", home=home)
    scores = [s for s, _ in matches]
    assert scores == sorted(scores, reverse=True)
    assert {p.name for _, p in matches} == {
        "TABZ-TV-Dashboard-Complete-Report-v2.7.21.pdf",
        "TABZ-TV-Footer-Fix-Report-v2.7.21.pdf",
        "Staff_Attendance_Sheet_TABZ.xlsx",
    }


# ------------------------------------------------------------------ intents
def test_open_file_intent_wins_over_open_app():
    reg = default_registry()
    m = reg.match("open tabz dashboard report it is in the download folder of my pc")
    assert m is not None and m.name == "open_file"
    assert m.slots["file"] == "tabz dashboard report"
    assert m.slots["folder"] == "download"
    m = reg.match("open tabz dashboard report in downloads")
    assert m is not None and m.name == "open_file"
    assert m.slots["file"] == "tabz dashboard report"
    m = reg.match("open report.pdf")
    assert m is not None and m.name == "open_file"
    assert m.slots["file"] == "report.pdf"
    m = reg.match("open the attendance sheet file")
    assert m is not None and m.name == "open_file"


def test_open_app_still_matches_plain_app_names():
    reg = default_registry()
    assert reg.match("open chrome").name == "open_app"
    assert reg.match("open notepad").name == "open_app"
    assert reg.match("kholo excel").name == "open_app"


# ------------------------------------------------------------- files worker
def _ctx(**slots) -> TaskContext:
    return TaskContext(text="open file", slots=slots, dry_run=True)


def test_files_worker_reads_boss_intent_slot(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    worker = FileOrganizer()
    result = worker.run(
        _ctx(_intent="open_file", file="tabz dashboard report", folder="downloads")
    )
    assert result.ok is True
    assert "TABZ-TV-Dashboard-Complete-Report" in result.summary


def test_files_worker_reports_missing_file_plainly(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    worker = FileOrganizer()
    result = worker.run(_ctx(_intent="open_file", file="birthday party photos"))
    assert result.ok is False
    assert "birthday party photos" in result.summary.lower()
    assert "could not find" in result.summary.lower()


def test_files_worker_asks_when_two_files_tie(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    worker = FileOrganizer()
    result = worker.run(_ctx(_intent="open_file", file="tabz report"))
    assert result.ok is False
    assert "more than one match" in result.summary


def test_files_worker_falls_back_to_app_when_no_file(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    worker = FileOrganizer()
    # "open excel file": no file called excel, but Excel is an app —
    # dry run reports the would-be open instead of failing.
    result = worker.run(_ctx(_intent="open_file", file="excel"))
    assert result.ok is True
    assert "dry run" in result.summary.lower()


# ------------------------------------------------------------ boss fallback
def test_boss_open_app_falls_back_to_a_file(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    from kairo.boss import _SystemWorker
    from kairo.pc.controller import NotAvailable

    worker = _SystemWorker()
    calls = {"n": 0}

    class FakeController:
        def open_app(self, name):
            calls["n"] += 1
            raise NotAvailable(f"no app {name}")

    monkeypatch.setattr(
        "kairo.pc.controller.SystemController", lambda **kw: FakeController()
    )
    monkeypatch.setattr(
        "kairo.file_open.open_file",
        lambda p, timeout=5.0: f"Opened {Path(p).name}",
    )
    result = worker.run(
        TaskContext(
            text="open tabz dashboard report",
            slots={"_intent": "open_app", "app": "tabz dashboard report"},
            dry_run=False,
        )
    )
    assert calls["n"] == 1
    assert result.ok is True
    assert "TABZ-TV-Dashboard-Complete-Report" in result.summary
