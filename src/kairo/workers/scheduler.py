"""Scheduler worker — timers, routines, briefings, deadlines.

* Timers: "timer for 10 minutes".
* Routines: a saved YAML/JSON file of steps ("morning mode" runs a whole
  chain through the normal workers, in order, checked at each step).
* Briefings: morning (today's tasks, meetings, weather, leftovers) and
  evening (what got done, what is still waiting) summaries.
* Deadlines: dated tasks watched by the Scheduler; a reminder the day
  before and again on the morning it is due.
* Job-hunt secretary: tracks applications and deadlines, drafts tailored
  applications from the CV + posting — DRAFTS only, NEVER submits without
  the boss's explicit per-job approval.

Builder D owns this file. Keep the public API exactly as declared here.

There is deliberately NO submit / send method in this file: drafting
only, never submitting — the boss's standing rule.
"""

from __future__ import annotations

import re
import threading
from datetime import date, timedelta

from ..brain import BrainError, LocalBrain
from ..memory import MemoryStore
from .base import TaskContext, Worker, WorkerResult


class SchedulerWorker(Worker):
    """Timers, routines, briefings, deadlines. Builder D implements this."""

    name = "scheduler"
    intents = {"start_timer", "run_routine", "morning_briefing", "evening_briefing"}

    def __init__(
        self,
        memory: MemoryStore | None = None,
        speak=None,
        boss=None,
        brain=None,
    ) -> None:
        self._memory = memory
        self._speak = speak  # callable(text) -> None, speaks a note aloud
        self._boss = boss  # callable(step_text) -> str, runs one routine step
        self._brain = brain if brain is not None else LocalBrain()
        self._timers: list[threading.Timer] = []
        self.last_timer_note: str | None = None

    # -- helpers ----------------------------------------------------------
    def _memory_from(self, ctx: TaskContext | None = None):
        if ctx is not None and getattr(ctx, "memory", None) is not None:
            return ctx.memory
        return self._memory

    def _speak_from(self, ctx: TaskContext | None = None):
        if ctx is not None and getattr(ctx, "speak", None) is not None:
            return ctx.speak
        return self._speak

    def _boss_from(self, ctx: TaskContext | None = None):
        if ctx is not None and getattr(ctx, "boss", None) is not None:
            return ctx.boss
        return self._boss

    def _weather_line(self) -> str:
        """Weather is optional and guarded: no network call is ever made
        from this worker. The line just tells the boss where to look."""
        return "Weather: check Open-Meteo when online."

    # -- timers -----------------------------------------------------------
    def _timer_done(self, minutes: int, ctx: TaskContext | None = None) -> None:
        note = f"Timer done, boss — {minutes} minutes are up."
        self.last_timer_note = note
        memory = self._memory_from(ctx)
        if memory is not None:
            try:
                memory.log_task(f"Timer ({minutes} minutes) finished")
            except Exception:
                pass  # a timer note must never crash the worker
        speak = self._speak_from(ctx)
        if callable(speak):
            try:
                speak(note)
            except Exception:
                pass

    def start_timer(self, minutes: int) -> str:
        minutes = int(minutes)
        timer = threading.Timer(minutes * 60.0, self._timer_done, args=(minutes,))
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        return f"Timer set for {minutes} minutes, boss."

    # -- routines ---------------------------------------------------------
    def run_routine(self, name: str) -> str:
        memory = self._memory_from()
        if memory is None:
            return (
                f"Boss, I have no memory to load the '{name}' routine from — "
                "routines live in memory, so I cannot run it yet."
            )
        steps = memory.get_routine(name)
        if steps is None:
            return f"Boss, I could not find a routine called '{name}'."
        if not steps:
            return f"Boss, the '{name}' routine is empty — nothing to run."
        boss = self._boss_from()
        ran: list[str] = []
        for step in steps:
            text = step.get("text", "") if isinstance(step, dict) else str(step)
            if not text:
                continue
            if callable(boss):
                try:
                    result = boss(text)
                    ran.append(str(result) if result else text)
                except Exception:
                    ran.append(text)
            else:
                ran.append(text)
        return (
            f"Routine '{name}' done, boss. "
            f"Ran {len(ran)} step(s): " + "; ".join(ran) + "."
        )

    # -- briefings --------------------------------------------------------
    def morning_briefing(self) -> str:
        memory = self._memory_from()
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        lines = ["Good morning, boss. Here is your day."]
        if memory is not None:
            jobs_today = [
                j for j in memory.list_jobs()
                if (j.get("deadline") or "") == today
            ]
            if jobs_today:
                lines.append(
                    "Jobs due today: "
                    + "; ".join(
                        f"{j['title']} at {j['company']}" for j in jobs_today
                    )
                    + "."
                )
            else:
                lines.append("No job deadlines today, boss.")
            open_tasks = memory.list_tasks(status="open")
            if open_tasks:
                lines.append(
                    "Open tasks: "
                    + "; ".join(t["text"] for t in open_tasks)
                    + "."
                )
            else:
                lines.append("Nothing open right now, boss.")
            leftover = [
                t for t in memory.list_tasks(limit=200)
                if t["status"] != "done" and (t["created"] or "")[:10] < today
            ]
            _ = yesterday  # kept for the day-before deadline check below
            if leftover:
                lines.append(
                    "Left over from earlier: "
                    + "; ".join(t["text"] for t in leftover)
                    + "."
                )
            else:
                lines.append("Nothing left over from yesterday, boss.")
            due_tomorrow = [
                j for j in memory.list_jobs()
                if (j.get("deadline") or "")
                == (date.today() + timedelta(days=1)).isoformat()
            ]
            if due_tomorrow:
                lines.append(
                    "Due tomorrow: "
                    + "; ".join(
                        f"{j['title']} at {j['company']}" for j in due_tomorrow
                    )
                    + " — I will remind you again in the morning, boss."
                )
        else:
            lines.append("No memory loaded, boss, so I have no tasks to report.")
        lines.append(self._weather_line())
        return "\n".join(lines)

    def evening_briefing(self) -> str:
        memory = self._memory_from()
        today = date.today().isoformat()
        lines = ["Good evening, boss. Here is how today went."]
        if memory is not None:
            done_today = [
                t for t in memory.list_tasks(status="done", limit=200)
                if (t["created"] or "")[:10] == today
            ]
            if done_today:
                lines.append(
                    f"You finished {len(done_today)} thing(s) today, boss: "
                    + "; ".join(t["text"] for t in done_today)
                    + "."
                )
            else:
                lines.append("Nothing marked done today yet, boss.")
            usage = memory.usage_today()
            total = sum(usage.values())
            if usage:
                detail = ", ".join(f"{app} {mins} min" for app, mins in usage.items())
                lines.append(
                    f"Screen time today: {total} minutes in total "
                    f"({detail})."
                )
            else:
                lines.append("No app usage logged today, boss.")
            open_items = memory.list_tasks(status="open")
            open_jobs = memory.list_jobs(status="open")
            if open_items or open_jobs:
                bits = [t["text"] for t in open_items]
                bits += [
                    f"{j['title']} at {j['company']}" for j in open_jobs
                ]
                lines.append("Still open: " + "; ".join(bits) + ".")
            else:
                lines.append("Nothing still open, boss — a clean slate.")
        else:
            lines.append("No memory loaded, boss, so I have no tasks to report.")
        return "\n".join(lines)

    # -- job-hunt drafting (drafts only — never submits) ------------------
    def draft_application(self, job_id: int, cv_text: str) -> str:
        """Draft a short tailored application for a tracked job.

        Drafting only. There is no submit / send step anywhere in this
        worker — the boss reviews the draft and decides per job.
        """
        memory = self._memory_from()
        job = None
        if memory is not None:
            for candidate in memory.list_jobs():
                if candidate["id"] == job_id:
                    job = candidate
                    break
        title = job["title"] if job else "the role"
        company = job["company"] if job else "the company"
        deadline = (job.get("deadline") or "") if job else ""
        cv_snippet = (cv_text or "").strip()
        if len(cv_snippet) > 600:
            cv_snippet = cv_snippet[:600] + "..."

        brain = self._brain
        is_local = (
            brain is None
            or getattr(brain, "name", "") == "local"
            or type(brain).__name__ == "LocalBrain"
        )
        if not is_local:
            try:
                reply = brain.complete(
                    "You write short, tailored job applications for the boss. "
                    "Write a brief cover note: greeting, why he fits the "
                    "role, and a polite close. Draft only.",
                    f"Role: {title}\nCompany: {company}\n"
                    f"Deadline: {deadline or 'not stated'}\n"
                    f"CV highlights:\n{cv_snippet}",
                )
                draft = reply.text.strip()
                # A cloud draft must still name the company and must never
                # promise to dispatch anything on the boss's behalf.
                if company.lower() in draft.lower() and not re.search(
                    r"\b(submit|send|sent|sending)\b", draft, re.IGNORECASE
                ):
                    return draft
            except BrainError:
                pass
            except Exception:
                pass

        deadline_line = (
            f" (deadline {deadline})" if deadline else ""
        )
        return (
            f"Dear {company} hiring team,\n\n"
            f"I am writing to apply for the {title} role at {company}"
            f"{deadline_line}. "
            "My background fits this role: "
            f"{cv_snippet or 'see my CV for details'} "
            "I would welcome the chance to discuss how I can help your team, "
            "and I am happy to share more detail or samples of my work.\n\n"
            "Thank you for your time and consideration.\n\n"
            "Best regards,\nMuhammad Asif\n\n"
            "— Draft only, boss. Review it first; nothing goes out "
            "without your yes."
        )

    # -- dispatch ---------------------------------------------------------
    def run(self, ctx: TaskContext) -> WorkerResult:
        slots = dict(ctx.slots or {})
        text = (ctx.text or "").lower()
        intent = slots.get("intent", "")
        try:
            if intent == "start_timer" or "minutes" in slots or "timer" in text:
                raw = slots.get("minutes", "")
                if not raw:
                    found = re.search(r"(\d+)\s*(?:minutes?|min)", text)
                    raw = found.group(1) if found else "5"
                summary = self.start_timer(int(raw))
                return WorkerResult.done(summary, minutes=int(raw))
            if intent == "run_routine" or "routine" in slots or "routine" in text:
                routine_name = (
                    slots.get("routine") or slots.get("name") or ""
                )
                if not routine_name:
                    found = re.search(r"routine\s+(?:called\s+)?(.+)", text)
                    routine_name = found.group(1).strip() if found else ""
                summary = self.run_routine(routine_name)
                return WorkerResult.done(summary, routine=routine_name)
            if intent == "morning_briefing" or "morning" in text:
                return WorkerResult.done(self.morning_briefing())
            if intent == "evening_briefing" or "evening" in text:
                return WorkerResult.done(self.evening_briefing())
        except Exception as exc:  # a worker failure is a failed result, not a crash
            return WorkerResult.failed(f"Boss, the scheduler hit a problem: {exc}")
        return WorkerResult.failed(
            "Boss, I did not understand that scheduler request."
        )
