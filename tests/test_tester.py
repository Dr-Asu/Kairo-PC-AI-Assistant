"""Tests for the Tester worker (verify file / Excel / script).

All files live under tmp_path only — never real user files.
Run with: .venv/bin/pytest -q
"""

import time

import pytest

from kairo.workers.base import TaskContext
from kairo.workers.office import make_excel
from kairo.workers.tester import TesterWorker, run_script, verify_excel, verify_file


# -- verify_file ---------------------------------------------------------------
def test_verify_file_true_for_nonempty_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert verify_file(str(f)) is True


def test_verify_file_false_for_missing_file(tmp_path):
    assert verify_file(str(tmp_path / "nope.txt")) is False


def test_verify_file_false_for_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    assert verify_file(str(f)) is False


# -- verify_excel ---------------------------------------------------------------
def test_verify_excel_passes_on_good_file(tmp_path):
    path = tmp_path / "good.xlsx"
    make_excel(str(path), rows=[["Apples", 10], ["Oranges", 5]],
               headers=["Item", "Qty"],
               formulas={"B4": "=SUM(B2:B3)"})
    ok, detail = verify_excel(str(path))
    assert ok is True
    assert "data row" in detail.lower()


def test_verify_excel_fails_on_formula_referencing_nonexistent_row(tmp_path):
    path = tmp_path / "bad.xlsx"
    make_excel(str(path), rows=[["Apples", 10], ["Oranges", 5]],
               headers=["Item", "Qty"],
               formulas={"B4": "=SUM(B2:B100)"})
    ok, detail = verify_excel(str(path))
    assert ok is False
    assert "100" in detail  # the bad row is named


def test_verify_excel_fails_on_headers_only(tmp_path):
    path = tmp_path / "headers.xlsx"
    make_excel(str(path), rows=[], headers=["Item", "Qty"])
    ok, detail = verify_excel(str(path))
    assert ok is False
    assert "no data" in detail.lower()


def test_verify_excel_fails_on_missing_file(tmp_path):
    ok, _ = verify_excel(str(tmp_path / "nope.xlsx"))
    assert ok is False


def test_verify_excel_fails_on_non_excel_file(tmp_path):
    f = tmp_path / "not-excel.xlsx"
    f.write_text("this is not a spreadsheet")
    ok, _ = verify_excel(str(f))
    assert ok is False


# -- run_script ------------------------------------------------------------------
def test_run_script_runs_tiny_script_and_reports_output(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("print('hello from the script')\n")
    ok, output = run_script(str(script))
    assert ok is True
    assert "hello from the script" in output


def test_run_script_reports_failing_script(tmp_path):
    script = tmp_path / "boom.py"
    script.write_text("import sys; print('oops'); sys.exit(3)\n")
    ok, output = run_script(str(script))
    assert ok is False
    assert "3" in output
    assert "oops" in output


def test_run_script_times_out_on_sleeping_script(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30); print('woke')\n")
    start = time.monotonic()
    ok, output = run_script(str(script), timeout=1)
    elapsed = time.monotonic() - start
    assert ok is False
    assert "timed out" in output.lower()
    assert elapsed < 10  # killed promptly, did not wait 30s


def test_run_script_missing_file(tmp_path):
    ok, _ = run_script(str(tmp_path / "nope.py"))
    assert ok is False


# -- TesterWorker.run -------------------------------------------------------------
def test_run_verify_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    ctx = TaskContext(text="check it", slots={"kind": "file", "path": str(f)})
    result = TesterWorker().run(ctx)
    assert result.ok is True


def test_run_verify_missing_file_fails(tmp_path):
    ctx = TaskContext(text="check it", slots={
        "kind": "file", "path": str(tmp_path / "nope.txt")})
    assert TesterWorker().run(ctx).ok is False


def test_run_verify_excel(tmp_path):
    path = tmp_path / "good.xlsx"
    make_excel(str(path), rows=[[1, 2]], headers=["A", "B"],
               formulas={"B3": "=SUM(B2:B2)"})
    ctx = TaskContext(text="check the sheet", slots={
        "kind": "excel", "path": str(path)})
    assert TesterWorker().run(ctx).ok is True


def test_run_verify_bad_excel_fails(tmp_path):
    path = tmp_path / "bad.xlsx"
    make_excel(str(path), rows=[[1, 2]], headers=["A", "B"],
               formulas={"B3": "=SUM(B2:B99)"})
    ctx = TaskContext(text="check the sheet", slots={
        "kind": "excel", "path": str(path)})
    result = TesterWorker().run(ctx)
    assert result.ok is False


def test_run_script_check(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("print('ok')\n")
    ctx = TaskContext(text="run it", slots={"kind": "script", "path": str(script)})
    result = TesterWorker().run(ctx)
    assert result.ok is True
    assert "ok" in result.summary


def test_run_without_path_fails():
    ctx = TaskContext(text="check it", slots={"kind": "file"})
    assert TesterWorker().run(ctx).ok is False
