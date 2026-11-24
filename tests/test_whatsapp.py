"""Tests for the WhatsApp worker (Builder F).

No real browser, no real WhatsApp, no network: the Playwright layer is
mocked with a fake driver injected as ``worker.driver``. The only real
paths exercised are the ask-first gate, the dry-run planner, the
missing-Playwright error, and the Windows-only app route on Linux.
"""

from pathlib import Path

import pytest

from kairo.config import Settings
from kairo.safety import ActionKind
from kairo.workers.base import TaskContext
from kairo.workers.whatsapp import WhatsAppWorker


class _FakeDriver:
    """Stand-in for the Playwright layer — records what would be sent."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.files_sent: list[tuple[str, str]] = []
        self.opened: list[str] = []

    def open_chat(self, name):
        self.opened.append(name)
        return name

    def read_unread(self):
        return [{"chat": "Amna", "sender": "Amna", "text": "salaam"}]

    def reply(self, name, message):
        self.sent.append((name, message))
        return f"Sent WhatsApp message to {name}: '{message}'"

    def send_file(self, name, path):
        self.files_sent.append((name, path))
        return f"Sent file {Path(path).name} to {name} on WhatsApp"


def _worker(tmp_path, dry_run: bool = False) -> WhatsAppWorker:
    worker = WhatsAppWorker(Settings(data_dir=tmp_path), dry_run=dry_run)
    worker.driver = _FakeDriver()
    return worker


def _yes(_confirmation):
    return True


def _no(_confirmation):
    return False


# -- ask-first ------------------------------------------------------------
def test_reply_without_confirmation_returns_needs_confirmation(tmp_path):
    worker = _worker(tmp_path)
    result = worker.reply("Amna", "the report is ready")
    assert result.ok is False
    assert worker.driver.sent == []  # nothing was sent
    conf = result.needs_confirmation
    assert conf is not None
    assert conf.kind is ActionKind.SEND
    assert "Amna" in conf.description
    assert "the report is ready" in conf.description


def test_reply_with_confirming_callback_sends_via_fake_driver(tmp_path):
    worker = _worker(tmp_path)
    result = worker.reply("Amna", "the report is ready", confirm=_yes)
    assert result.ok is True
    assert "Sent" in result.summary
    assert worker.driver.sent == [("Amna", "the report is ready")]


def test_reply_with_declining_callback_sends_nothing(tmp_path):
    worker = _worker(tmp_path)
    result = worker.reply("Amna", "the report is ready", confirm=_no)
    assert result.ok is False
    assert worker.driver.sent == []


def test_send_file_confirmation_names_file_and_person(tmp_path):
    worker = _worker(tmp_path)
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"fake-pdf")
    result = worker.send_file("Amna", str(file_path))
    assert result.ok is False
    assert worker.driver.files_sent == []
    conf = result.needs_confirmation
    assert conf is not None
    assert conf.kind is ActionKind.SEND
    assert "report.pdf" in conf.description
    assert "Amna" in conf.description


def test_send_file_with_confirmation_sends(tmp_path):
    worker = _worker(tmp_path)
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"fake-pdf")
    result = worker.send_file("Amna", str(file_path), confirm=_yes)
    assert result.ok is True
    assert worker.driver.files_sent == [("Amna", str(file_path))]


def test_send_file_missing_file_fails_without_sending(tmp_path):
    worker = _worker(tmp_path)
    result = worker.send_file("Amna", str(tmp_path / "nope.pdf"), confirm=_yes)
    assert result.ok is False
    assert "not found" in result.summary.lower()
    assert worker.driver.files_sent == []


# -- dry run --------------------------------------------------------------
def test_dry_run_returns_planned_action_strings(tmp_path):
    worker = _worker(tmp_path, dry_run=True)
    result = worker.reply("Amna", "hello there")
    assert result.ok is True
    assert "Dry run" in result.summary
    assert "Amna" in result.summary
    assert worker.driver.sent == []
    assert "would open" in worker.open_chat("Amna").lower()
    assert worker.read_unread() == []


def test_run_dispatches_reply_through_ctx_confirm(tmp_path):
    worker = _worker(tmp_path)
    ctx = TaskContext(
        text="reply to Amna",
        slots={"intent": "whatsapp_reply", "name": "Amna", "message": "hi"},
        confirm=_yes,
    )
    result = worker.run(ctx)
    assert result.ok is True
    assert worker.driver.sent == [("Amna", "hi")]


def test_run_reply_without_ctx_confirm_returns_needs_confirmation(tmp_path):
    worker = _worker(tmp_path)
    ctx = TaskContext(
        text="reply to Amna",
        slots={"intent": "whatsapp_reply", "name": "Amna", "message": "hi"},
        confirm=None,
    )
    result = worker.run(ctx)
    assert result.needs_confirmation is not None
    assert worker.driver.sent == []


def test_run_read_returns_unread_from_fake_driver(tmp_path):
    worker = _worker(tmp_path)
    ctx = TaskContext(text="read whatsapp", slots={"intent": "whatsapp_read"})
    result = worker.run(ctx)
    assert result.ok is True
    assert result.data["messages"][0]["chat"] == "Amna"


# -- missing Playwright / Windows route -----------------------------------
def test_reply_without_playwright_fails_plainly(tmp_path):
    worker = WhatsAppWorker(Settings(data_dir=tmp_path))
    assert worker.driver is None  # no fake driver, no Playwright installed
    result = worker.reply("Amna", "hello", confirm=_yes)
    assert result.ok is False
    assert "Playwright is not installed" in result.summary


def test_windows_app_route_on_linux_returns_needs_windows(tmp_path):
    worker = _worker(tmp_path)
    result = worker.reply_via_app("Amna", "hello", confirm=_yes)
    assert result.ok is False
    assert "needs Windows" in result.summary
    assert worker.driver.sent == []
