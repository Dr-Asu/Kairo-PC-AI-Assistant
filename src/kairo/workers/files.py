"""File Organizer — copy, move, rename, sort, arrange files and folders.

Moves, overwrites, and deletes always go through the ask-first gate
(``ctx.confirm``) — the worker never moves or deletes silently.
Builder E owns this file.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ..safety import ActionKind, Confirmation
from .base import TaskContext, Worker, WorkerResult

#: Extension groups for arrange(by="type").
_TYPE_GROUPS: dict[str, set[str]] = {
    "Documents": {".txt", ".doc", ".docx", ".pdf", ".md", ".rtf", ".odt"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "Sheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Slides": {".ppt", ".pptx", ".odp"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
}


def _group_for(suffix: str) -> str:
    suffix = suffix.lower()
    for group, exts in _TYPE_GROUPS.items():
        if suffix in exts:
            return group
    return "Other"


class FileOrganizer(Worker):
    """Arrange files and folders. Builder E implements this."""

    name = "files"
    intents = {"copy_files", "move_files", "arrange_folder", "rename_file", "open_file"}

    # -- direct methods ---------------------------------------------------
    def copy(self, src: str, dst: str) -> str:
        """Copy a file, creating parent folders as needed. Returns dst."""
        src_p, dst_p = Path(src), Path(dst)
        if not src_p.is_file():
            raise FileNotFoundError(f"Nothing to copy — no such file: {src}")
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dst_p)
        return str(dst_p)

    def move(self, src: str, dst: str, confirm=None) -> str:
        """Move a file.

        ``confirm`` is a callable taking a ``Confirmation`` and returning
        True/False. If the move would overwrite an existing file and no
        confirmation was given (or it was refused), the move is refused
        safely — nothing is overwritten, nothing is lost.
        """
        src_p, dst_p = Path(src), Path(dst)
        if not src_p.is_file():
            raise FileNotFoundError(f"Nothing to move — no such file: {src}")
        if dst_p.exists():
            if confirm is None:
                raise PermissionError(
                    f"Refused to move {src} — {dst} already exists. "
                    "Ask the boss first; I never overwrite without a yes.")
            ok = confirm(Confirmation(
                ActionKind.OVERWRITE,
                f"Move {src} over the existing file {dst}?",
                target=str(dst_p)))
            if not ok:
                raise PermissionError(
                    f"Move refused — {dst} already exists and the boss said no.")
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_p), str(dst_p))
        return str(dst_p)

    def rename(self, src: str, dst: str, confirm=None) -> str:
        """Rename a file (a move within the same folder). Same ask-first
        rules as :meth:`move`."""
        return self.move(src, dst, confirm=confirm)

    def arrange(self, folder: str, by: str = "type") -> dict[str, list[str]]:
        """Sort a folder's top-level files into subfolders.

        ``by="type"`` groups by extension (Documents/Images/Sheets/Other);
        ``by="date"`` groups by last-modified month (``YYYY-MM``).
        Files already sitting in their target subfolder are skipped.
        Returns ``{subfolder: [filenames moved]}``.
        """
        folder_p = Path(folder)
        if not folder_p.is_dir():
            raise FileNotFoundError(f"No such folder: {folder}")
        if by not in {"type", "date"}:
            raise ValueError(f"arrange by must be 'type' or 'date', not {by!r}")

        moved: dict[str, list[str]] = {}
        for item in sorted(folder_p.iterdir()):
            if not item.is_file():
                continue  # never touch subfolders themselves
            if by == "type":
                group = _group_for(item.suffix)
            else:
                group = datetime.fromtimestamp(
                    item.stat().st_mtime).strftime("%Y-%m")
            target_dir = folder_p / group
            target = target_dir / item.name
            if target.exists():
                continue  # already in place (or a name clash) — skip safely
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(target))
            moved.setdefault(group, []).append(item.name)
        return moved

    # -- worker entry point ------------------------------------------------
    def run(self, ctx: TaskContext) -> WorkerResult:
        # The boss dispatches with slots["_intent"]; older callers used
        # "intent"/"kind". Read _intent first or file intents never fire.
        intent = ctx.slots.get("_intent") or ctx.slots.get("intent") or ctx.slots.get("kind") or ""
        try:
            if intent == "open_file":
                return self._open_file(ctx)

            if intent == "copy_files":
                src, dst = ctx.slots.get("src", ""), ctx.slots.get("dst", "")
                if not src or not dst:
                    return WorkerResult.failed(
                        "Copy needs a source and a destination — boss, "
                        "tell me which file and where it should go.")
                out = self.copy(src, dst)
                return WorkerResult(ok=True, summary=f"Copied {src} to {out}.",
                                    files=[out], data={"src": src, "dst": out})

            if intent in {"move_files", "rename_file"}:
                src, dst = ctx.slots.get("src", ""), ctx.slots.get("dst", "")
                if not src or not dst:
                    return WorkerResult.failed(
                        "Move needs a source and a destination — boss, "
                        "tell me which file and where it should go.")
                # Ask-first gate: a move always needs the boss's yes.
                conf = Confirmation(ActionKind.MOVE,
                                    f"Move {src} to {dst}?", target=dst)
                if ctx.confirm is None:
                    return WorkerResult(
                        ok=False,
                        summary=f"I need your yes before moving {src} to {dst} — "
                                "I never move files without asking first, boss.",
                        needs_confirmation=conf)
                if not ctx.confirm(conf):
                    return WorkerResult.failed(
                        f"Not moved — you said no to moving {src} to {dst}.")
                try:
                    out = self.move(src, dst, confirm=lambda c: True)
                except PermissionError as exc:
                    return WorkerResult.failed(str(exc))
                verb = "Renamed" if intent == "rename_file" else "Moved"
                return WorkerResult(ok=True, summary=f"{verb} {src} to {out}.",
                                    files=[out], data={"src": src, "dst": out})

            if intent == "arrange_folder":
                folder = ctx.slots.get("folder", "")
                if not folder:
                    return WorkerResult.failed(
                        "Arrange needs a folder — boss, tell me which folder "
                        "to tidy up.")
                by = ctx.slots.get("by", "type")
                moved = self.arrange(folder, by=by)
                if not moved:
                    return WorkerResult.done(
                        f"{folder} is already tidy — nothing to move.")
                parts = ", ".join(f"{g} ({len(v)})" for g, v in moved.items())
                return WorkerResult.done(
                    f"Arranged {folder}: {parts}.", arranged=moved)

            return WorkerResult.failed(
                f"I don't know how to do '{intent or 'that'}' with files — "
                "I can copy, move, rename, open a file, or arrange a folder. "
                "Tell me which one, boss.")
        except FileNotFoundError as exc:
            return WorkerResult.failed(str(exc))
        except (OSError, ValueError) as exc:
            return WorkerResult.failed(f"Couldn't do that with the files ({exc}).")

    # -- open a file --------------------------------------------------------
    def _open_file(self, ctx: TaskContext) -> WorkerResult:
        from ..file_open import SEARCH_FOLDERS, find_best, open_file as _open_file
        from ..pc.controller import NotAvailable, SystemController

        name = (ctx.slots.get("file") or "").strip()
        folder = (ctx.slots.get("folder") or "").strip()
        if not name:
            return WorkerResult.failed(
                "Open needs a file name — boss, which file should I open?")
        best, ties = find_best(name, folder_hint=folder or None)
        if best is None:
            # No file by that name — maybe he meant an app ("open excel file").
            # Only take the app path when Kairo actually knows such an app.
            try:
                from ..apps import find_app

                data_dir = getattr(ctx.settings, "data_dir", None) if ctx.settings else None
                if find_app(name, data_dir) is not None:
                    opened = SystemController(
                        dry_run=ctx.dry_run, data_dir=data_dir
                    ).open_app(name)
                    return WorkerResult.done(opened)
            except Exception:
                pass
            searched = folder.title() if folder else ", ".join(SEARCH_FOLDERS)
            return WorkerResult.failed(
                f"I could not find a file called '{name}' in {searched}, "
                "boss. Say 'list apps' if it is an app, or check the name.")
        if ties:
            shown = ", ".join(p.name for p in [best, *ties][:3])
            return WorkerResult.failed(
                f"Boss, I found more than one match for '{name}': {shown} — "
                "which one should I open?")
        if ctx.dry_run:
            return WorkerResult.done(
                f"Would open {best.name} ({best}) (dry run)", files=[str(best)]
            )
        try:
            return WorkerResult.done(_open_file(best), files=[str(best)])
        except NotAvailable as exc:
            return WorkerResult.failed(str(exc))
