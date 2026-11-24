"""Usage tracking — which app, how long (plan section 5).

On Windows this reads the foreground window (pywin32, guarded). On Linux
it falls back to the most CPU-active process via psutil, and reports the
app name as best-effort. Minutes are written to the memory store by the
boss; this module only measures.

Builder C owns this file.
"""

from __future__ import annotations

import platform
from datetime import date


class UsageTracker:
    """Measure active use. ``tick()`` is called once a minute by the boss."""

    def __init__(self, memory=None) -> None:
        self.memory = memory
        self.minutes_today: dict[str, int] = {}

    def active_app(self) -> str:
        """Name of the app currently in the foreground ('unknown' if unsure)."""
        if platform.system() == "Windows":
            try:
                import win32gui

                title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
                return title.strip() or "unknown"
            except ImportError:
                return "unknown"
            except Exception:  # noqa: BLE001 - best effort only
                return "unknown"
        # Linux: most CPU-active process is the best proxy for "in use".
        try:
            import psutil

            best_name = "unknown"
            best_cpu = 0.0
            for proc in psutil.process_iter(["name", "cpu_percent"]):
                try:
                    cpu = proc.info["cpu_percent"] or 0.0
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                if cpu > best_cpu:
                    best_cpu = cpu
                    best_name = proc.info["name"] or "unknown"
            return best_name
        except ImportError:
            return "unknown"
        except Exception:  # noqa: BLE001 - best effort only
            return "unknown"

    def tick(self) -> str:
        """Record one minute of use for the active app. Returns the app name."""
        app = self.active_app()
        key = app.lower()
        self.minutes_today[key] = self.minutes_today.get(key, 0) + 1
        if self.memory is not None:
            try:
                self.memory.log_usage(date.today().isoformat(), app, 1)
            except Exception:  # noqa: BLE001 - memory is best-effort here
                pass
        return app

    def minutes_for(self, app: str) -> int:
        return self.minutes_today.get(app.lower(), 0)

    def total_minutes(self) -> int:
        return sum(self.minutes_today.values())
