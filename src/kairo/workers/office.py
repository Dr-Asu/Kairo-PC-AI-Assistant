"""Office Worker — Excel, Word, PowerPoint (plan: Office Worker).

Builds and edits spreadsheets with real formulas (openpyxl), documents
(python-docx), and presentations (python-pptx). The Tester verifies the
finished file before the boss delivers it.

Builder E owns this file. Keep the public API exactly as declared here:

* ``OfficeWorker`` — a ``Worker`` with intents {"make_excel", "make_word",
  "make_pptx"}; ``run(ctx)`` dispatches on ``ctx.slots["kind"]``.
* ``make_excel(path, rows, headers=None, formulas=None, title=None) -> str``
  — write a sheet; ``formulas`` maps "C5" -> "=SUM(C2:C4)".
* ``make_word(path, title, paragraphs, bold_headings=True) -> str``
* ``make_pptx(path, title, slides) -> str`` — ``slides`` is a list of
  {"title": ..., "bullets": [...]}.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import TaskContext, Worker, WorkerResult


def _ensure_parent(path: str) -> Path:
    p = Path(path)
    if p.parent and str(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def make_excel(path: str, rows: list[list], headers: list[str] | None = None,
               formulas: dict[str, str] | None = None, title: str | None = None) -> str:
    """Write a spreadsheet: optional title row, bold header row, data rows,
    and formulas (``{"C5": "=SUM(C2:C4)"}``). Column widths fit content."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    p = _ensure_parent(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    row_idx = 1
    ncols = 0
    if headers:
        ncols = max(ncols, len(headers))
    for row in rows:
        ncols = max(ncols, len(row))
    if formulas:
        from openpyxl.utils import column_index_from_string
        import re
        for cell in formulas:
            m = re.match(r"([A-Za-z]+)", cell)
            if m:
                ncols = max(ncols, column_index_from_string(m.group(1)))

    if title:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(ncols, 1))
        cell = ws.cell(row=1, column=1, value=title)
        cell.font = Font(bold=True, size=14)
        row_idx = 2

    if headers:
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row_idx, column=col, value=header)
            cell.font = Font(bold=True)
        row_idx += 1

    data_start = row_idx
    for row in rows:
        for col, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col, value=value)
        row_idx += 1

    if formulas:
        for cell_ref, formula in formulas.items():
            ws[cell_ref] = formula

    # Column widths sized to content (title row counts too), capped at 50.
    widths: dict[int, int] = {}
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            value = ws.cell(row=r, column=c).value
            if value is None:
                continue
            widths[c] = max(widths.get(c, 0), len(str(value)))
    for col, width in widths.items():
        # get_column_letter, not cell.column_letter: row 1 may hold merged
        # title cells (MergedCell has no column_letter attribute).
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 10), 50)

    wb.save(str(p))
    return str(p)


def _is_heading_like(line: str) -> bool:
    """A line that reads as a heading: ends with ':' or is short all-caps."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.endswith(":"):
        return True
    if stripped.isupper() and len(stripped) <= 80 and len(stripped.split()) <= 8:
        return True
    return False


def make_word(path: str, title: str, paragraphs: list[str], bold_headings: bool = True) -> str:
    """Write a .docx: title heading, then paragraphs. Heading-like lines
    (ending with ':' or short all-caps) become bold headings when
    ``bold_headings`` is True."""
    from docx import Document

    p = _ensure_parent(path)
    doc = Document()
    doc.add_heading(title, level=0)
    for line in paragraphs:
        if bold_headings and _is_heading_like(line):
            text = line.strip().rstrip(":")
            doc.add_heading(text, level=2)
        else:
            doc.add_paragraph(line)
    doc.save(str(p))
    return str(p)


def make_pptx(path: str, title: str, slides: list[dict]) -> str:
    """Write a .pptx: a title slide, then one slide per
    ``{"title": ..., "bullets": [...]}``."""
    from pptx import Presentation
    from pptx.util import Pt

    p = _ensure_parent(path)
    prs = Presentation()
    # Title slide.
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = title
    if len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = ""

    for slide in slides:
        layout = prs.slide_layouts[1]  # title + content
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = str(slide.get("title", ""))
        body = s.placeholders[1].text_frame
        body.clear()
        bullets = slide.get("bullets") or []
        for i, bullet in enumerate(bullets):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            para.text = str(bullet)
            para.level = 0
            for run in para.runs:
                run.font.size = Pt(18)
    prs.save(str(p))
    return str(p)


def _parse_payload(raw) -> object | None:
    """Payload arrives in ctx.slots as JSON (or already-parsed data)."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


