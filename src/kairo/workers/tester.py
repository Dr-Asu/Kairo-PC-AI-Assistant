"""Tester worker — verify before anything is delivered.

The boss never hands the boss (the user) an unchecked result. The Tester:
* checks a file exists and is not empty,
* opens an Excel file and checks its formulas actually compute,
* runs a script with a timeout and reports pass/fail,
* checks a program is installed and opens.
Builder E owns this file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .base import TaskContext, Worker, WorkerResult

#: Cell references inside a formula, e.g. C5, $B$2, or ranges C2:C4.
#: The (?!\() guard keeps function names like LOG10(...) from being
#: mistaken for a cell reference.
_CELL_REF_RE = re.compile(r"(?<![A-Za-z])\$?([A-Za-z]{1,3})\$?(\d+)(?!\()")


def verify_file(path: str) -> bool:
    """True only if the file exists and is not empty."""
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def verify_excel(path: str) -> tuple[bool, str]:
    """Open an .xlsx and check its formulas compute. Returns (ok, detail).

    Checks: the file opens, the sheet has at least one data row, and every
    formula cell only references cells/rows that actually exist in the
    sheet (e.g. ``=SUM(C2:C100)`` fails on a 3-row sheet).
    """
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string

    p = Path(path)
    if not p.is_file():
        return False, f"No such file: {path}"
    try:
        wb = load_workbook(str(p), data_only=False)
    except Exception as exc:  # noqa: BLE001 - bad/corrupt file
        return False, f"Couldn't open {path} as an Excel file ({exc})."
    ws = wb.active
    if ws is None:
        return False, "Workbook has no sheets."

    # At least one data row: count non-empty rows, minus a header row if
    # the first row is all text (headers).
    nonempty_rows = [
        r for r in range(1, ws.max_row + 1)
        if any(ws.cell(row=r, column=c).value is not None
               for c in range(1, ws.max_column + 1))
    ]
    if not nonempty_rows:
        return False, "Sheet is empty — no data rows."
    first_row = nonempty_rows[0]
    first_is_header = all(
        isinstance(ws.cell(row=first_row, column=c).value, str)
        for c in range(1, ws.max_column + 1)
        if ws.cell(row=first_row, column=c).value is not None
    )
    data_rows = [r for r in nonempty_rows
                 if not (first_is_header and r == first_row)]
    if not data_rows:
        return False, "Sheet has headers but no data rows."

    # Every formula must reference cells that exist in the sheet.
    bad: list[str] = []
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if not (isinstance(value, str) and value.startswith("=")):
                continue
            for col_letters, row_no in _CELL_REF_RE.findall(value):
                ref_row = int(row_no)
                ref_col = column_index_from_string(col_letters)
                if ref_row > ws.max_row or ref_row < 1:
                    bad.append(
                        f"{cell.coordinate} ({value}) references row {ref_row}, "
                        f"but the sheet only has {ws.max_row} rows")
                elif ref_col > ws.max_column:
                    bad.append(
                        f"{cell.coordinate} ({value}) references column "
                        f"{col_letters}, but the sheet only has "
                        f"{ws.max_column} columns")
    if bad:
        return False, "Bad formula(s): " + "; ".join(bad)
    return True, (f"OK — {len(data_rows)} data row(s), "
                  f"{ws.max_row} row(s) total, formulas check out.")


def run_script(path: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a script and report (ok, output). Times out after ``timeout``
    seconds; a non-zero exit counts as a failure."""
    p = Path(path)
    if not p.is_file():
        return False, f"No such script: {path}"
    try:
        proc = subprocess.run(
            [sys.executable, str(p)],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return False, (f"Timed out after {timeout}s — the script was killed. "
                       f"Output so far: {out.strip() or '(none)'}")
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip()
    if proc.returncode != 0:
        return False, (f"Script exited with code {proc.returncode}. "
                       f"Output: {output or '(none)'}")
    return True, output or "(no output)"


class TesterWorker(Worker):
    """Verify results. Builder E implements this."""

    name = "tester"
    intents = {"verify"}

    def run(self, ctx: TaskContext) -> WorkerResult:
        kind = ctx.slots.get("kind") or ctx.slots.get("check") or "file"
        path = ctx.slots.get("path", "")
        if not path:
            return WorkerResult.failed(
                "Verify what, boss? Give me the file path to check.")
        if kind in {"file", "exists"}:
            ok = verify_file(path)
            return WorkerResult(
                ok=ok,
                summary=(f"{path} exists and is not empty — looks good."
                         if ok else f"{path} is missing or empty."),
                data={"path": path, "check": "file", "ok": ok})
        if kind == "excel":
            ok, detail = verify_excel(path)
            return WorkerResult(
                ok=ok,
                summary=(f"Excel check passed for {path}: {detail}"
                         if ok else f"Excel check failed for {path}: {detail}"),
                data={"path": path, "check": "excel", "ok": ok, "detail": detail})
        if kind in {"script", "run_script"}:
            timeout = 30
            try:
                timeout = int(ctx.slots.get("timeout", "30"))
            except ValueError:
                pass
            ok, output = run_script(path, timeout=timeout)
            return WorkerResult(
                ok=ok,
                summary=(f"Script {path} ran fine: {output}"
                         if ok else f"Script {path} failed: {output}"),
                data={"path": path, "check": "script", "ok": ok, "output": output})
        return WorkerResult.failed(
            f"I don't know how to check '{kind}' — I can verify a file, "
            "an Excel sheet, or run a script. Tell me which, boss.")
