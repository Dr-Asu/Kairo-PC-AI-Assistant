"""Tests for the File Organizer (copy / move / rename / arrange).

All files live under tmp_path only — never real user files.
Run with: .venv/bin/pytest -q
"""

import pytest

from kairo.safety import ActionKind
from kairo.workers.base import TaskContext
from kairo.workers.files import FileOrganizer


def _file(path, content="hello"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# -- copy --------------------------------------------------------------------
def test_copy_works_and_creates_parent_dirs(tmp_path):
    src = _file(tmp_path / "a.txt", "data")
    dst = tmp_path / "sub" / "deep" / "b.txt"
    out = FileOrganizer().copy(str(src), str(dst))
    assert out == str(dst)
    assert dst.read_text() == "data"
    assert src.exists()  # copy leaves the original


def test_copy_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileOrganizer().copy(str(tmp_path / "nope.txt"), str(tmp_path / "x.txt"))


# -- move --------------------------------------------------------------------
def test_move_works(tmp_path):
    src = _file(tmp_path / "a.txt")
    dst = tmp_path / "moved.txt"
    out = FileOrganizer().move(str(src), str(dst))
    assert out == str(dst)
    assert dst.exists()
    assert not src.exists()


def test_move_without_confirmation_refuses_to_overwrite(tmp_path):
    src = _file(tmp_path / "a.txt", "new")
    dst = _file(tmp_path / "b.txt", "original — must survive")
    with pytest.raises(PermissionError):
        FileOrganizer().move(str(src), str(dst))
    # Nothing was overwritten and nothing was lost.
    assert dst.read_text() == "original — must survive"
    assert src.exists()


def test_move_with_confirmation_yes_overwrites(tmp_path):
    src = _file(tmp_path / "a.txt", "new")
    dst = _file(tmp_path / "b.txt", "old")
    out = FileOrganizer().move(str(src), str(dst), confirm=lambda c: True)
    assert dst.read_text() == "new"
    assert not src.exists()


def test_move_with_confirmation_no_refuses(tmp_path):
    src = _file(tmp_path / "a.txt", "new")
    dst = _file(tmp_path / "b.txt", "old")
    with pytest.raises(PermissionError):
        FileOrganizer().move(str(src), str(dst), confirm=lambda c: False)
    assert dst.read_text() == "old"
    assert src.exists()


# -- arrange -----------------------------------------------------------------
def test_arrange_sorts_by_type_into_subfolders(tmp_path):
    _file(tmp_path / "report.docx")
    _file(tmp_path / "notes.txt")
    _file(tmp_path / "photo.jpg")
    _file(tmp_path / "budget.xlsx")
    _file(tmp_path / "mystery.xyz")
    moved = FileOrganizer().arrange(str(tmp_path), by="type")
    assert moved["Documents"] == ["notes.txt", "report.docx"]
    assert moved["Images"] == ["photo.jpg"]
    assert moved["Sheets"] == ["budget.xlsx"]
    assert moved["Other"] == ["mystery.xyz"]
    assert (tmp_path / "Documents" / "report.docx").exists()
    assert (tmp_path / "Images" / "photo.jpg").exists()
    assert not (tmp_path / "report.docx").exists()


def test_arrange_by_date_uses_yyyy_mm_folders(tmp_path):
    import os
    from datetime import datetime
    f = _file(tmp_path / "old.txt")
    stamp = datetime(2026, 3, 15, 12, 0).timestamp()
    os.utime(f, (stamp, stamp))
    moved = FileOrganizer().arrange(str(tmp_path), by="date")
    assert moved == {"2026-03": ["old.txt"]}
    assert (tmp_path / "2026-03" / "old.txt").exists()


def test_arrange_skips_files_already_in_place(tmp_path):
    _file(tmp_path / "Documents" / "done.txt")
    moved = FileOrganizer().arrange(str(tmp_path), by="type")
    assert moved == {}  # the only file is already in its subfolder


def test_arrange_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError):
        FileOrganizer().arrange(str(tmp_path), by="colour")


# -- run() --------------------------------------------------------------------
def test_run_copy_files(tmp_path):
    src = _file(tmp_path / "a.txt")
    ctx = TaskContext(text="copy it", slots={
        "intent": "copy_files", "src": str(src),
        "dst": str(tmp_path / "copy.txt")})
    result = FileOrganizer().run(ctx)
    assert result.ok is True
    assert (tmp_path / "copy.txt").exists()


def test_run_move_without_confirm_callable_asks_first(tmp_path):
    src = _file(tmp_path / "a.txt")
    ctx = TaskContext(text="move it", slots={
        "intent": "move_files", "src": str(src),
        "dst": str(tmp_path / "moved.txt")})
    result = FileOrganizer().run(ctx)
    assert result.ok is False
    assert result.needs_confirmation is not None
    assert result.needs_confirmation.kind is ActionKind.MOVE
    assert src.exists()  # nothing moved silently


def test_run_move_with_yes_moves(tmp_path):
    src = _file(tmp_path / "a.txt")
    ctx = TaskContext(text="move it", slots={
        "intent": "move_files", "src": str(src),
        "dst": str(tmp_path / "moved.txt")},
        confirm=lambda c: True)
    result = FileOrganizer().run(ctx)
    assert result.ok is True
    assert (tmp_path / "moved.txt").exists()
    assert not src.exists()


def test_run_move_with_no_refuses(tmp_path):
    src = _file(tmp_path / "a.txt")
    ctx = TaskContext(text="move it", slots={
        "intent": "move_files", "src": str(src),
        "dst": str(tmp_path / "moved.txt")},
        confirm=lambda c: False)
    result = FileOrganizer().run(ctx)
    assert result.ok is False
    assert src.exists()


def test_run_arrange_folder(tmp_path):
    _file(tmp_path / "pic.png")
    ctx = TaskContext(text="tidy up", slots={
        "intent": "arrange_folder", "folder": str(tmp_path), "by": "type"})
    result = FileOrganizer().run(ctx)
    assert result.ok is True
    assert (tmp_path / "Images" / "pic.png").exists()


def test_run_rename_file(tmp_path):
    src = _file(tmp_path / "old.txt")
    ctx = TaskContext(text="rename it", slots={
        "intent": "rename_file", "src": str(src),
        "dst": str(tmp_path / "new.txt")},
        confirm=lambda c: True)
    result = FileOrganizer().run(ctx)
    assert result.ok is True
    assert (tmp_path / "new.txt").exists()
