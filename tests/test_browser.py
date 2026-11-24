"""Tests for the Browser Agent.

Everything here runs in dry_run mode — no real browser is launched and
no network is touched. The missing-Playwright path is tested by making
the import fail, the same way it fails on a machine without Playwright.
Run with: .venv/bin/pytest -q
"""

import sys

import pytest

from kairo.workers.base import TaskContext
from kairo.workers.browser import BrowserAgent, BrowserNotAvailableError


# -- dry_run ------------------------------------------------------------------
def test_dry_run_open_url_returns_planned_action(tmp_path):
    agent = BrowserAgent(settings=None, dry_run=True)
    result = agent.open_url("https://example.com", browser="chrome")
    assert "https://example.com" in result
    assert "dry run" in result.lower()
    assert "would open" in result.lower()


def test_dry_run_download_returns_planned_action(tmp_path):
    agent = BrowserAgent(settings=None, dry_run=True)
    dest = str(tmp_path / "file.pdf")
    result = agent.download("https://example.com/file.pdf", dest, browser="chrome")
    assert "https://example.com/file.pdf" in result
    assert dest in result
    assert "dry run" in result.lower()
    assert not (tmp_path / "file.pdf").exists()  # nothing downloaded


def test_run_dry_run_via_ctx(tmp_path):
    agent = BrowserAgent()
    ctx = TaskContext(text="open example", dry_run=True, slots={
        "intent": "go_to_site", "url": "https://example.com"})
    result = agent.run(ctx)
    assert result.ok is True
    assert "would open" in result.summary.lower()
    assert "https://example.com" in result.summary


def test_run_dry_run_download_via_ctx(tmp_path):
    agent = BrowserAgent()
    ctx = TaskContext(text="download it", dry_run=True, slots={
        "intent": "download_file", "url": "https://example.com/f.zip",
        "dest": str(tmp_path / "f.zip")})
    result = agent.run(ctx)
    assert result.ok is True
    assert "would download" in result.summary.lower()


# -- Playwright missing --------------------------------------------------------
def test_missing_playwright_open_url_raises_clear_error(monkeypatch):
    # Simulate a machine without Playwright: the import must fail.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    agent = BrowserAgent()
    with pytest.raises(BrowserNotAvailableError, match="Playwright is not installed"):
        agent.open_url("https://example.com")


def test_missing_playwright_download_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    agent = BrowserAgent()
    with pytest.raises(BrowserNotAvailableError, match="Playwright is not installed"):
        agent.download("https://example.com/f.zip", "/tmp/f.zip")


def test_run_go_to_site_needs_no_playwright(monkeypatch):
    # "open youtube" opens the default browser directly — Playwright is
    # not involved, so a machine without it must still work.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    opened = {}

    def fake_open(url):
        opened["url"] = url
        return True

    monkeypatch.setattr("kairo.workers.browser.webbrowser.open", fake_open)
    agent = BrowserAgent()
    ctx = TaskContext(text="open youtube", slots={
        "_intent": "go_to_site", "site": "youtube"})
    result = agent.run(ctx)
    assert result.ok is True
    assert opened["url"] == "https://www.youtube.com"
    assert "youtube" in result.summary.lower()


def test_run_download_without_playwright_returns_failed_result(monkeypatch):
    agent = BrowserAgent()
    ctx = TaskContext(text="download it", slots={
        "intent": "download_file", "url": "https://example.com/f.zip",
        "dest": "/tmp/f.zip"})
    result = agent.run(ctx)
    assert result.ok is False
    assert "playwright" in result.summary.lower()


# -- validation -----------------------------------------------------------------
def test_run_open_without_url_fails(tmp_path):
    agent = BrowserAgent(dry_run=True)
    ctx = TaskContext(text="open it", slots={"intent": "go_to_site"})
    result = agent.run(ctx)
    assert result.ok is False


def test_run_unknown_intent_fails():
    agent = BrowserAgent(dry_run=True)
    ctx = TaskContext(text="do a thing", slots={"intent": "surf_the_web"})
    assert agent.run(ctx).ok is False


def test_profile_dir_under_settings_data_dir(tmp_path):
    from kairo.config import Settings
    agent = BrowserAgent(settings=Settings(data_dir=tmp_path))
    assert agent._profile_dir() == tmp_path / "browser-profile"


# -- go_to_site (v0.3.8): "open youtube" --------------------------------------
def test_resolve_site_known_names():
    from kairo.workers.browser import resolve_site
    assert resolve_site("youtube") == "https://www.youtube.com"
    assert resolve_site("gmail") == "https://mail.google.com"
    assert resolve_site("whatsapp") == "https://web.whatsapp.com"
    assert resolve_site("Google") == "https://www.google.com"


def test_resolve_site_dotted_and_full_urls():
    from kairo.workers.browser import resolve_site
    assert resolve_site("example.com") == "https://example.com"
    assert resolve_site("https://example.com/page") == "https://example.com/page"


def test_resolve_site_unknown_name_becomes_search():
    from kairo.workers.browser import resolve_site
    assert resolve_site("nasa") == "https://www.google.com/search?q=nasa"


def test_go_to_site_dry_run_opens_nothing(monkeypatch):
    monkeypatch.setattr(
        "kairo.workers.browser.webbrowser.open",
        lambda url: (_ for _ in ()).throw(AssertionError("must not open")),
    )
    agent = BrowserAgent()
    ctx = TaskContext(text="open youtube", dry_run=True, slots={
        "_intent": "go_to_site", "site": "youtube"})
    result = agent.run(ctx)
    assert result.ok is True
    assert "would open" in result.summary.lower()
    assert "https://www.youtube.com" in result.summary


def test_go_to_site_browser_refuses(monkeypatch):
    monkeypatch.setattr(
        "kairo.workers.browser.webbrowser.open", lambda url: False)
    agent = BrowserAgent()
    ctx = TaskContext(text="open youtube", slots={
        "_intent": "go_to_site", "site": "youtube"})
    assert agent.run(ctx).ok is False


def test_registry_routes_open_youtube_to_browser_not_apps():
    from kairo.intents import default_registry
    registry = default_registry()
    match = registry.match("open youtube")
    assert match is not None
    assert match.name == "go_to_site"
    assert match.slots == {"site": "youtube"}
    match = registry.match("open youtube.com")
    assert match is not None and match.name == "go_to_site"
    # Real apps still go to the system worker, not the browser.
    assert registry.match("open chrome").name == "open_app"
    assert registry.match("open notepad").name == "open_app"
