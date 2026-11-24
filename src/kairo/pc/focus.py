"""Focus mode — silence notifications, block distracting apps.

You say "focus mode" and it switches on Windows Do Not Disturb and
enforces a block list of apps (TikTok on PC, social apps, anything you
name) until you say "stop focus mode" or the time you set ends.
Builder C owns this file.
"""

from __future__ import annotations


class FocusMode:
    """Guard the boss's time. Builder C implements this."""

    def __init__(self, blocked_apps: list[str] | None = None) -> None:
        self.blocked_apps = blocked_apps or ["tiktok", "facebook", "instagram", "youtube"]
        self.active = False
        self.minutes_left: int | None = None

    def start(self, minutes: int | None = None) -> str:
        self.active = True
        self.minutes_left = minutes
        if minutes:
            return f"Focus mode is on, boss — for {minutes} minutes. Distracting apps are blocked."
        return "Focus mode is on, boss. Distracting apps are blocked."

    def stop(self) -> str:
        self.active = False
        self.minutes_left = None
        return "Focus mode is off, boss."

    def is_blocked(self, app: str) -> bool:
        return self.active and any(b in app.lower() for b in self.blocked_apps)
