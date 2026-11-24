"""Tests for the background service (usage ticking + health ladder)."""

from kairo.config import Settings
from kairo.service import BackgroundService


def _settings(**overrides):
    import tempfile

    s = Settings(data_dir=tempfile.mkdtemp())
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_tick_records_usage_and_fires_step_one():
    events = []
    svc = BackgroundService(
        _settings(sitting_warn_min=2),
        on_event=events.append,
        activity=lambda: True,
        interval_seconds=60,
    )
    assert svc.tick_once() is None  # minute 1 — nothing yet
    event = svc.tick_once()  # minute 2 — step 1 fires
    assert event is not None and event.step == 1
    assert "two hours" in event.message or "rest" in event.message
    assert len(events) == 1
    assert svc.usage.total_minutes() == 2


def test_idle_minutes_count_as_break_and_reset():
    svc = BackgroundService(
        _settings(sitting_warn_min=2),
        activity=lambda: True,
    )
    svc.tick_once()
    svc.tick_once()  # step 1 fired
    svc.activity = lambda: False
    for _ in range(10):
        svc.tick_once()  # 10 idle minutes -> ladder resets
    assert svc.ladder.continuous_minutes == 0
    assert svc.ladder._step_reached == 0


def test_rest_handover_defers_shutdown():
    svc = BackgroundService(
        _settings(sitting_warn_min=1, hard_limit_hours=1),
        on_event=lambda event: None,
        activity=lambda: True,
    )
    svc.ladder.rest_handover_active = True
    for _ in range(70):  # past the 1-hour hard limit
        event = svc.tick_once()
    # No shutdown event while the handover is active.
    assert svc.ladder._step_reached < 4


def test_start_and_stop_thread():
    svc = BackgroundService(_settings(), interval_seconds=0.05)
    svc.start()
    svc.start()  # second start is a no-op
    import time

    time.sleep(0.2)
    svc.stop()
    assert svc.usage.total_minutes() >= 1
    assert svc._thread is None
