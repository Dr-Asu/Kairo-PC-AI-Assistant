"""System Controller — the assistant's hands on the PC (plan section 4).

Opens/closes apps, volume, brightness, lock/sleep/shutdown, screenshots,
window listing. Windows-only libraries are imported lazily and guarded;
on Linux, or in dry-run mode, actions are logged and reported instead of
executed, so the controller is testable anywhere.

Shutdown/restart/sleep always go through the ask-first gate — the
controller itself never shuts anything down without the boss's yes.

Builder C owns this file. Keep the public API exactly as declared here.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from ..apps import find_app, load_apps, resolve_exe

# Common app names -> candidate executables (Linux first, Windows fallback).
APP_MAP: dict[str, list[str]] = {
    "notepad": ["gedit", "kate", "mousepad", "xed", "notepad"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "edge": ["microsoft-edge", "microsoft-edge-stable"],
    "firefox": ["firefox"],
    "word": ["libreoffice", "soffice"],
    "excel": ["libreoffice", "soffice"],
    "powerpoint": ["libreoffice", "soffice"],
    "calculator": ["gnome-calculator", "kcalc", "calc"],
    "explorer": ["nautilus", "dolphin", "thunar", "explorer"],
    "terminal": ["gnome-terminal", "konsole", "xterm", "wt"],
    "settings": ["gnome-control-center", "systemsettings"],
}


class NotAvailable(RuntimeError):
    """An action is not available on this machine."""


class SystemController:
    """Operate the PC. ``dry_run=True`` logs actions instead of doing them."""

    def __init__(self, dry_run: bool = False, data_dir: str | Path | None = None) -> None:
        self.dry_run = dry_run
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".kairo"
        self.is_windows = platform.system() == "Windows"
        self.log: list[str] = []  # every action taken, for the audit trail

    def _record(self, action: str) -> str:
        self.log.append(action)
        return action

    def _dry(self, action: str) -> str:
        """Dry-run path: log the action, do nothing, say so plainly."""
        text = f"Would {action} (dry run)"
        self.log.append(text)
        return text

    @staticmethod
    def _clamp_level(level: int) -> int:
        return max(0, min(100, int(level)))

    # -- apps -------------------------------------------------------------
    @staticmethod
    def _is_running(image: str) -> bool:
        """True when a process with this image name (e.g. EXCEL.EXE) runs."""
        try:
            import psutil
        except ImportError:
            return False
        want = image.lower()
        try:
            for proc in psutil.process_iter(["name"]):
                if (proc.info.get("name") or "").lower() == want:
                    return True
        except Exception:
            return False
        return False

    def _wait_for(self, image: str, timeout: float, want_running: bool) -> bool:
        """Poll until the process appears (or disappears); False on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_running(image) == want_running:
                return True
            time.sleep(0.5)
        return self._is_running(image) == want_running

    def open_app(self, name: str) -> str:
        if self.dry_run:
            return self._dry(f"open {name}")
        if self.is_windows:
            return self._open_app_windows(name)
        # Linux: a real file/folder opens with xdg-open; otherwise map the
        # name to a known executable and launch it.
        if os.path.exists(name):
            subprocess.Popen(["xdg-open", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._record(f"Opened {name}")
        for candidate in APP_MAP.get(name.lower(), [name]):
            path = shutil.which(candidate)
            if path:
                proc = subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.3)
                if proc.poll() is not None and proc.returncode != 0:
                    raise NotAvailable(
                        f"'{name}' ({candidate}) started and exited at once "
                        f"(code {proc.returncode}) — it may have failed to start"
                    )
                return self._record(f"Opened {name} ({candidate})")
        raise NotAvailable(f"Cannot open '{name}': no matching app found on this PC")

    def _open_app_windows(self, name: str) -> str:
        """Windows open: resolve the app from the installed-apps index,
        launch it, then CHECK the process really started. Never report
        'Opened' for an app that never appeared."""
        if not name.strip():
            raise ValueError("open_app needs an app name, boss")
        app = find_app(name, self.data_dir)
        if app is None:
            raise NotAvailable(
                f"I don't know an app called '{name}' on this PC, boss. "
                "Say 'list apps' to see what I know, or 'rescan apps' "
                "if you just installed it."
            )
        exe = resolve_exe(app)
        if exe and self._is_running(exe):
            return self._record(f"{app.name} is already open, boss")
        try:
            os.startfile(app.target)  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", "Start-Process", app.target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if not exe:
            # Could not work out the process name (rare) — say so plainly
            # instead of claiming a verified open.
            time.sleep(1.5)
            return self._record(
                f"Opened {app.name} — I could not check it started, "
                "boss, please look at your screen"
            )
        if self._wait_for(exe, timeout=6.0, want_running=True):
            return self._record(f"Opened {app.name}")
        raise NotAvailable(
            f"I tried to open {app.name} but it did not start, boss — "
            "it may have failed, or be waiting for something on screen"
        )

    def list_apps(self) -> list[str]:
        """Display names of every app Kairo knows on this PC."""
        if self.dry_run:
            return ["(dry run — app list not read)"]
        return [app.name for app in load_apps(self.data_dir)]

    def rescan_apps(self) -> int:
        """Rebuild the installed-apps index now; returns how many apps."""
        return len(load_apps(self.data_dir, force=True))

    def close_app(self, name: str) -> str:
        if not name.strip():
            raise ValueError("close_app needs an app name, boss")
        if self.dry_run:
            return self._dry(f"close {name}")
        if self.is_windows:
            app = find_app(name, self.data_dir)
            if app is None:
                raise NotAvailable(
                    f"I don't know an app called '{name}' on this PC, boss."
                )
            exe = resolve_exe(app) or (
                app.name if app.name.lower().endswith(".exe") else f"{app.name}.exe"
            )
            if not self._is_running(exe):
                raise NotAvailable(f"{app.name} is not running, boss")
            subprocess.run(
                ["taskkill", "/IM", exe, "/F"],
                capture_output=True,
                check=False,
            )
            if self._wait_for(exe, timeout=3.0, want_running=False):
                return self._record(f"Closed {app.name}")
            raise NotAvailable(
                f"I tried to close {app.name} but it is still running, boss"
            )
        if shutil.which("pkill") is None:
            raise NotAvailable("Cannot close apps: 'pkill' is not available on this PC")
        done = subprocess.run(["pkill", "-f", name], capture_output=True, check=False)
        if done.returncode == 1:
            raise NotAvailable(f"'{name}' is not running")
        return self._record(f"Closed {name}")

    # -- volume / brightness ----------------------------------------------
    def set_volume(self, level: int) -> str:
        level = self._clamp_level(level)
        if self.dry_run:
            return self._dry(f"set volume to {level}%")
        if self.is_windows:
            try:
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                from comtypes import CLSCTX_ALL
            except ImportError as exc:
                raise NotAvailable("Volume control needs pycaw (Windows only)") from exc
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            return self._record(f"Set volume to {level}%")
        if shutil.which("pactl") is None:
            raise NotAvailable("Volume control needs 'pactl' (PulseAudio) on this PC")
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
            capture_output=True,
            check=True,
        )
        return self._record(f"Set volume to {level}%")

    def set_brightness(self, level: int) -> str:
        level = self._clamp_level(level)
        if self.dry_run:
            return self._dry(f"set brightness to {level}%")
        if self.is_windows:
            try:
                import screen_brightness_control as sbc
            except ImportError as exc:
                raise NotAvailable("Brightness control needs screen-brightness-control (Windows only)") from exc
            sbc.set_brightness(level)
            return self._record(f"Set brightness to {level}%")
        raise NotAvailable("Brightness control is not available on Linux yet")

    # -- lock / sleep / shutdown / restart --------------------------------
    def lock(self) -> str:
        if self.dry_run:
            return self._dry("lock the PC")
        if self.is_windows:
            import ctypes

            ctypes.windll.user32.LockWorkStation()
            return self._record("Locked the PC")
        if shutil.which("loginctl") is None:
            raise NotAvailable("Locking needs 'loginctl' on this PC")
        subprocess.run(["loginctl", "lock-session"], capture_output=True, check=False)
        return self._record("Locked the PC")

    def sleep(self) -> str:
        if self.dry_run:
            return self._dry("put the PC to sleep")
        if self.is_windows:
            subprocess.run(
                ["powershell", "-Command", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"],
                capture_output=True,
                check=False,
            )
            return self._record("Put the PC to sleep")
        # Never sleep this Linux VM — there is no safe sleep path here.
        raise NotAvailable("Sleep is not available on this Linux machine")

    def shutdown(self) -> str:
        if self.dry_run:
            return self._dry("shut down the PC")
        if self.is_windows:
            subprocess.run(
                ["powershell", "-Command", "Stop-Computer -Force"],
                capture_output=True,
                check=False,
            )
            return self._record("Shut down the PC")
        # Never shut down this Linux VM.
        raise NotAvailable("Shutdown is not available on this Linux machine")

    def restart(self) -> str:
        if self.dry_run:
            return self._dry("restart the PC")
        if self.is_windows:
            subprocess.run(
                ["powershell", "-Command", "Restart-Computer -Force"],
                capture_output=True,
                check=False,
            )
            return self._record("Restarted the PC")
        # Never restart this Linux VM.
        raise NotAvailable("Restart is not available on this Linux machine")

    # -- screen -------------------------------------------------------------
    def screenshot(self, path: str) -> str:
        if self.dry_run:
            return self._dry(f"take a screenshot and save it to {path}")
        try:
            import pyautogui

            pyautogui.screenshot(path)
            return self._record(f"Saved screenshot to {path}")
        except ImportError:
            pass
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(path)
            return self._record(f"Saved screenshot to {path}")
        except Exception as exc:  # noqa: BLE001 - guarded fallback chain
            raise NotAvailable("Screenshots need pyautogui or PIL ImageGrab") from exc

    def list_windows(self) -> list[str]:
        if self.dry_run:
            self.log.append("Would list open windows (dry run)")
            return ["(dry run — no windows listed)"]
        if self.is_windows:
            try:
                import win32gui
            except ImportError as exc:
                raise NotAvailable("Window listing needs pywin32 (Windows only)") from exc
            titles: list[str] = []

            def _enum(hwnd, _extra):  # noqa: ANN001, ANN202
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title:
                        titles.append(title)

            win32gui.EnumWindows(_enum, None)
            self._record(f"Listed {len(titles)} open windows")
            return titles
        raise NotAvailable("Window listing is not available on Linux yet")
