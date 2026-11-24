"""The boss agent — plans, assigns, checks, reports.

Flow for every message from the boss (the user):
1. If a confirmation is pending and the message is a yes/no, resolve it.
2. Match a fixed intent first (fast, offline). If the intent's action needs
   confirmation, ask and wait — never do it silently.
3. Otherwise fall through to the cloud brain (or local mode) with memory.
4. Dispatch worker results through the Tester for verification, then report
   back in plain words, spoken and on screen.

Builder A owns this file: implement ``Boss.handle`` and ``build_boss``.
Keep the public API exactly as declared here.

Notes on the state of the project (23 Nov 2026): the other builders'
modules are still stubs — ``MemoryStore`` and every worker's ``run()``
raise ``NotImplementedError`` until their builder lands them. The boss
is written to survive that: memory is optional (``None`` when its store
cannot be created), workers whose import fails are skipped and listed,
and a worker that raises ``NotImplementedError`` is reported plainly as
"still being built" instead of crashing the boss.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .brain import Brain, BrainError, boss_system_prompt, get_brain
from .config import Settings, get_settings
from .intents import Intent, default_registry
from .safety import (
    ActionKind,
    Confirmation,
    PermissionGate,
    guard_untrusted,
    parse_yes_no,
)
from .state import StateMachine, Task, TaskState
from .workers.base import TaskContext, Worker, WorkerResult

try:
    from .memory import MemoryStore
except Exception:  # pragma: no cover - memory module missing entirely
    MemoryStore = None  # type: ignore[assignment, misc]

# Phrases that make a piece of text look like a hidden instruction rather
# than a plain result. Worker output / memory text containing one of these
# is treated as untrusted data: wrapped, reported, never obeyed.
_INJECTION_PHRASES = (
    "ignore your rules",
    "ignore previous instructions",
    "ignore all instructions",
    "ignore the instructions",
    "disregard your instructions",
    "disregard all previous",
    "override your instructions",
    "delete everything",
    "you are now",
    "new system prompt",
    "do not follow your rules",
)


def _looks_like_injection(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _INJECTION_PHRASES)


def _confirmation_for_intent(intent_name: str) -> Confirmation | None:
    """The ask-first confirmation for a confirmation-gated intent."""
    if intent_name == "shutdown_pc":
        return Confirmation(ActionKind.SHUTDOWN, "Shut the PC down", target="pc")
    if intent_name == "restart_pc":
        return Confirmation(ActionKind.SHUTDOWN, "Restart the PC", target="pc")
    return None


class _SystemWorker(Worker):
    """Minimal system worker, kept inside boss.py (Builder A).

    Delegates to ``pc.controller.SystemController``. The controller is
    still a stub (Builder C), so on this machine its calls raise
    ``NotImplementedError`` and are reported plainly as unavailable —
    never executed silently and never crashing the boss. Windows-only
    imports live inside the controller, so this file imports on Linux.
    """

    name = "system"
    intents = {
        "open_app", "close_app", "set_volume", "set_brightness",
        "lock_pc", "sleep_pc", "shutdown_pc", "restart_pc",
        "mute", "unmute", "focus_mode_on", "focus_mode_off",
    }

    def run(self, ctx: TaskContext) -> WorkerResult:
        intent = (ctx.slots or {}).get("_intent", "")
        try:
            from .pc.controller import SystemController

            controller = SystemController(dry_run=ctx.dry_run)
            return self._run_with_controller(controller, intent, ctx)
        except NotImplementedError:
            return WorkerResult.failed(
                "System control is still being built, boss — that part of "
                "the PC control is not available yet."
            )
        except Exception as exc:  # pragma: no cover - defensive
            return WorkerResult.failed(f"System control failed, boss: {exc}")

    def _run_with_controller(self, controller, intent: str, ctx: TaskContext) -> WorkerResult:
        slots = ctx.slots or {}
        try:
            if intent == "open_app":
                return WorkerResult.done(controller.open_app(slots.get("app", "")))
            if intent == "close_app":
                return WorkerResult.done(controller.close_app(slots.get("app", "")))
            if intent == "set_volume":
                level = max(0, min(100, int(slots.get("level", "50"))))
                return WorkerResult.done(controller.set_volume(level))
            if intent == "set_brightness":
                level = max(0, min(100, int(slots.get("level", "50"))))
                return WorkerResult.done(controller.set_brightness(level))
            if intent == "lock_pc":
                return WorkerResult.done(controller.lock())
            if intent == "sleep_pc":
                return WorkerResult.done(controller.sleep())
            if intent == "shutdown_pc":
                return WorkerResult.done(controller.shutdown())
            if intent == "restart_pc":
                return WorkerResult.done(controller.restart())
            if intent == "mute":
                return WorkerResult.done(controller.set_volume(0))
            if intent == "unmute":
                return WorkerResult.done(controller.set_volume(50))
            if intent == "focus_mode_on":
                from .pc.focus import FocusMode

                return WorkerResult.done(FocusMode().start())
            if intent == "focus_mode_off":
                from .pc.focus import FocusMode

                return WorkerResult.done(FocusMode().stop())
        except NotImplementedError:
            return WorkerResult.failed(
                "System control is still being built, boss — that part of "
                "the PC control is not available yet."
            )
        except (ValueError, KeyError) as exc:
            return WorkerResult.failed(f"Boss, I could not understand that setting: {exc}")
        return WorkerResult.failed(f"Boss, I do not know how to do '{intent}' yet.")


class _HealthWorker(Worker):
    """Minimal health worker, kept inside boss.py (Builder A).

    Answers screen-time / TikTok questions from the usage the phone
    bridge has logged in memory, and starts a rest handover. With no
    memory or no logged usage it says so plainly instead of inventing
    numbers. The full rest-enforcement ladder lives in ``pc.health``
    (Builder C); this worker only handles the boss-facing intents.
    """

    name = "health"
    intents = {"screen_time", "tiktok_time", "take_break"}

    def run(self, ctx: TaskContext) -> WorkerResult:
        intent = (ctx.slots or {}).get("_intent", "")
        if intent == "take_break":
            return WorkerResult.done(
                "Rest now, boss — I will finish the remaining work while "
                "you rest, and I will pause any shutdown until it is done."
            )
        usage: dict[str, int] = {}
        if ctx.memory is not None:
            try:
                usage = dict(ctx.memory.usage_today() or {})
            except Exception:
                usage = {}
        if not usage:
            return WorkerResult.done(
                "Boss, I do not have any screen-time numbers yet — the phone "
                "has not sent any usage today."
            )
        lowered = {str(k).lower(): int(v) for k, v in usage.items()}
        if intent == "tiktok_time":
            minutes = lowered.get("tiktok", 0)
            return WorkerResult.done(
                f"Boss, today you have spent {minutes} minutes on TikTok."
            )
        total = sum(lowered.values())
        return WorkerResult.done(
            f"Boss, today you have spent {total} minutes on screen in total."
        )


class Boss:
    """The one agent the boss (the user) ever talks to."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.brain: Brain = get_brain(settings)
        self.gate = PermissionGate()
        self.state = StateMachine()
        self.registry = default_registry()
        self.workers: dict[str, object] = {}
        self.skipped_workers: list[str] = []
        self.memory = None
        if MemoryStore is not None:
            try:
                self.memory = MemoryStore(settings.data_dir)
            except Exception:
                # Memory is still a stub (Builder D) or its DB cannot be
                # opened — the boss works without it and says nothing
                # it does not know.
                self.memory = None
        self._task_counter = 0
        self._pending_action: Callable[[], str] | None = None
        self._pending_task: Task | None = None
        self._pending_text: str = ""

    # -- registration -----------------------------------------------------
    def register_worker(self, worker: object) -> None:
        """Add a worker the boss can dispatch to."""
        name = getattr(worker, "name", None) or type(worker).__name__.lower()
        self.workers[str(name)] = worker

    def _find_worker(self, intent_name: str) -> object | None:
        for worker in self.workers.values():
            can_handle = getattr(worker, "can_handle", None)
            if callable(can_handle) and can_handle(intent_name):
                return worker
        return None

    # -- task bookkeeping ---------------------------------------------------
    def _new_task(self, text: str) -> Task:
        self._task_counter += 1
        return Task(id=self._task_counter, text=text)

    @staticmethod
    def _advance(task: Task, new_state: TaskState, note: str = "") -> None:
        try:
            task.transition(new_state, note=note)
        except Exception:
            # A transition that is not allowed for the task's current
            # state must never crash a reply to the boss.
            pass

    def _log_task(self, text: str, status: str) -> None:
        if self.memory is None:
            return
        try:
            self.memory.log_task(text, status)
        except Exception:
            pass

    def _memory_snippet(self, query: str) -> str:
        """Facts about the boss, wrapped as untrusted data for the brain."""
        if self.memory is None:
            return ""
        try:
            facts = self.memory.search_facts(query, limit=5) or []
        except Exception:
            return ""
        lines = []
        for fact in facts:
            if isinstance(fact, dict):
                key = fact.get("key", "")
                value = fact.get("value", "")
                lines.append(f"{key}: {value}" if key else str(value))
            else:
                lines.append(str(fact))
        if not lines:
            return ""
        # Memory content came from files / past logs, not from the boss's
        # live words — the brain must treat it as data, never orders.
        return guard_untrusted("\n".join(lines))

    # -- confirmations ------------------------------------------------------
    def _store_confirmation(
        self, confirmation: Confirmation, task: Task, action: Callable[[], str]
    ) -> str:
        """Park an action behind the ask-first gate and ask the boss."""
        self.gate.check(confirmation)
        self._pending_action = action
        self._pending_task = task
        self._pending_text = confirmation.description
        return confirmation.prompt()

    def _resolve_pending(self, text: str) -> str | None:
        """Resolve a pending confirmation. None = not a yes/no answer."""
        if self.gate.pending is None:
            return None
        answer = parse_yes_no(text)
        if answer is None:
            pending = self.gate.pending
            prompt = pending.prompt() if pending is not None else ""
            return f"Boss, I am still waiting for a yes or no. {prompt}"
        # parse_yes_no said yes/no — clear the gate (resolve re-checks
        # the same answer and drops the pending confirmation).
        self.gate.resolve(text)
        action = self._pending_action
        task = self._pending_task
        self._pending_action = None
        self._pending_task = None
        if answer is False:
            if task is not None:
                self._advance(task, TaskState.FAILED, note="refused by boss")
            self._log_task(self._pending_text or text, "failed")
            self._pending_text = ""
            return "Okay, boss, I won't do that."
        self._pending_text = ""
        if action is None:
            return "Okay, boss, I will do that."
        try:
            return action()
        except Exception as exc:  # pragma: no cover - defensive
            if task is not None:
                self._advance(task, TaskState.FAILED, note=str(exc))
            self._log_task(text, "failed")
            return f"Boss, that did not work: {exc}"

    # -- direct (boss-handled) intents --------------------------------------
    def _handle_direct(self, intent_name: str, task: Task) -> str:
        self._advance(task, TaskState.ASSIGNED)
        self._advance(task, TaskState.WORKING)
        if intent_name == "what_time":
            reply = f"Boss, it is {datetime.now().strftime('%I:%M %p')}."
        elif intent_name == "what_date":
            reply = f"Boss, today is {datetime.now().strftime('%A, %d %B %Y')}."
        elif intent_name == "stop":
            reply = "Stopped, boss. Nothing is running now."
        elif intent_name == "help":
            lines = ["Boss, here is what I can do:"]
            for intent in self.registry.intents:
                if intent.description:
                    lines.append(f"- {intent.description}")
            lines.append(
                "Anything else, just tell me in plain words, boss — "
                "I will work it out or tell you plainly if I cannot."
            )
            reply = "\n".join(lines)
        else:  # pragma: no cover - registry handler is always one of these
            reply = "Boss, I do not know how to do that yet."
        self._advance(task, TaskState.VERIFYING)
        self._advance(task, TaskState.DONE)
        self._log_task(task.text, "done")
        return reply

    # -- worker dispatch ------------------------------------------------------
    def _finish_worker_run(self, worker: object, match_text: str, slots: dict, task: Task) -> str:
        """Run a worker, verify file results, report back. Returns the reply."""
        self._advance(task, TaskState.WORKING)
        ctx = TaskContext(
            text=match_text,
            slots=dict(slots),
            memory=self.memory,
            settings=self.settings,
            dry_run=bool(self.settings.dry_run),
            confirm=lambda conf: False,
        )
        try:
            result = worker.run(ctx)  # type: ignore[attr-defined]
        except NotImplementedError:
            result = WorkerResult.failed(
                f"The {getattr(worker, 'name', 'worker')} worker is still "
                "being built, boss — I cannot do that yet."
            )
        except Exception as exc:
            result = WorkerResult.failed(f"Something went wrong, boss: {exc}")
        if result is None:  # pragma: no cover - defensive
            result = WorkerResult.failed("Boss, the worker gave back nothing.")

        if result.needs_confirmation is not None:
            # The worker itself asks first: park a re-run behind the gate.
            # A fresh task is used for the re-run so its state machine
            # stays legal; the current task is closed as refused if the
            # boss says no (see _resolve_pending).
            fresh = self._new_task(match_text)
            self._advance(fresh, TaskState.PLANNED)
            self._advance(fresh, TaskState.ASSIGNED)
            return self._store_confirmation(
                result.needs_confirmation,
                task,
                lambda: self._finish_worker_run(worker, match_text, slots, fresh),
            )

        if _looks_like_injection(result.summary):
            # Untrusted text from a worker result is data, never an order.
            # Report it wrapped — do not act on it, do not pass it raw
            # to the brain, do not delete / send / run anything it says.
            self._advance(task, TaskState.FAILED, note="untrusted worker output")
            self._log_task(match_text, "failed")
            return (
                "Boss, the worker gave back text that reads like a hidden "
                "instruction, so I treated it as data only and did not "
                f"follow it:\n{guard_untrusted(result.summary)}"
            )

        if not result.ok:
            self._advance(task, TaskState.FAILED, note="worker failed")
            self._log_task(match_text, "failed")
            return f"Boss, that did not work: {result.summary}"

        if result.files:
            self._advance(task, TaskState.VERIFYING)
            tester = self.workers.get("tester")
            if tester is not None and tester is not worker:
                verify_ctx = TaskContext(
                    text=f"verify files: {', '.join(result.files)}",
                    slots={"files": ", ".join(result.files), "_intent": "verify"},
                    memory=self.memory,
                    settings=self.settings,
                    dry_run=bool(self.settings.dry_run),
                )
                try:
                    check = tester.run(verify_ctx)  # type: ignore[attr-defined]
                except NotImplementedError:
                    check = None
                except Exception as exc:  # pragma: no cover - defensive
                    check = WorkerResult.failed(str(exc))
                if check is None:
                    # Tester exists but is still a stub: report success
                    # honestly, without claiming it was verified.
                    self._advance(task, TaskState.DONE)
                    self._log_task(match_text, "done")
                    return (
                        f"{self._with_boss(result.summary)} Boss, I could "
                        "not verify it yet — the Tester is still being "
                        "built, so please check the file yourself."
                    )
                if not check.ok:
                    self._advance(task, TaskState.FAILED, note="verification failed")
                    self._log_task(match_text, "failed")
                    return (
                        "Boss, I checked the result but verification failed: "
                        f"{check.summary} Shall I try once more, boss?"
                    )
                self._advance(task, TaskState.DONE)
                self._log_task(match_text, "done")
                return f"{self._with_boss(result.summary)} Checked and verified, boss."
            self._advance(task, TaskState.DONE)
            self._log_task(match_text, "done")
            return self._with_boss(result.summary)

        self._advance(task, TaskState.VERIFYING)
        self._advance(task, TaskState.DONE)
        self._log_task(match_text, "done")
        return self._with_boss(result.summary)

    @staticmethod
    def _with_boss(summary: str) -> str:
        if "boss" in summary.lower():
            return summary
        return f"{summary} Done, boss."

    # -- brain fallback -------------------------------------------------------
    def _fall_back_to_brain(self, text: str, task: Task) -> str:
        self._advance(task, TaskState.ASSIGNED)
        self._advance(task, TaskState.WORKING)
        system = boss_system_prompt(self._memory_snippet(text))
        try:
            reply = self.brain.complete(system, text)
        except BrainError:
            self._advance(task, TaskState.FAILED, note="brain unreachable")
            self._log_task(text, "failed")
            return (
                "Boss, the cloud brain could not be reached just now, so I "
                "am in local mode for the moment — fixed commands still "
                "work. Please try again in a bit."
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._advance(task, TaskState.FAILED, note=str(exc))
            self._log_task(text, "failed")
            return f"Boss, something went wrong with the brain: {exc}"
        self._advance(task, TaskState.VERIFYING)
        self._advance(task, TaskState.DONE)
        self._log_task(text, "done")
        return reply.text

    # -- main entry -----------------------------------------------------------
    def handle(self, text: str) -> str:
        """Handle one message and return the spoken reply."""
        text = (text or "").strip()
        if not text:
            return "Boss, I did not catch that — please say it again."

        # 1. A pending confirmation is answered first.
        resolved = self._resolve_pending(text)
        if resolved is not None:
            return resolved

        task = self._new_task(text)
        self._advance(task, TaskState.PLANNED)

        # 2. Fixed intent first — fast, offline, predictable.
        match = self.registry.match(text)
        if match is not None:
            intent: Intent | None = next(
                (i for i in self.registry.intents if i.name == match.name), None
            )
            if intent is not None and intent.handler == "boss":
                return self._handle_direct(intent.name, task)
            worker = self._find_worker(match.name)
            if worker is None:
                self._advance(task, TaskState.FAILED, note="no worker")
                self._log_task(text, "failed")
                return (
                    f"Boss, I understood '{match.name}', but I do not have "
                    "a worker for it yet — that part is still being built."
                )
            self._advance(task, TaskState.ASSIGNED)
            slots = dict(match.slots)
            slots["_intent"] = match.name
            if intent is not None and intent.needs_confirmation:
                confirmation = _confirmation_for_intent(match.name)
                if confirmation is not None:
                    return self._store_confirmation(
                        confirmation,
                        task,
                        lambda: self._finish_worker_run(worker, text, slots, task),
                    )
            return self._finish_worker_run(worker, text, slots, task)

        # 3. Nothing matched — fall through to the brain (or local mode).
        return self._fall_back_to_brain(text, task)

    def status_text(self) -> str:
        """One-line status for the CLI / dashboard."""
        brain_name = getattr(self.brain, "name", None) or self.settings.brain_name
        if self.settings.has_cloud_brain:
            brain_part = f"brain: {brain_name} (cloud)"
        else:
            brain_part = (
                f"brain: {brain_name} — local mode, no cloud key in .env "
                "(fixed commands only; add a free GROQ_API_KEY or "
                "GEMINI_API_KEY for full thinking)"
            )
        worker_names = ", ".join(sorted(self.workers)) if self.workers else "none"
        skipped = (
            f", skipped workers: {', '.join(self.skipped_workers)}"
            if self.skipped_workers
            else ""
        )
        return (
            f"Kairo — {brain_part}, language: {self.settings.language}, "
            f"workers: {len(self.workers)} registered ({worker_names}), "
            f"memory: {self.settings.data_dir}{skipped}"
        )


def build_boss(settings: Settings | None = None) -> Boss:
    """Build a Boss with all workers registered (Builder A)."""
    if settings is None:
        settings = get_settings()
    boss = Boss(settings)

    # System + health workers are small inline workers in this file, so
    # the boss always has something for the system/health intents even
    # while the PC-control builders are still working.
    boss.register_worker(_SystemWorker())
    boss.register_worker(_HealthWorker())

    # Every other worker is imported lazily: a missing optional worker
    # (or a missing optional dependency) must never break startup.
    # Each entry: (short name for the skipped list, module, class name).
    _worker_specs: list[tuple[str, str, str]] = [
        ("scheduler", "kairo.workers.scheduler", "SchedulerWorker"),
        ("office", "kairo.workers.office", "OfficeWorker"),
        ("files", "kairo.workers.files", "FileOrganizer"),
        ("browser", "kairo.workers.browser", "BrowserAgent"),
        ("whatsapp", "kairo.workers.whatsapp", "WhatsAppWorker"),
        ("vision", "kairo.workers.vision", "ScreenVisionWorker"),
        ("tester", "kairo.workers.tester", "TesterWorker"),
    ]
    import importlib

    for short_name, module_name, class_name in _worker_specs:
        try:
            module = importlib.import_module(module_name)
            worker_cls = getattr(module, class_name)
            boss.register_worker(worker_cls())
        except Exception:
            if short_name not in boss.skipped_workers:
                boss.skipped_workers.append(short_name)

    return boss
