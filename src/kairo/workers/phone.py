"""Phone bridge — the TECNO companion app talks to the PC over HTTP.

The companion app posts app-usage minutes (UsageStatsManager) and health
numbers (Health Connect) to the PC over home Wi-Fi, and can send a voice
command from the phone to the PC (over Tailscale when outside). Every
request carries the secret token from ``KAIRO_TOKEN`` — only the boss's
phone can talk to his PC.

The Android companion app itself is out of scope for v0.1; this module
defines the exact HTTP contract it will use (see README + STATUS.md).
Builder F owns this file.
"""

from __future__ import annotations

from datetime import date


def _today() -> str:
    return date.today().isoformat()


def record_usage(memory, payload: dict) -> dict:
    """Store app-usage minutes posted by the phone.

    Payload: ``{"date": "YYYY-MM-DD", "apps": {"TikTok": 45, ...}}``.
    Writes one ``memory.log_usage(date, app, minutes)`` row per app and
    returns ``{"ok": True, "apps": n}``.
    """
    if memory is None:
        raise ValueError("No memory store — phone usage cannot be recorded.")
    payload = payload or {}
    when = str(payload.get("date") or _today())
    apps = payload.get("apps") or {}
    if not isinstance(apps, dict):
        raise ValueError("Phone usage payload needs an 'apps' object of {app: minutes}.")
    count = 0
    for app, minutes in apps.items():
        try:
            minutes_int = int(minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Minutes for {app!r} must be a number, got {minutes!r}.") from exc
        memory.log_usage(when, str(app), minutes_int)
        count += 1
    return {"ok": True, "apps": count}


def record_health(memory, payload: dict) -> dict:
    """Store health numbers posted by the phone.

    Payload: ``{"date": ..., "steps": n, "sleep_hours": x, "heart_rate": n}``.
    Stores one fact per metric (``health_steps_<date>`` and friends) via
    ``memory.add_fact`` and returns ``{"ok": True}``.
    """
    if memory is None:
        raise ValueError("No memory store — phone health data cannot be recorded.")
    payload = payload or {}
    when = str(payload.get("date") or _today())
    stored = 0
    for key in ("steps", "sleep_hours", "heart_rate"):
        if key in payload and payload[key] is not None:
            memory.add_fact(
                f"health_{key}_{when}", str(payload[key]), source="phone"
            )
            stored += 1
    return {"ok": True, "stored": stored}
