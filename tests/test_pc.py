"""Tests for the PC control layer — Builder C.

Dry-run and Linux paths run everywhere. Anything that needs a real
Windows desktop is marked windows_only and skipped elsewhere.
"""

import sys

import pytest

from kairo.config import Settings
from kairo.pc.controller import NotAvailable, SystemController
from kairo.pc.focus import FocusMode
from kairo.pc.health import HealthLadder
from kairo.pc.tray import run_tray
from kairo.pc.usage import UsageTracker


def _drive(ladder: HealthLadder, minutes: int, active: bool = True):
    """Tick ``minutes`` times; return the last non-None event, if any."""
    last = None
    for _ in range(minutes):
        event = ladder.tick(active)
        if event is not None:
            last = event
    return last


def _small_settings(**overrides):
    settings = Settings(sitting_warn_min=2, hard_limit_hours=5)
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


# ---------------------------------------------------------------- controller
class TestDryRunController:
    def test_dry_run_logs_without_executing(self):
        ctrl = SystemController(dry_run=True)
        assert "dry run" in ctrl.open_app("notepad").lower()
        assert "dry run" in ctrl.set_volume(50).lower()
        assert "dry run" in ctrl.shutdown().lower()
        assert "dry run" in ctrl.close_app("notepad").lower()
        assert "dry run" in ctrl.set_brightness(80).lower()
        assert "dry run" in ctrl.lock().lower()
        assert "dry run" in ctrl.sleep().lower()
        assert "dry run" in ctrl.restart().lower()
        assert "dry run" in ctrl.screenshot("/tmp/shot.png").lower()
        assert ctrl.list_windows() == ["(dry run — no windows listed)"]
        # Every action is in the audit log.
        assert len(ctrl.log) == 10
        assert all("dry run" in entry.lower() for entry in ctrl.log)
        assert any("notepad" in entry for entry in ctrl.log)
        assert any("50%" in entry for entry in ctrl.log)
        assert any("shut down" in entry for entry in ctrl.log)

    def test_dry_run_never_touches_the_real_machine(self):
        ctrl = SystemController(dry_run=True)
        ctrl.shutdown()
        ctrl.sleep()
        ctrl.restart()
        # Still here, VM still up — dry run only logged.
        assert len(ctrl.log) == 3


class TestLinuxController:
    def test_shutdown_sleep_restart_not_available_on_linux(self):
        ctrl = SystemController(dry_run=False)
        if ctrl.is_windows:
            pytest.skip("Linux-only behaviour")
        with pytest.raises(NotAvailable):
            ctrl.shutdown()
        with pytest.raises(NotAvailable):
            ctrl.sleep()
        with pytest.raises(NotAvailable):
            ctrl.restart()

    def test_unknown_app_raises_not_available(self):
        ctrl = SystemController(dry_run=False)
        if ctrl.is_windows:
            pytest.skip("Linux-only behaviour")
        with pytest.raises(NotAvailable):
            ctrl.open_app("definitely-not-a-real-app-xyz")

    def test_volume_and_brightness_not_available_without_backends(self):
        ctrl = SystemController(dry_run=False)
        if ctrl.is_windows:
            pytest.skip("Linux-only behaviour")
        # No pactl on this VM, no brightness backend on Linux.
        with pytest.raises(NotAvailable):
            ctrl.set_volume(50)
        with pytest.raises(NotAvailable):
            ctrl.set_brightness(50)

    def test_close_app_needs_a_name(self):
        with pytest.raises(ValueError):
            SystemController(dry_run=True).close_app("  ")


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows")
def test_open_notepad_on_windows():
    ctrl = SystemController(dry_run=False)
    assert "Opened" in ctrl.open_app("notepad")


# -------------------------------------------------------------------- usage
class FakeMemory:
    def __init__(self):
        self.calls = []

    def log_usage(self, date, app, minutes):
        self.calls.append((date, app, minutes))


def test_usage_tracker_tick_records_minutes(monkeypatch):
    memory = FakeMemory()
    tracker = UsageTracker(memory=memory)
    monkeypatch.setattr(tracker, "active_app", lambda: "Chrome")
    assert tracker.tick() == "Chrome"
    assert tracker.tick() == "Chrome"
    assert tracker.minutes_for("chrome") == 2
    assert tracker.minutes_for("Chrome") == 2
    assert tracker.total_minutes() == 2
    assert len(memory.calls) == 2
    assert memory.calls[0][1] == "Chrome"
    assert memory.calls[0][2] == 1


def test_usage_tracker_without_memory_still_counts(monkeypatch):
    tracker = UsageTracker()
    monkeypatch.setattr(tracker, "active_app", lambda: "Word")
    tracker.tick()
    assert tracker.minutes_for("word") == 1
    assert tracker.total_minutes() == 1


def test_usage_tracker_active_app_returns_a_string():
    # On this Linux VM it is the most CPU-active process (or "unknown").
    assert isinstance(UsageTracker().active_app(), str)


# ------------------------------------------------------------------- health
class TestHealthLadderDefaults:
    def test_step1_fires_at_120_minutes_with_exact_message(self):
        ladder = HealthLadder(Settings())
        assert _drive(ladder, 119) is None
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 1
        assert event.action == "warn"
        assert event.message == "Boss, you have been working for two hours, please take a rest."

    def test_step2_fires_at_180_minutes(self):
        ladder = HealthLadder(Settings())
        _drive(ladder, 179)  # step 1 fires at minute 120 along the way
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 2
        assert event.action == "countdown"
        assert "10 minutes" in event.message

    def test_step3_fires_after_the_countdown(self):
        ladder = HealthLadder(Settings())
        _drive(ladder, 189)
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 3
        assert event.action == "close_apps"
        assert "60 seconds" in event.message
        assert "stop" in event.message.lower()

    def test_step4_fires_at_300_minutes(self):
        ladder = HealthLadder(Settings())
        _drive(ladder, 299)
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 4
        assert event.action == "shutdown"
        assert "boss" in event.message.lower()


