"""Web dashboard + phone bridge (FastAPI).

The same FastAPI app serves the local web dashboard and receives the
phone's numbers. Everything is local: the dashboard reads the same SQLite
database and task state the backend writes. Nothing leaves the house.

Builder F owns this file. Keep the public API exactly as declared here:

* ``create_app(boss) -> FastAPI`` — routes:
  - GET  /                the dashboard page (static/index.html)
  - GET  /api/status      listening state, current task, usage today, warnings
  - GET  /api/tasks       recent tasks from memory
  - POST /api/command     {"text": "..."} -> boss.handle(text)
  - GET  /api/memory      facts + learnings (view/correct/delete)
  - POST /api/phone/usage   companion app posts app minutes
  - POST /api/phone/health  companion app posts health numbers
  - POST /api/phone/command voice command from the phone -> boss.handle
* Auth: when ``KAIRO_TOKEN`` is set, every /api route requires the
  ``X-Kairo-Token`` header. When it is not set, the dashboard binds to
  127.0.0.1 only (see __main__.py).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..state import AssistantState
from ..workers.phone import record_health, record_usage

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _CommandBody(BaseModel):
    text: str = ""


class _PhoneUsageBody(BaseModel):
    date: str = ""
    apps: dict[str, int] = {}


class _PhoneHealthBody(BaseModel):
    date: str = ""
    steps: int | None = None
    sleep_hours: float | None = None
    heart_rate: int | None = None


def _safe_call(obj, method: str, *args, default=None, **kwargs):
    """Call an optional method on a possibly-None object, never crashing."""
    if obj is None:
        return default
    fn = getattr(obj, method, None)
    if fn is None:
        return default
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def create_app(boss):
    """Build the FastAPI app. Builder F implements this."""
    settings = getattr(boss, "settings", None)
    token = (getattr(settings, "kairo_token", "") or "").strip()

    app = FastAPI(title="Kairo Assistant")

    @app.middleware("http")
    async def _token_auth(request: Request, call_next):
        # GET / (the dashboard page) stays public on localhost. Every
        # /api route requires X-Kairo-Token when a token is set.
        if token and request.url.path.startswith("/api"):
            if request.headers.get("X-Kairo-Token") != token:
                return JSONResponse(
                    {"detail": "Missing or wrong X-Kairo-Token header."},
                    status_code=401,
                )
        return await call_next(request)

    def _memory():
        return getattr(boss, "memory", None)

    def _status_payload() -> dict:
        state = getattr(boss, "state", None)
        state_value = getattr(state, "state", None)
        if hasattr(state_value, "value"):
            state_value = state_value.value
        state_value = state_value or "idle"
        brain = getattr(boss, "brain", None)
        brain_name = getattr(brain, "name", None) or getattr(
            settings, "brain_name", "local"
        )
        workers = getattr(boss, "workers", None)
        if isinstance(workers, dict):
            worker_names = sorted(workers.keys())
        elif workers is None:
            worker_names = []
        else:
            worker_names = sorted(str(w) for w in workers)
        usage = _safe_call(_memory(), "usage_today", default={}) or {}
        return {
            "state": state_value,
            "brain": brain_name,
            "language": getattr(settings, "language", "en") or "en",
            "workers": worker_names,
            "usage_today": usage,
            "listening": state_value == AssistantState.LISTENING.value,
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        index = _STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return HTMLResponse("<h1>Kairo Assistant</h1><p>Dashboard page missing.</p>")

    @app.get("/api/status")
    async def api_status():
        return _status_payload()

    @app.get("/api/tasks")
    async def api_tasks():
        tasks = _safe_call(_memory(), "list_tasks", limit=50, default=[]) or []
        return {"tasks": tasks}

    @app.post("/api/command")
    async def api_command(body: _CommandBody):
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Command needs a 'text' field.")
        handle = getattr(boss, "handle", None)
        if handle is None:
            raise HTTPException(status_code=503, detail="Boss is not available.")
        try:
            reply = handle(text)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the dashboard
            raise HTTPException(status_code=500, detail=f"Boss failed: {exc}") from exc
        return {"reply": reply}

    @app.get("/api/memory")
    async def api_memory():
        memory = _memory()
        facts = _safe_call(memory, "search_facts", "", limit=50, default=[]) or []
        learnings = _safe_call(memory, "list_learnings", default=[]) or []
        return {"facts": facts, "learnings": learnings}

    @app.post("/api/phone/usage")
    async def api_phone_usage(body: _PhoneUsageBody):
        memory = _memory()
        if memory is None:
            raise HTTPException(status_code=503, detail="Memory is not available.")
        payload: dict = {"apps": body.apps}
        if body.date:
            payload["date"] = body.date
        try:
            return record_usage(memory, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/phone/health")
    async def api_phone_health(body: _PhoneHealthBody):
        memory = _memory()
        if memory is None:
            raise HTTPException(status_code=503, detail="Memory is not available.")
        payload: dict = {}
        if body.date:
            payload["date"] = body.date
        for key in ("steps", "sleep_hours", "heart_rate"):
            value = getattr(body, key)
            if value is not None:
                payload[key] = value
        try:
            return record_health(memory, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/phone/command")
    async def api_phone_command(body: _CommandBody):
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Command needs a 'text' field.")
        handle = getattr(boss, "handle", None)
        if handle is None:
            raise HTTPException(status_code=503, detail="Boss is not available.")
        try:
            reply = handle(text)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the dashboard
            raise HTTPException(status_code=500, detail=f"Boss failed: {exc}") from exc
        return {"reply": reply}

    return app
