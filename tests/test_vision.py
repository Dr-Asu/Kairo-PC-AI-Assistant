"""Tests for the Screen Vision worker (Builder F).

No real screenshots, no real Gemini calls: the Gemini REST call is mocked
at the httpx level, and pyautogui is absent in this environment, so the
screenshot-unavailable path is exercised for real.
"""

import json

import pytest

from kairo.config import Settings
from kairo.workers.base import TaskContext
from kairo.workers.vision import NO_KEY_MESSAGE, ScreenVisionWorker


def _worker(tmp_path, gemini_key: str = "") -> ScreenVisionWorker:
    return ScreenVisionWorker(Settings(data_dir=tmp_path, gemini_api_key=gemini_key))


def _fake_image(tmp_path, name: str = "screen.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    return str(path)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _mock_gemini(monkeypatch, text: str):
    import httpx

    def fake_post(url, json=None, timeout=None):
        assert "generativelanguage.googleapis.com" in url
        assert "gem-test-key" in url
        return _FakeResponse(
            {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        )

    monkeypatch.setattr(httpx, "post", fake_post)


# -- no-key behaviour -----------------------------------------------------
def test_describe_without_key_says_it_needs_a_gemini_key(tmp_path):
    worker = _worker(tmp_path)
    result = worker.describe(_fake_image(tmp_path))
    assert "needs a Gemini key" in result
    assert result == NO_KEY_MESSAGE


def test_handover_plan_without_key_returns_unknown_app(tmp_path):
    worker = _worker(tmp_path)
    plan = worker.handover_plan(_fake_image(tmp_path))
    assert plan["app"] == "unknown"
    assert plan["done"] == []
    assert plan["remaining"] == []
    assert "Gemini key" in plan["note"]


def test_explain_without_key_says_it_needs_a_gemini_key(tmp_path):
    worker = _worker(tmp_path)
    assert "needs a Gemini key" in worker.explain(_fake_image(tmp_path))


# -- mocked Gemini --------------------------------------------------------
def test_describe_with_mocked_gemini_returns_mocked_description(tmp_path, monkeypatch):
    _mock_gemini(monkeypatch, "Chrome is open with a half-filled form and no errors.")
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    result = worker.describe(_fake_image(tmp_path))
    assert result == "Chrome is open with a half-filled form and no errors."


def test_explain_returns_simple_words(tmp_path, monkeypatch):
    _mock_gemini(
        monkeypatch,
        "This is a simple screen. It shows a form with your name filled in.",
    )
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    result = worker.explain(_fake_image(tmp_path), "explain this simply")
    assert "simple screen" in result
    assert "form" in result


def test_handover_plan_parses_json_from_gemini(tmp_path, monkeypatch):
    plan_text = json.dumps(
        {
            "app": "Excel",
            "task": "Fill the attendance sheet",
            "done": ["Opened the file"],
            "remaining": ["Fill column B"],
        }
    )
    _mock_gemini(monkeypatch, plan_text)
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    plan = worker.handover_plan(_fake_image(tmp_path))
    assert plan["app"] == "Excel"
    assert plan["task"] == "Fill the attendance sheet"
    assert plan["done"] == ["Opened the file"]
    assert plan["remaining"] == ["Fill column B"]


# -- screenshots ----------------------------------------------------------
def test_screenshot_without_pyautogui_reports_unavailable(tmp_path):
    worker = _worker(tmp_path)
    with pytest.raises(RuntimeError, match="not available on this machine"):
        worker.screenshot()


def test_run_screenshot_intent_fails_plainly_without_pyautogui(tmp_path):
    worker = _worker(tmp_path)
    ctx = TaskContext(text="take a screenshot", slots={"intent": "screenshot"})
    result = worker.run(ctx)
    assert result.ok is False
    assert "not available on this machine" in result.summary


# -- complete_this --------------------------------------------------------
def test_run_complete_this_returns_plan_and_summary(tmp_path, monkeypatch):
    plan_text = json.dumps(
        {
            "app": "Excel",
            "task": "Fill the attendance sheet",
            "done": ["Opened the file"],
            "remaining": ["Fill column B"],
        }
    )
    _mock_gemini(monkeypatch, plan_text)
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    ctx = TaskContext(
        text="finish this for me",
        slots={"intent": "complete_this", "image": _fake_image(tmp_path)},
    )
    result = worker.run(ctx)
    assert result.ok is True
    assert result.needs_confirmation is None
    assert result.data["plan"]["app"] == "Excel"
    assert "Fill column B" in result.summary


def test_run_complete_this_never_silently_submits(tmp_path, monkeypatch):
    plan_text = json.dumps(
        {
            "app": "Chrome",
            "task": "Submit the job application",
            "done": ["Filled the form"],
            "remaining": ["Submit the application"],
        }
    )
    _mock_gemini(monkeypatch, plan_text)
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    ctx = TaskContext(
        text="complete this",
        slots={"intent": "complete_this", "image": _fake_image(tmp_path)},
    )
    result = worker.run(ctx)
    assert result.ok is False
    assert result.needs_confirmation is not None
    assert "Submit the application" in result.needs_confirmation.description
    assert result.data["plan"]["app"] == "Chrome"


def test_run_explain_screen_uses_mocked_vision(tmp_path, monkeypatch):
    _mock_gemini(monkeypatch, "A simple settings screen with one switch turned on.")
    worker = _worker(tmp_path, gemini_key="gem-test-key")
    ctx = TaskContext(
        text="explain this screen",
        slots={"intent": "explain_screen", "image": _fake_image(tmp_path)},
    )
    result = worker.run(ctx)
    assert result.ok is True
    assert "settings screen" in result.summary
