"""Background service — usage tracking + health ladder, ticking every minute.

The assistant watches the clock even when nobody is talking to it: once
a minute it records which app is in use and asks the health ladder
whether a warning (or an enforcement step) is due. When the ladder fires,
the ``on_event`` callback is called with the ladder event — the caller
decides how to deliver it (speak it, print it, show a toast).

Activity detection: on Windows it uses GetLastInputInfo (real idle time).
On other platforms it is approximate — the assistant is running in an
interactive session, so activity is assumed unless a custom ``activity``
callable is given. This is said plainly in the docstring, not hidden.

The service never shuts anything down by itself: a shutdown event is
just an event; the boss (or the caller) decides what to do with it, and
the ask-first gate still applies.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .config import Settings
from .pc.health import HealthLadder, LadderEvent
from .pc.usage import UsageTracker


def _windows_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input on Windows, else None."""
    try:
        import ctypes

        class _LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return None


def default_activity() -> bool:
    """True when the boss is actively using the PC right now.

    Windows: idle for less than 60 seconds counts as active. Anywhere
    else: assumed active while the assistant is running (approximate —
    see the module docstring).
    """
    idle = _windows_idle_seconds()
    if idle is None:
        return True
    return idle < 60.0


class BackgroundService:
    """Tick usage + health ladder on a daemon thread. Coordinator-owned."""

    def __init__(
        self,
        settings: Settings,
        memory=None,
        on_event: Callable[[LadderEvent], None] | None = None,
        activity: Callable[[], bool] | None = None,
        interval_seconds: float = 60.0,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.on_event = on_event or (lambda event: print(f"Kairo: {event.message}"))
        self.activity = activity or default_activity
        self.interval_seconds = interval_seconds
        self.usage = UsageTracker(memory=memory)
        self.ladder = HealthLadder(settings=settings)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick_once(self) -> LadderEvent | None:
        """One minute of work: record usage, ask the ladder. Returns the event, if any."""
        active = bool(self.activity())
        try:
            self.usage.tick()
        except Exception:
            pass  # tracking must never break the service
        try:
            event = self.ladder.tick(active)
        except Exception:
            return None
        if event is not None and event.step > 0:
            self.on_event(event)
        return event

    def start(self) -> None:
        """Start ticking on a daemon thread. Safe to call twice."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="kairo-background")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.tick_once()

    def stop(self) -> None:
        """Stop the background thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def snooze(self) -> str:
        return self.ladder.snooze()

    def stop_ladder(self) -> str:
        return self.ladder.stop()
