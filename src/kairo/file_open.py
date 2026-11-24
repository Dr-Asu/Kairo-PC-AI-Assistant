"""Open a file by name — find it in Downloads / Desktop / Documents and
launch it with its default app.

The user says things like "open tabz dashboard report, it is in the
download folder" — a file, not an app. The old code only knew apps, so
the whole sentence became one fake app name. This module searches the
usual folders, matches the name forgivingly, and opens the best match —
and says plainly when nothing matches, listing where it looked.
"""

from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path, PureWindowsPath

from .pc.controller import NotAvailable

#: Folders searched, in order. A folder hint restricts the search to one.
SEARCH_FOLDERS = ("Downloads", "Desktop", "Documents")

_FOLDER_ALIASES = {
    "download": "Downloads",
    "downloads": "Downloads",
    "desktop": "Desktop",
    "documents": "Documents",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def search_roots(folder_hint: str | None = None, home: str | Path | None = None) -> list[Path]:
    """The folders to search. Missing folders are skipped silently."""
    base = Path(home) if home else Path.home()
    names = SEARCH_FOLDERS
    if folder_hint:
        canonical = _FOLDER_ALIASES.get(folder_hint.strip().lower())
        names = (canonical,) if canonical else SEARCH_FOLDERS
    return [base / name for name in names if (base / name).is_dir()]


def score_file(query: str, path: Path) -> int:
    """How well ``query`` matches a file name (pure function — easy to test).

    Each query word found in the file name scores 1. "tabz dashboard
    report" scores 3 against TABZ-TV-Dashboard-Complete-Report-v2.7.21.pdf.
    A word matches a whole file-name word, or — when it is at least 4
    letters — a part of one ("dash" finds "dashboard"). Short words
    never match by substring, so "no" does not match "notes".
    """
    words = [w for w in _norm(query).split() if w]
    if not words:
        return 0
    hay_words = _norm(f"{path.stem} {path.suffix}").split()
    score = 0
    for word in words:
        if word in hay_words:
            score += 1
        elif len(word) >= 4 and any(word in token for token in hay_words):
            score += 1
    return score


def find_files(
    query: str,
    folder_hint: str | None = None,
    home: str | Path | None = None,
) -> list[tuple[int, Path]]:
    """All matching files, best score first (ties: shorter name first)."""
    results: list[tuple[int, Path]] = []
    for root in search_roots(folder_hint, home):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            score = score_file(query, path)
            if score > 0:
                results.append((score, path))
    results.sort(key=lambda item: (-item[0], len(item[1].name)))
    return results


def find_best(
    query: str,
    folder_hint: str | None = None,
    home: str | Path | None = None,
) -> tuple[Path | None, list[Path]]:
    """Best match for ``query``, plus any files tied with it.

    Returns ``(None, [])`` when nothing matches. When two or more files
    tie for the best score, the caller should ask the boss which one.
    """
    matches = find_files(query, folder_hint, home)
    if not matches:
        return None, []
    top = matches[0][0]
    tied = [p for score, p in matches if score == top]
    return tied[0], tied[1:]


def default_handler_exe(path: str | Path) -> str | None:
    """The exe Windows would use to open this file (registry lookup).

    Best effort — None when the extension has no association or the
    lookup fails. Used only to name the app and to check it started.
    """
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        ext = Path(path).suffix.lower()
        if not ext:
            return None
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as key:
            prog_id, _ = winreg.QueryValueEx(key, "")
        if not prog_id:
            return None
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, f"{prog_id}\\shell\\open\\command"
        ) as key:
            command, _ = winreg.QueryValueEx(key, "")
        match = re.search(r'"([^"]+\.exe)"|([^\s]+\.exe)', command, re.IGNORECASE)
        if match:
            return PureWindowsPath(match.group(1) or match.group(2)).name
    except OSError:
        return None
    return None


def _running_names() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    try:
        return {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:
        return set()


def open_file(path: str | Path, timeout: float = 5.0) -> str:
    """Open a file with its default app and check something started.

    Returns a plain description of what opened. Raises NotAvailable
    when the file is missing, has no associated app, or nothing started.
    """
    target = Path(path)
    if not target.is_file():
        raise NotAvailable(f"No such file: {target}")
    if platform.system() != "Windows":
        import subprocess

        try:
            done = subprocess.run(
                ["xdg-open", str(target)], capture_output=True, check=False
            )
        except FileNotFoundError as exc:
            raise NotAvailable(
                "I could not open files on this PC — no opener is installed"
            ) from exc
        if done.returncode != 0:
            raise NotAvailable(
                f"I could not open {target.name} — no app on this PC "
                "knows how to open it"
            )
        return f"Opened {target.name}"
    handler = default_handler_exe(target)
    before = _running_names()
    handler_was_running = bool(handler) and handler.lower() in before
    try:
        os.startfile(target)  # type: ignore[attr-defined]
    except OSError as exc:
        raise NotAvailable(f"I could not open {target.name} — {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        now = _running_names()
        if handler and handler.lower() in now:
            if handler_was_running:
                return (
                    f"Opened {target.name} — it should be showing in "
                    f"{handler} now (it was already open)"
                )
            return f"Opened {target.name} in {handler}"
        if not handler:
            new = now - before
            if new:
                return f"Opened {target.name} (started {sorted(new)[0]})"
        time.sleep(0.5)
    if handler and handler.lower() in _running_names():
        return f"Opened {target.name} in {handler}"
    raise NotAvailable(
        f"I asked Windows to open {target.name} but nothing started, "
        "boss — please check your screen"
    )
