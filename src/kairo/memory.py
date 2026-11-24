"""Local memory store — SQLite facts, tasks, learnings, usage, jobs.

Nothing leaves the PC. The cloud brain only ever sees the small snippet of
memory the current task needs. The boss can view, correct, or delete
anything learned, any time.

Builder D owns this file. Keep the public API exactly as declared here:

* ``MemoryStore(data_dir)`` — creates ``memory.db`` in ``data_dir``.
* ``add_fact(key, value, source="")`` / ``get_fact(key)`` / ``search_facts(query)``
* ``log_task(text, status)`` / ``list_tasks(status=None, limit=50)``
* ``add_learning(text, kind="preference")`` / ``list_learnings()`` /
  ``approve_learning(id, keep=True)`` / ``delete_learning(id)``
* ``log_usage(date, app, minutes)`` / ``usage_today()`` -> dict[app, minutes]
* ``add_routine(name, steps)`` / ``get_routine(name)`` / ``list_routines()``
* ``add_job(title, company, deadline, status="open")`` / ``list_jobs()`` /
  ``update_job(id, **fields)``
* ``run_learning_job(brain) -> int`` — nightly learning: read today's tasks
  and conversations, write durable learnings, return how many were written.

Search uses SQLite FTS5 when available, LIKE fallback otherwise. There is
no separate vector store in v0.1 — FTS search is the documented stand-in
(see STATUS.md); a ChromaDB/FAISS layer can be added later.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from .brain import BrainError


def _now_iso() -> str:
    """Current UTC time as an ISO string (sortable, comparable by date)."""
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _normalise(text: str) -> str:
    """Lower-case, collapse whitespace — for spotting repeated tasks."""
    return re.sub(r"\s+", " ", text.strip().lower())


# Words that signal the boss stated a format preference in a task.
_FORMAT_WORDS = {
    "pdf", "excel", "word", "csv", "powerpoint", "ppt", "pptx", "docx",
    "xlsx", "landscape", "portrait", "bold", "table", "spreadsheet",
}


class MemoryStore:
    """SQLite-backed memory. Builder D implements this."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "memory.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fts = False
        self._init_schema()

    # -- schema -----------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    key TEXT PRIMARY KEY, value TEXT, source TEXT, updated TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY, text TEXT, status TEXT, created TEXT
                );
                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY, text TEXT, kind TEXT,
                    approved INTEGER DEFAULT 0, created TEXT
                );
                CREATE TABLE IF NOT EXISTS usage (
                    date TEXT, app TEXT, minutes INTEGER,
                    PRIMARY KEY (date, app)
                );
                CREATE TABLE IF NOT EXISTS routines (
                    name TEXT PRIMARY KEY, steps_json TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, title TEXT, company TEXT,
                    deadline TEXT, status TEXT
                );
                """
            )
            # FTS5 side-table for fact search; LIKE fallback if unavailable.
            try:
                cur.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
                    "USING fts5(key, value)"
                )
                self._fts = True
            except sqlite3.OperationalError:
                self._fts = False
            self._conn.commit()

    def _execute(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    # -- facts ------------------------------------------------------------
    def add_fact(self, key: str, value: str, source: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts (key, value, source, updated) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, source=excluded.source, "
                "updated=excluded.updated",
                (key, value, source, _now_iso()),
            )
            if self._fts:
                self._conn.execute("DELETE FROM facts_fts WHERE key = ?", (key,))
                self._conn.execute(
                    "INSERT INTO facts_fts (key, value) VALUES (?, ?)",
                    (key, value),
                )
            self._conn.commit()

    def get_fact(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM facts WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else None

    def search_facts(self, query: str, limit: int = 5) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        with self._lock:
            if self._fts:
                fts_query = " OR ".join(f'"{t}"' for t in terms)
                try:
                    rows = self._conn.execute(
                        "SELECT key, value FROM facts_fts "
                        "WHERE facts_fts MATCH ? LIMIT ?",
                        (fts_query, limit),
                    ).fetchall()
                    return [{"key": r["key"], "value": r["value"]} for r in rows]
                except sqlite3.OperationalError:
                    pass  # fall through to LIKE
            like = f"%{query}%"
            rows = self._conn.execute(
                "SELECT key, value FROM facts "
                "WHERE key LIKE ? OR value LIKE ? LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]

    # -- tasks ------------------------------------------------------------
    def log_task(self, text: str, status: str = "done") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (text, status, created) VALUES (?, ?, ?)",
                (text, status, _now_iso()),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT id, text, status, created FROM tasks "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, text, status, created FROM tasks "
                    "WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [
            {"id": r["id"], "text": r["text"], "status": r["status"],
             "created": r["created"]}
            for r in rows
        ]

    # -- learnings --------------------------------------------------------
    def add_learning(self, text: str, kind: str = "preference") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO learnings (text, kind, approved, created) "
                "VALUES (?, ?, 0, ?)",
                (text, kind, _now_iso()),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_learnings(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text, kind, approved, created FROM learnings "
                "ORDER BY id DESC"
            ).fetchall()
        return [
            {"id": r["id"], "text": r["text"], "kind": r["kind"],
             "approved": r["approved"], "created": r["created"]}
            for r in rows
        ]

    def approve_learning(self, learning_id: int, keep: bool = True) -> None:
        with self._lock:
            if keep:
                self._conn.execute(
                    "UPDATE learnings SET approved = 1 WHERE id = ?",
                    (learning_id,),
                )
            else:
                self._conn.execute(
                    "DELETE FROM learnings WHERE id = ?", (learning_id,)
                )
            self._conn.commit()

    def delete_learning(self, learning_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM learnings WHERE id = ?", (learning_id,)
            )
            self._conn.commit()

    # -- usage ------------------------------------------------------------
    def log_usage(self, date: str, app: str, minutes: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (date, app, minutes) VALUES (?, ?, ?) "
                "ON CONFLICT(date, app) DO UPDATE SET "
                "minutes = usage.minutes + excluded.minutes",
                (date, app, minutes),
            )
            self._conn.commit()

    def usage_today(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT app, minutes FROM usage WHERE date = ?",
                (_today_iso(),),
            ).fetchall()
        return {r["app"]: r["minutes"] for r in rows}

    # -- routines ---------------------------------------------------------
    def add_routine(self, name: str, steps: list[dict]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO routines (name, steps_json) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET steps_json=excluded.steps_json",
                (name, json.dumps(steps)),
            )
            self._conn.commit()

    def get_routine(self, name: str) -> list[dict] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT steps_json FROM routines WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["steps_json"])

    def list_routines(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM routines ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    # -- job hunt ---------------------------------------------------------
    def add_job(self, title: str, company: str, deadline: str = "", status: str = "open") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs (title, company, deadline, status) "
                "VALUES (?, ?, ?, ?)",
                (title, company, deadline, status),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_jobs(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT id, title, company, deadline, status FROM jobs "
                    "ORDER BY id DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, title, company, deadline, status FROM jobs "
                    "WHERE status = ? ORDER BY id DESC",
                    (status,),
                ).fetchall()
        return [
            {"id": r["id"], "title": r["title"], "company": r["company"],
             "deadline": r["deadline"], "status": r["status"]}
            for r in rows
        ]

    def update_job(self, job_id: int, **fields: object) -> None:
        allowed = {"title", "company", "deadline", "status"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*updates.values(), job_id),
            )
            self._conn.commit()

    # -- nightly learning -------------------------------------------------
    def run_learning_job(self, brain) -> int:
        """Read today's tasks, write durable learnings. Returns count."""
        today = _today_iso()
        done_today = [
            t for t in self.list_tasks(status="done", limit=200)
            if (t["created"] or "")[:10] == today
        ]
        if not done_today:
            return 0

        existing = {_normalise(l["text"]) for l in self.list_learnings()}

        def _store(text: str, kind: str) -> bool:
            if _normalise(text) in existing:
                return False
            self.add_learning(text, kind=kind)
            existing.add(_normalise(text))
            return True

        is_local = (
            brain is None
            or getattr(brain, "name", "") == "local"
            or type(brain).__name__ == "LocalBrain"
        )
        if not is_local:
            try:
                return self._learning_from_brain(brain, done_today, _store)
            except BrainError:
                pass  # fall through to the local heuristics
            except Exception:
                pass  # a brain failure must never break the nightly job
        return self._learning_from_heuristics(done_today, _store)

    def _learning_from_heuristics(self, done_today: list[dict], _store) -> int:
        written = 0
        # Repeated task -> offer it as a routine.
        counts: dict[str, str] = {}
        for task in done_today:
            norm = _normalise(task["text"])
            if not norm:
                continue
            counts.setdefault(norm, task["text"].strip())
        repeated: set[str] = set()
        for norm, original in counts.items():
            seen = sum(
                1 for t in done_today if _normalise(t["text"]) == norm
            )
            if seen >= 2:
                repeated.add(norm)
                if _store(
                    f"Boss often asks for '{original}' — offer it as a routine.",
                    "preference",
                ):
                    written += 1
        # A task that names a format -> record the preference.
        # Skip tasks already covered by the routine learning above so one
        # task never writes two learnings for the same night.
        for task in done_today:
            if _normalise(task["text"]) in repeated:
                continue
            words = set(re.findall(r"\w+", task["text"].lower()))
            if words & _FORMAT_WORDS:
                if _store(
                    f"Boss prefers '{task['text'].strip()}' — "
                    "remember this format preference.",
                    "preference",
                ):
                    written += 1
        return written

    def _learning_from_brain(self, brain, done_today: list[dict], _store) -> int:
        tasks_text = "\n".join(f"- {t['text']}" for t in done_today)
        known = "\n".join(
            f"- {l['text']}" for l in self.list_learnings()
        ) or "(none yet)"
        reply = brain.complete(
            "You turn the boss's recent tasks into short durable learnings. "
            "Reply with 0-3 learnings, one per line, plain facts about "
            "lasting preferences or habits. No greeting, no numbering, "
            "no extra text. If nothing is durable, reply with just: none",
            f"Today's done tasks:\n{tasks_text}\n\n"
            f"Learnings already stored:\n{known}",
        )
        written = 0
        for line in reply.text.splitlines():
            line = line.strip()
            line = re.sub(r"^[-*•\d.)\s]+", "", line).strip()
            if not line:
                continue
            if _normalise(line) in {"none", "no learnings",
                                    "no durable learnings"}:
                continue
            if line.lower().startswith("no ") and len(line) < 40:
                continue
            if _store(line, "learned"):
                written += 1
        return written
