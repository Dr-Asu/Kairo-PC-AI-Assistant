"""Tests for the Office Worker (Excel / Word / PowerPoint).

All files live under tmp_path only — never real user files.
Run with: .venv/bin/pytest -q
"""

import json

import pytest
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from kairo.workers.base import TaskContext, WorkerResult
from kairo.workers.office import OfficeWorker, make_excel, make_pptx, make_word


# -- make_excel -------------------------------------------------------------
def test_make_excel_headers_rows_and_formulas(tmp_path):
    path = tmp_path / "report.xlsx"
    out = make_excel(
        str(path),
        rows=[["Apples", 10, 2.5], ["Oranges", 5, 3.0]],
        headers=["Item", "Qty", "Price"],
        formulas={"C5": "=SUM(C3:C4)", "B5": "=SUM(B3:B4)"},
        title="Fruit Report",
    )
    assert out == str(path)
    assert path.exists() and path.stat().st_size > 0

    wb = load_workbook(str(path))
    ws = wb.active
    assert ws["A1"].value == "Fruit Report"          # title row
    assert ws["A2"].value == "Item"                  # bold header row
    assert ws["A2"].font.bold is True
    assert ws["A3"].value == "Apples"
    assert ws["B3"].value == 10
    assert ws["C3"].value == 2.5
    # Formula strings are stored (openpyxl does not evaluate them).
    assert ws["C5"].value == "=SUM(C3:C4)"
    assert ws["B5"].value == "=SUM(B3:B4)"
    # Column widths were sized to content.
    assert ws.column_dimensions["A"].width >= len("Oranges")


def test_make_excel_without_title_or_headers(tmp_path):
    path = tmp_path / "plain.xlsx"
    make_excel(str(path), rows=[[1, 2], [3, 4]])
    wb = load_workbook(str(path))
    ws = wb.active
    assert ws["A1"].value == 1
    assert ws["B2"].value == 4


def test_make_excel_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "sheet.xlsx"
    make_excel(str(path), rows=[[1]], headers=["N"])
    assert path.exists()


# -- make_word ---------------------------------------------------------------
def test_make_word_title_and_paragraphs(tmp_path):
    path = tmp_path / "doc.docx"
    out = make_word(str(path), "My Report",
                    ["Introduction:", "Hello world.", "Conclusion:", "Done."])
    assert out == str(path)
    doc = Document(str(path))
    texts = [p.text for p in doc.paragraphs]
    assert texts[0] == "My Report"
    assert "Hello world." in texts
    assert "Done." in texts
    # Heading-like lines became headings (bold by style), not plain text.
    styles = [p.style.name for p in doc.paragraphs]
    assert "Heading 2" in styles


def test_make_word_bold_headings_off(tmp_path):
    path = tmp_path / "plain.docx"
    make_word(str(path), "Title", ["Introduction:", "Body."],
              bold_headings=False)
    doc = Document(str(path))
    assert all(p.style.name not in {"Heading 1", "Heading 2"}
               for p in doc.paragraphs if p.text == "Introduction:")


# -- make_pptx ---------------------------------------------------------------
def test_make_pptx_slide_count_and_content(tmp_path):
    path = tmp_path / "deck.pptx"
    slides = [
        {"title": "Agenda", "bullets": ["One", "Two"]},
        {"title": "Results", "bullets": ["Win"]},
    ]
    out = make_pptx(str(path), "Quarterly Update", slides)
    assert out == str(path)
    prs = Presentation(str(path))
    assert len(prs.slides) == 3  # title slide + 2 content slides
    assert prs.slides[0].shapes.title.text == "Quarterly Update"
    assert prs.slides[1].shapes.title.text == "Agenda"
    body_text = prs.slides[1].placeholders[1].text
    assert "One" in body_text and "Two" in body_text
    assert prs.slides[2].shapes.title.text == "Results"


def test_make_pptx_no_slides_is_just_title(tmp_path):
    path = tmp_path / "empty.pptx"
    make_pptx(str(path), "Only Title", [])
    assert len(Presentation(str(path)).slides) == 1


# -- OfficeWorker.run --------------------------------------------------------
def test_run_make_excel_from_json_payload(tmp_path):
    path = tmp_path / "run.xlsx"
    ctx = TaskContext(
        text="make a spreadsheet",
        slots={"kind": "make_excel", "path": str(path),
               "payload": json.dumps({
                   "headers": ["Item", "Qty"],
                   "rows": [["Pen", 3]],
                   "formulas": {"B3": "=SUM(B2:B2)"},
               })})
    result = OfficeWorker().run(ctx)
    assert isinstance(result, WorkerResult)
    assert result.ok is True
    assert result.files == [str(path)]
    assert load_workbook(str(path)).active["B3"].value == "=SUM(B2:B2)"


def test_run_make_word_from_payload(tmp_path):
    path = tmp_path / "run.docx"
    ctx = TaskContext(
        text="write a doc",
        slots={"kind": "make_word", "path": str(path),
               "payload": json.dumps({
                   "title": "Notes", "paragraphs": ["Hello."]})})
    result = OfficeWorker().run(ctx)
    assert result.ok is True
    assert result.files == [str(path)]


def test_run_make_pptx_from_payload(tmp_path):
    path = tmp_path / "run.pptx"
    ctx = TaskContext(
        text="make slides",
        slots={"kind": "make_pptx", "path": str(path),
               "payload": json.dumps({
                   "title": "Deck",
                   "slides": [{"title": "S1", "bullets": ["a"]}]})})
    result = OfficeWorker().run(ctx)
    assert result.ok is True
    assert len(Presentation(str(path)).slides) == 2


def test_run_without_payload_asks_boss_for_content(tmp_path):
    ctx = TaskContext(
        text="make a spreadsheet",
        slots={"kind": "make_excel", "path": str(tmp_path / "x.xlsx")})
    result = OfficeWorker().run(ctx)
    assert result.ok is False
    assert "content" in result.summary.lower()
    assert not (tmp_path / "x.xlsx").exists()


def test_run_unknown_kind_fails(tmp_path):
    ctx = TaskContext(text="make a pdf",
                      slots={"kind": "make_pdf", "path": str(tmp_path / "x.pdf")})
    assert OfficeWorker().run(ctx).ok is False
