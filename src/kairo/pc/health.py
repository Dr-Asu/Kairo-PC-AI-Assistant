"""Health protection — the rest-enforcement ladder, PC only (plan).

Step 1 — gentle warning after 2h continuous use.
Step 2 — firm warning with a 10-minute countdown after another hour
         with no 10-minute break.
Step 3 — close applications (save what it can, plain report, final
         60-second "say stop to cancel" window).
Step 4 — save everything it can and shut the PC down (or Sleep, if that
         is the setting) at 5h continuous use or 2 AM, whichever first.
Snooze: "30 more minutes", max 2 times, then it enforces.

"Continuous use" means the PC is actually being used (mouse, keyboard,
screen on) — idle time does not count. Every threshold is a default from
``Settings`` and can be changed.

Rest handover: when the boss is finishing remaining work while the boss
(the user) rests, the ladder pauses the shutdown step until the work is
done — then the PC sleeps. ``rest_handover_active`` is set by the boss.

Builder C owns this file. Keep the public API exactly as declared here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..config import Settings

#: Minutes of real break that reset the ladder (plan section 6).
BREAK_MINUTES = 10
#: Gap between the step-1 warning and the step-2 firm warning.
STEP2_GAP_MIN = 60
#: Length of the step-2 countdown before apps are closed.
COUNTDOWN_MIN = 10
#: Snooze length, and how many snoozes are allowed per ladder run.
SNOOZE_MIN = 30
MAX_SNOOZES = 2


@dataclass
class LadderEvent:
    """One thing the ladder wants the boss to say or do."""

    step: int  # 0 = no action, 1..4 = ladder steps
    message: str  # plain words for the boss, e.g. the spoken warning
    action: str = ""  # "warn" | "countdown" | "close_apps" | "shutdown" | ""


@dataclass
class HealthLadder:
    """Track continuous use and fire ladder steps. Builder C implements."""

    settings: Settings
    continuous_minutes: int = 0
    snoozes_used: int = 0
    break_minutes: int = 0  # minutes away from the keyboard so far
    rest_handover_active: bool = False
    sleep_instead: bool = False  # step 4 sleeps the PC instead of shutting down
    _step_reached: int = field(default=0, repr=False)
    _extra_minutes: int = field(default=0, repr=False)  # added by snoozes
    _close_pending: bool = field(default=False, repr=False)  # step 3 60s window

    # -- thresholds (snoozes push every later step out) --------------------
    @property
    def _step1_at(self) -> int:
        return self.settings.sitting_warn_min + self._extra_minutes

    @property
    def _step2_at(self) -> int:
        return self._step1_at + STEP2_GAP_MIN

    @property
    def _step3_at(self) -> int:
        return self._step2_at + COUNTDOWN_MIN

    @property
    def _step4_at(self) -> int:
        return self.settings.hard_limit_hours * 60 + self._extra_minutes

    def tick(self, active: bool) -> LadderEvent | None:
        """Call once a minute. ``active`` = mouse/keyboard used this minute.

        Returns a LadderEvent when a step fires, else None.
        """
        if active:
            self.continuous_minutes += 1
            self.break_minutes = 0
        else:
            self.break_minutes += 1
            if self.break_minutes >= BREAK_MINUTES:
                self.reset_after_break()
            return None

        # Step 4 by the clock: bedtime (default 02:00), whichever comes first.
        now = datetime.now().strftime("%H:%M")
        bedtime_hit = now == self.settings.bedtime and self.continuous_minutes >= 1
        step4_due = self.continuous_minutes >= self._step4_at or bedtime_hit

        if self._step_reached < 4 and step4_due:
            if self.rest_handover_active:
                # Shutdown waits for the remaining work; do not advance the
                # step, so it fires properly once the handover ends.
                return LadderEvent(
                    step=0,
                    message=(
                        "Boss, the shutdown is waiting — the remaining work is "
                        "still running, so I will put the PC to sleep once it is done."
                    ),
                )
            self._step_reached = 4
            if self.sleep_instead:
                return LadderEvent(
                    step=4,
                    message=(
                        "Boss, you have reached the limit — I saved what I could "
                        "and I am putting the PC to sleep now. Please rest."
                    ),
                    action="sleep",
                )
            return LadderEvent(
                step=4,
                message=(
                    "Boss, you have reached the limit — I saved what I could "
                    "and I am shutting the PC down now. Please rest."
                ),
                action="shutdown",
            )

        if self._step_reached < 3 and self.continuous_minutes >= self._step3_at:
            self._step_reached = 3
            self._close_pending = True
            return LadderEvent(
                step=3,
                message=(
                    "Boss, I saved what I could, and I am closing your apps now. "
                    "Say 'stop' within 60 seconds to cancel — otherwise they close."
                ),
                action="close_apps",
            )

        if self._step_reached < 2 and self.continuous_minutes >= self._step2_at:
            self._step_reached = 2
            return LadderEvent(
                step=2,
                message=(
                    "Boss, I will close your files in 10 minutes if you do not "
                    "take a rest. Please take a break now."
                ),
                action="countdown",
            )

        if self._step_reached < 1 and self.continuous_minutes >= self._step1_at:
            self._step_reached = 1
            warn = self.settings.sitting_warn_min
            if warn >= 120:
                message = "Boss, you have been working for two hours, please take a rest."
            elif warn == 60:
                message = "Boss, you have been working for one hour, please take a rest."
            else:
                message = f"Boss, you have been working for {warn} minutes, please take a rest."
            return LadderEvent(step=1, message=message, action="warn")

        return None

    def snooze(self) -> str:
        """'30 more minutes' — max 2 times, then it enforces."""
        if self.snoozes_used >= MAX_SNOOZES:
            return (
                "Sorry boss, no more snoozes — you have already used both. "
                "Please take a rest now, the ladder will enforce it."
            )
        self.snoozes_used += 1
        self._extra_minutes += SNOOZE_MIN
        self._close_pending = False
        return (
            f"OK boss, 30 more minutes. That is snooze {self.snoozes_used} of "
            f"{MAX_SNOOZES}."
        )

    def stop(self) -> str:
        """'stop' — cancels the current step, but the ladder keeps counting."""
        self._close_pending = False
        return (
            "OK boss, stopped — the apps stay open for now. The ladder keeps "
            "counting though, so please take a rest soon."
        )

    def reset_after_break(self) -> None:
        """A real break (10+ minutes away) resets the ladder."""
        self.continuous_minutes = 0
        self.break_minutes = 0
        self.snoozes_used = 0
        self._step_reached = 0
        self._extra_minutes = 0
        self._close_pending = False