class OfficeWorker(Worker):
    """Build and edit Office files. Builder E implements this."""

    name = "office"
    intents = {"make_excel", "make_word", "make_pptx"}

    def run(self, ctx: TaskContext) -> WorkerResult:
        kind = ctx.slots.get("kind") or ctx.slots.get("intent") or ""
        path = ctx.slots.get("path", "")
        payload = _parse_payload(ctx.slots.get("payload"))

        if kind not in self.intents:
            return WorkerResult.failed(
                f"I don't know how to make '{kind or 'that'}' — "
                "I can make Excel, Word, or PowerPoint files. "
                "Tell me which one and what should be in it, boss.")
        if not path:
            return WorkerResult.failed(
                "No file path given — tell me where to save it, boss.")
        if payload is None:
            return WorkerResult.failed(
                f"No content given for the {kind.replace('make_', '')} file — "
                "boss, please give me the content (rows, paragraphs, or slides) "
                "and I'll build it.")
        try:
            if kind == "make_excel":
                if isinstance(payload, dict):
                    rows = payload.get("rows") or []
                    headers = payload.get("headers")
                    formulas = payload.get("formulas")
                    title = payload.get("title")
                elif isinstance(payload, list):
                    rows, headers, formulas, title = payload, None, None, None
                else:
                    return WorkerResult.failed(
                        "Excel content must be rows (with optional headers, "
                        "formulas, and title) — boss, please give me the rows.")
                if not rows and not headers:
                    return WorkerResult.failed(
                        "No rows given for the spreadsheet — boss, please "
                        "give me the content and I'll build it.")
                out = make_excel(path, rows, headers=headers, formulas=formulas, title=title)
                return WorkerResult(ok=True, summary=f"Excel file created: {out}",
                                    files=[out], data={"path": out, "kind": kind})
            if kind == "make_word":
                if isinstance(payload, dict):
                    title = payload.get("title") or ctx.slots.get("title") or "Document"
                    paragraphs = payload.get("paragraphs") or []
                elif isinstance(payload, list):
                    title = ctx.slots.get("title") or "Document"
                    paragraphs = payload
                else:
                    return WorkerResult.failed(
                        "Word content must be a title and paragraphs — boss, "
                        "please give me the text.")
                if not paragraphs:
                    return WorkerResult.failed(
                        "No paragraphs given for the document — boss, please "
                        "give me the content and I'll write it.")
                out = make_word(path, title, paragraphs)
                return WorkerResult(ok=True, summary=f"Word document created: {out}",
                                    files=[out], data={"path": out, "kind": kind})
            # make_pptx
            if isinstance(payload, dict):
                title = payload.get("title") or ctx.slots.get("title") or "Presentation"
                slides = payload.get("slides") or []
            elif isinstance(payload, list):
                title = ctx.slots.get("title") or "Presentation"
                slides = payload
            else:
                return WorkerResult.failed(
                    "Presentation content must be slides — boss, please "
                    "give me the slide titles and bullets.")
            if not slides:
                return WorkerResult.failed(
                    "No slides given for the presentation — boss, please "
                    "give me the content and I'll build it.")
            out = make_pptx(path, title, slides)
            return WorkerResult(ok=True, summary=f"Presentation created: {out}",
                                files=[out], data={"path": out, "kind": kind})
        except Exception as exc:  # noqa: BLE001 - report build failures plainly
            return WorkerResult.failed(f"Couldn't build the file ({exc}).")
        return WorkerResult.failed("Couldn't build the file.")
