"""Worker base — every specialist agent implements this interface.

A worker does ONE kind of job (Excel, files, browser, WhatsApp, vision,
health, ...). The boss assigns work to workers; workers never talk to the
boss's user directly. A worker returns a WorkerResult, and the boss (plus
the Tester) verifies it before anything is delivered.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..safety import Confirmation


@dataclass
class TaskContext:
    """Everything a worker needs to do its job."""

    text: str  # the boss's original words for this step
    slots: dict[str, str] = field(default_factory=dict)  # intent slots
    memory: Any = None  # MemoryStore (set by the boss)
    settings: Any = None  # Settings (set by the boss)
    dry_run: bool = False  # True in tests / on non-Windows machines
    confirm: Any = None  # callable(Confirmation) -> bool, set by the boss


@dataclass
class WorkerResult:
    """What a worker gives back to the boss."""

    ok: bool
    summary: str  # plain words for the boss, e.g. "Opened Notepad."
    data: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)  # files created/changed
    needs_confirmation: Confirmation | None = None  # set when ask-first applies

    @classmethod
    def done(cls, summary: str, **data: Any) -> "WorkerResult":
        return cls(ok=True, summary=summary, data=data)

    @classmethod
    def failed(cls, summary: str, **data: Any) -> "WorkerResult":
        return cls(ok=False, summary=summary, data=data)


class Worker(ABC):
    """One specialist. Subclass and implement ``run``."""

    name: str = "worker"
    # Intent names this worker handles (see intents.py), e.g. {"open_app"}.
    intents: set[str] = set()

    def can_handle(self, intent_name: str) -> bool:
        return intent_name in self.intents

    @abstractmethod
    def run(self, ctx: TaskContext) -> WorkerResult:
        """Do the work. Never send/delete/install without ask-first."""
        raise NotImplementedError
