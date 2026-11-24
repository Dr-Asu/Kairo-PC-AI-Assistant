"""App discovery — Kairo knows every installed app, not just a hardcoded list.

On Windows the index is built from:
  1. Start Menu shortcuts (all users + current user) — the real names the
     user sees, e.g. "Google Chrome", "Microsoft Excel".
  2. The App Paths registry — every exe Windows itself can launch by name.
  3. Built-in aliases for system tools (notepad, calculator, settings...).

The index is cached in ``~/.kairo/apps.json`` (24 h) and rebuilt
automatically when a name is not found — so a newly installed app is
picked up the first time the user asks for it. ``rescan_apps`` forces a
fresh scan at any time.

Matching is forgiving: "excell" still finds Excel, "chrome" finds
Google Chrome, "word" finds Microsoft Word.
"""

from __future__ import annotations

import difflib
import json
import os
import platform
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

CACHE_MAX_AGE_SECONDS = 24 * 3600
CACHE_FILE = "apps.json"

# Built-in aliases: name -> launch target (Windows) / candidates (Linux).
# These cover system tools the Start Menu / registry may not list.
ALIASES: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "settings": ["ms-settings:"],
    "terminal": ["wt.exe"],
    "explorer": ["explorer.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
}


@dataclass
class AppInfo:
    """One installed app Kairo can open."""

    name: str  # display name, e.g. "Google Chrome"
    target: str  # what to launch: a .lnk path, an exe path, or an exe name
    exe: str | None = None  # process image name for verification, e.g. "EXCEL.EXE"
    source: str = "start-menu"  # start-menu | app-paths | alias


def normalize(name: str) -> str:
    """Lowercase, letters and digits only — 'MS Excel 2016' -> 'msexcel2016'."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def match_app(query: str, apps: list[AppInfo]) -> AppInfo | None:
    """Best match for ``query`` in ``apps`` (pure function — easy to test).

    Exact normalized match first, then name starting with the query, then
    the query appearing inside the name, then a fuzzy match for typos.
    """
    q = normalize(query)
    if not q:
        return None
    by_norm = {normalize(a.name): a for a in apps}
    if q in by_norm:
        return by_norm[q]
    # The exe name is also a match: "excel" finds "Microsoft Excel".
    for app in apps:
        if app.exe and normalize(app.exe.replace(".exe", "")) == q:
            return app
    starts = [a for a in apps if normalize(a.name).startswith(q)]
    if starts:
        return sorted(starts, key=lambda a: len(a.name))[0]
    contains = [a for a in apps if q in normalize(a.name)]
    if contains:
        return sorted(contains, key=lambda a: len(a.name))[0]
    close = difflib.get_close_matches(
        q, [normalize(a.name) for a in apps], n=1, cutoff=0.55
    )
    if close:
        return by_norm[close[0]]
    # Typos against a single word of a name: "excell" vs "excel" in
    # "Microsoft Excel" (the whole-name ratio is too low for that).
    best: AppInfo | None = None
    best_ratio = 0.55
    for app in apps:
        for word in re.split(r"[^a-z0-9]+", app.name.lower()):
            if not word:
                continue
            ratio = difflib.SequenceMatcher(None, q, word).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = app
    return best


def _start_menu_apps() -> list[AppInfo]:
    """Every .lnk in the Windows Start Menu folders."""
    found: list[AppInfo] = []
    roots = []
    program_data = os.environ.get("ProgramData", "")
    app_data = os.environ.get("APPDATA", "")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if app_data:
        roots.append(Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    for root in roots:
        if not root.is_dir():
            continue
        for lnk in root.rglob("*.lnk"):
            name = lnk.stem.strip()
            if not name:
                continue
            found.append(AppInfo(name=name, target=str(lnk), exe=None, source="start-menu"))
    return found


def _app_paths_apps() -> list[AppInfo]:
    """Every exe in the App Paths registry (what Windows can launch by name)."""
    found: list[AppInfo] = []
    if platform.system() != "Windows":
        return found
    try:
        import winreg
    except ImportError:
        return found
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
            )
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    continue
                if not sub.lower().endswith(".exe"):
                    continue
                try:
                    with winreg.OpenKey(key, sub) as subkey:
                        target, _ = winreg.QueryValueEx(subkey, "")
                except OSError:
                    continue
                if not target:
                    continue
                found.append(
                    AppInfo(
                        name=sub[: -len(".exe")],
                        target=target,
                        exe=sub,
                        source="app-paths",
                    )
                )
        finally:
            winreg.CloseKey(key)
    return found


def _alias_apps() -> list[AppInfo]:
    return [
        AppInfo(name=name, target=targets[0], exe=targets[0], source="alias")
        for name, targets in ALIASES.items()
    ]


def discover_apps() -> list[AppInfo]:
    """Scan this machine for installed apps. Windows only for the real
    scan; everywhere else the built-in aliases are the index."""
    apps: list[AppInfo] = []
    if platform.system() == "Windows":
        apps.extend(_start_menu_apps())
        apps.extend(_app_paths_apps())
    apps.extend(_alias_apps())
    # De-duplicate by normalized name — first source wins.
    seen: dict[str, AppInfo] = {}
    for app in apps:
        seen.setdefault(normalize(app.name), app)
    return sorted(seen.values(), key=lambda a: a.name.lower())


def _cache_path(data_dir: str | Path | None) -> Path:
    base = Path(data_dir) if data_dir else Path.home() / ".kairo"
    return base / CACHE_FILE


def load_apps(
    data_dir: str | Path | None = None,
    force: bool = False,
    _discover: object = None,
) -> list[AppInfo]:
    """The cached app index; rebuilt when missing, stale, or forced."""
    path = _cache_path(data_dir)
    if not force and path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - float(raw.get("updated_at", 0))
            if age < CACHE_MAX_AGE_SECONDS:
                return [AppInfo(**item) for item in raw.get("apps", [])]
        except (OSError, ValueError, KeyError, TypeError):
            pass  # corrupt cache — rebuild below
    discover = _discover or discover_apps
    apps = discover()  # type: ignore[operator]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"updated_at": time.time(), "apps": [asdict(a) for a in apps]},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # cache is a convenience; a failed write must not break opens
    return apps


def find_app(
    query: str,
    data_dir: str | Path | None = None,
    rescan_on_miss: bool = True,
) -> AppInfo | None:
    """Find an app by the name the user said. One forced rescan on a miss,
    so an app installed since the last scan is still found."""
    apps = load_apps(data_dir)
    hit = match_app(query, apps)
    if hit is None and rescan_on_miss:
        hit = match_app(query, load_apps(data_dir, force=True))
    return hit


def resolve_exe(app: AppInfo) -> str | None:
    """The process image name used to verify the app really started.

    For a .lnk the target exe is read through WScript.Shell (Windows);
    for an exe path or name it is the file name itself.
    """
    if app.exe:
        return app.exe
    target = app.target
    if target.lower().endswith(".exe"):
        # PureWindowsPath: backslashes are separators even on Linux.
        return PureWindowsPath(target).name
    if target.lower().endswith(".lnk") and platform.system() == "Windows":
        try:
            out = __import__("subprocess").run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(New-Object -ComObject WScript.Shell)"
                    f".CreateShortcut('{target}').TargetPath",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            resolved = (out.stdout or "").strip()
            if resolved:
                return PureWindowsPath(resolved).name
        except Exception:
            return None
    return None