class TestHealthLadderSmallThresholds:
    def test_steps_fire_in_order_with_small_thresholds(self):
        ladder = HealthLadder(_small_settings())
        assert _drive(ladder, 1) is None
        assert ladder.tick(active=True).step == 1  # minute 2
        assert _drive(ladder, 59) is None
        assert ladder.tick(active=True).step == 2  # minute 62
        assert _drive(ladder, 9) is None
        assert ladder.tick(active=True).step == 3  # minute 72
        assert ladder._close_pending is True

    def test_each_step_fires_only_once(self):
        ladder = HealthLadder(_small_settings())
        _drive(ladder, 5)
        assert ladder._step_reached == 1
        assert _drive(ladder, 10) is None  # no repeat of step 1

    def test_snooze_twice_then_refuses(self):
        ladder = HealthLadder(_small_settings())
        first = ladder.snooze()
        assert "30 more minutes" in first
        assert ladder.snoozes_used == 1
        second = ladder.snooze()
        assert "30 more minutes" in second
        assert ladder.snoozes_used == 2
        third = ladder.snooze()
        assert "no more snoozes" in third.lower()
        assert ladder.snoozes_used == 2

    def test_snooze_pushes_the_next_step_out_by_30_minutes(self):
        ladder = HealthLadder(_small_settings())
        _drive(ladder, 2)  # step 1 fires
        ladder.snooze()
        # Step 2 would fire at 62; the snooze pushes it to 92.
        assert _drive(ladder, 59) is None  # minutes 3..61
        assert _drive(ladder, 30) is None  # minutes 62..91
        assert ladder.tick(active=True).step == 2  # minute 92

    def test_stop_cancels_the_pending_close(self):
        ladder = HealthLadder(_small_settings())
        _drive(ladder, 72)  # step 3 fires, close is pending
        assert ladder._close_pending is True
        message = ladder.stop()
        assert "stopped" in message.lower()
        assert ladder._close_pending is False
        # The ladder keeps counting and step 3 does not re-fire.
        assert _drive(ladder, 5) is None
        assert ladder._step_reached == 3

    def test_rest_handover_defers_step4(self):
        ladder = HealthLadder(_small_settings(hard_limit_hours=1))
        ladder.rest_handover_active = True
        _drive(ladder, 59)
        event = ladder.tick(active=True)  # minute 60 = hard limit
        assert event is not None
        assert event.step == 0  # deferred, not fired
        assert "waiting" in event.message.lower()
        assert ladder._step_reached < 4
        # Once the handover ends, the shutdown fires on the next tick.
        ladder.rest_handover_active = False
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 4
        assert event.action == "shutdown"

    def test_step4_sleeps_instead_when_set(self):
        ladder = HealthLadder(_small_settings(hard_limit_hours=1), sleep_instead=True)
        _drive(ladder, 59)
        event = ladder.tick(active=True)
        assert event is not None
        assert event.step == 4
        assert event.action == "sleep"

    def test_ten_idle_minutes_reset_the_ladder(self):
        ladder = HealthLadder(_small_settings())
        _drive(ladder, 2)  # step 1 fires
        assert ladder._step_reached == 1
        assert _drive(ladder, 9, active=False) is None
        assert ladder.continuous_minutes == 2  # not yet a real break
        ladder.tick(active=False)  # 10th idle minute
        assert ladder.continuous_minutes == 0
        assert ladder._step_reached == 0
        assert ladder.break_minutes == 0
        # Step 1 fires again after another 2 active minutes.
        _drive(ladder, 1)
        assert ladder.tick(active=True).step == 1

    def test_short_idle_does_not_reset(self):
        ladder = HealthLadder(_small_settings())
        _drive(ladder, 2)  # step 1 fires
        _drive(ladder, 5, active=False)
        assert ladder.continuous_minutes == 2
        assert ladder._step_reached == 1
        assert ladder.tick(active=True) is None  # still counting, no re-fire


# -------------------------------------------------------------------- focus
def test_focus_mode_blocks_listed_apps_only_while_active():
    focus = FocusMode()
    assert focus.is_blocked("TikTok") is False  # not active yet
    message = focus.start(minutes=25)
    assert focus.active is True
    assert "boss" in message.lower()
    assert focus.is_blocked("TikTok") is True
    assert focus.is_blocked("tiktok") is True
    assert focus.is_blocked("YouTube") is True
    assert focus.is_blocked("Word") is False
    assert focus.is_blocked("Excel") is False
    assert "off" in focus.stop().lower()
    assert focus.active is False
    assert focus.is_blocked("TikTok") is False


def test_focus_mode_custom_block_list():
    focus = FocusMode(blocked_apps=["steam", "discord"])
    focus.start()
    assert focus.is_blocked("Steam") is True
    assert focus.is_blocked("TikTok") is False


# --------------------------------------------------------------------- tray
def test_run_tray_returns_cleanly_without_pystray(capsys):
    assert run_tray(object()) is None
    assert "pystray" in capsys.readouterr().out
