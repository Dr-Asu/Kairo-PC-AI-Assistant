"""Tests for the HUD (FastAPI dashboard + phone bridge) (Builder F).

Uses fastapi.testclient.TestClient only — no server, no network. The boss
is a fake object with the same attributes the real Boss will have
(settings / memory / brain / state / workers / handle), and memory is a
small in-memory stand-in for the MemoryStore interface.
"""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kairo.config import Settings
from kairo.hud.app import create_app
from kairo.state import StateMachine


class _FakeMemory:
    """Stand-in for MemoryStore: log_usage / usage_today / tasks / facts."""

    def __init__(self):
        self.usage: dict[str, dict[str, int]] = {}
        self.facts: dict[str, str] = {}
        self.tasks = [
            {"id": 1, "text": "open notepad", "status": "done"},
            {"id": 2, "text": "take a screenshot", "status": "done"},
        ]

    def log_usage(self, when, app, minutes):
        self.usage.setdefault(when, {})[app] = minutes

    def usage_today(self):
        return dict(self.usage.get(date.today().isoformat(), {}))

    def list_tasks(self, status=None, limit=50):
        return self.tasks[:limit]

    def add_fact(self, key, value, source=""):
        self.facts[key] = value

    def get_fact(self, key):
        return self.facts.get(key)

    def search_facts(self, query, limit=5):
        return [{"key": k, "value": v} for k, v in self.facts.items()][:limit]

    def list_learnings(self):
        return [{"id": 1, "text": "Boss likes Roman Urdu replies", "kind": "preference"}]


class _FakeBoss:
    def __init__(self, tmp_path, token: str = "", memory=None):
        self.settings = Settings(data_dir=tmp_path, kairo_token=token)
        self.memory = _FakeMemory() if memory is None else memory
        self.brain = SimpleNamespace(name="local")
        self.state = StateMachine()
        self.workers = {"vision": object(), "whatsapp": object()}
        self.handled: list[str] = []

    def handle(self, text: str) -> str:
        self.handled.append(text)
        return f"Boss heard: {text}"


def _client(tmp_path, token: str = "") -> TestClient:
    return TestClient(create_app(_FakeBoss(tmp_path, token=token)))


# -- dashboard page -------------------------------------------------------
def test_get_root_returns_dashboard_html(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Kairo Assistant" in resp.text
    assert "<html" in resp.text.lower()


# -- status / tasks / memory ----------------------------------------------
def test_api_status_returns_brain_and_workers(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["brain"] == "local"
    assert data["workers"] == ["vision", "whatsapp"]
    assert data["language"] == "en"
    assert data["listening"] is False
    assert data["usage_today"] == {}
    assert data["state"] == "idle"


def test_api_tasks_returns_recent_tasks(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 2
    assert resp.json()["tasks"][0]["text"] == "open notepad"


def test_api_memory_returns_facts_and_learnings(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/memory")
    assert resp.status_code == 200
    assert "facts" in resp.json()
    assert resp.json()["learnings"][0]["kind"] == "preference"


def test_api_command_returns_boss_reply(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/command", json={"text": "what time is it"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Boss heard: what time is it"


def test_api_command_rejects_empty_text(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/command", json={"text": "  "})
    assert resp.status_code == 422


# -- token auth -----------------------------------------------------------
def test_token_auth_blocks_api_without_header(tmp_path):
    client = _client(tmp_path, token="secret123")
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/command", json={"text": "hi"}).status_code == 401


def test_token_auth_allows_api_with_header(tmp_path):
    client = _client(tmp_path, token="secret123")
    headers = {"X-Kairo-Token": "secret123"}
    assert client.get("/api/status", headers=headers).status_code == 200
    resp = client.post("/api/command", json={"text": "hi"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Boss heard: hi"


def test_token_auth_wrong_token_is_rejected(tmp_path):
    client = _client(tmp_path, token="secret123")
    resp = client.get("/api/status", headers={"X-Kairo-Token": "wrong"})
    assert resp.status_code == 401


def test_dashboard_page_stays_public_with_token_set(tmp_path):
    client = _client(tmp_path, token="secret123")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Kairo Assistant" in resp.text


# -- phone bridge ---------------------------------------------------------
def test_phone_usage_stored_and_visible_in_status(tmp_path):
    client = _client(tmp_path)
    today = date.today().isoformat()
    resp = client.post(
        "/api/phone/usage", json={"date": today, "apps": {"TikTok": 45, "YouTube": 30}}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "apps": 2}
    status = client.get("/api/status").json()
    assert status["usage_today"]["TikTok"] == 45
    assert status["usage_today"]["YouTube"] == 30


def test_phone_health_stores_facts(tmp_path):
    boss = _FakeBoss(tmp_path)
    client = TestClient(create_app(boss))
    today = date.today().isoformat()
    resp = client.post(
        "/api/phone/health",
        json={"date": today, "steps": 8000, "sleep_hours": 7.5, "heart_rate": 72},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert boss.memory.get_fact(f"health_steps_{today}") == "8000"
    assert boss.memory.get_fact(f"health_sleep_hours_{today}") == "7.5"
    assert boss.memory.get_fact(f"health_heart_rate_{today}") == "72"


def test_phone_command_goes_to_boss(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/phone/command", json={"text": "open chrome"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Boss heard: open chrome"


# -- no memory ------------------------------------------------------------
def test_status_and_tasks_survive_missing_memory(tmp_path):
    boss = _FakeBoss(tmp_path, memory=None)
    boss.memory = None
    client = TestClient(create_app(boss))
    assert client.get("/api/status").json()["usage_today"] == {}
    assert client.get("/api/tasks").json()["tasks"] == []
    assert client.get("/api/memory").json() == {"facts": [], "learnings": []}
