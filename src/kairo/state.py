"""State machines — the assistant always knows where it is.

Two machines, both explicit (research lesson 4: barge-in and an explicit
state machine from day one):

* ``AssistantState`` — what the voice loop is doing right now. Barge-in is
  the ``SPEAKING -> LISTENING`` transition: when the boss speaks while the
  assistant is speaking, the assistant stops and listens.
* ``TaskState`` — where a job is in the boss's pipeline:
  received -> planned -> assigned -> working -> verifying -> done/failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssistantState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CONFIRMING = "confirming"
    WORKING = "working"
    ERROR = "error"


class TaskState(Enum):
    RECEIVED = "received"
    PLANNED = "planned"
    ASSIGNED = "assigned"
    WORKING = "working"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"


# Allowed transitions for a task. Anything else is a bug, not a shortcut.
TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.RECEIVED: {TaskState.PLANNED, TaskState.FAILED},
    TaskState.PLANNED: {TaskState.ASSIGNED, TaskState.FAILED},
    TaskState.ASSIGNED: {TaskState.WORKING, TaskState.FAILED},
    TaskState.WORKING: {TaskState.VERIFYING, TaskState.FAILED},
    TaskState.VERIFYING: {TaskState.DONE, TaskState.FAILED, TaskState.WORKING},
    TaskState.DONE: set(),
    TaskState.FAILED: {TaskState.RECEIVED},  # retry starts over
}


class InvalidTransition(ValueError):
    """Raised when a task tries to move to a state it cannot reach."""


@dataclass
class Task:
    """One job the boss is running."""

    id: int
    text: str
    state: TaskState = TaskState.RECEIVED
    result: str = ""
    history: list[str] = field(default_factory=list)

    def transition(self, new_state: TaskState, note: str = "") -> None:
        allowed = TASK_TRANSITIONS[self.state]
        if new_state not in allowed:
            raise InvalidTransition(f"task {self.id}: {self.state.value} -> {new_state.value} is not allowed")
        self.history.append(f"{self.state.value} -> {new_state.value}" + (f" ({note})" if note else ""))
        self.state = new_state


@dataclass
class StateMachine:
    """The voice loop's state, with barge-in built in."""

    state: AssistantState = AssistantState.IDLE
    history: list[str] = field(default_factory=list)

    def transition(self, new_state: AssistantState, note: str = "") -> None:
        self.history.append(f"{self.state.value} -> {new_state.value}" + (f" ({note})" if note else ""))
        self.state = new_state

    def interrupt(self) -> bool:
        """Barge-in: the boss speaks while the assistant is speaking.

        Returns True if there was speech to stop. The assistant stops
        speaking immediately and starts listening.
        """
        if self.state is AssistantState.SPEAKING:
            self.transition(AssistantState.LISTENING, note="barge-in")
            return True
        return False

    def reset(self) -> None:
        self.transition(AssistantState.IDLE, note="reset")
