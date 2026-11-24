"""WhatsApp Worker — read, reply, and send files on WhatsApp.

Two routes, both the assistant's own:
* **WhatsApp Web** (main route) — Playwright drives WhatsApp Web in the
  boss's own logged-in browser.
* **Windows WhatsApp app** (fallback route) — Windows UI Automation drives
  the app itself. Windows-only; on other machines it reports unavailable.

Ask-first, every time: before sending, the worker reads back the
recipient's name and the exact message (or the file name) and sends only
on the boss's yes. A "yes, send it" in the same breath as the command
counts as the yes.

HARD LINE — completely separate from StudyBot. StudyBot is the boss's
separate WhatsApp bot with its own code and its own login session. This
worker never reads, uses, or touches StudyBot's code, session, or login,
and nothing here is built on StudyBot. The boss logs in once, in his own
WhatsApp, himself; this worker never handles his password and never
bypasses a login wall. WhatsApp Web automation is unofficial and can break
when WhatsApp changes its page — when it breaks, the worker says so
plainly instead of guessing.

Builder F owns this file. Keep the public API exactly as declared here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Settings, get_settings
from ..safety import ActionKind, Confirmation
from .base import TaskContext, Worker, WorkerResult

WHATSAPP_WEB_URL = "https://web.whatsapp.com"

PLAYWRIGHT_MISSING = (
    "Playwright is not installed, so WhatsApp Web control is unavailable. "
    "Install the free Playwright browser extra (pip install 'kairo-assistant[browser]' "
    "then playwright install chromium) and try again."
)

WINDOWS_ONLY = (
    "Windows WhatsApp app control needs Windows — this machine is not "
    "Windows, so the Windows app route is unavailable. Use WhatsApp Web instead."
)


class _WhatsAppWebDriver:
    """Playwright driver for WhatsApp Web (boss's own logged-in browser).

    Best effort: WhatsApp changes its page from time to time, and when a
    selector stops matching the driver raises a plain RuntimeError instead
    of guessing. The boss logs in once himself in his own browser profile;
    this driver never handles a password and never bypasses a login wall.
    """

    def __init__(self, settings: Settings) -> None:
        self.profile_dir = Path(settings.data_dir) / "whatsapp-profile"

    def _launch(self):
        from playwright.sync_api import sync_playwright

        return sync_playwright()

    def _open_page(self, playwright):
        """Open WhatsApp Web in the persistent profile. Returns (browser, page)."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        browser = playwright.chromium.launch_persistent_context(
            str(self.profile_dir), headless=False
        )
        page = browser.new_page() if not browser.pages else browser.pages[0]
        page.goto(WHATSAPP_WEB_URL)
        page.wait_for_timeout(3000)
        body = page.content()
        if "To use WhatsApp on your computer" in body or "Log in" in body:
            browser.close()
            raise RuntimeError(
                "WhatsApp Web is not logged in — boss, please open "
                "web.whatsapp.com in your own browser once and scan the QR "
                "code with your phone. I never handle your password."
            )
        return browser, page

    def open_chat(self, name: str) -> str:
        with self._launch() as playwright:
            browser, page = self._open_page(playwright)
            try:
                self._find_chat(page, name)
            finally:
                browser.close()
        return name

    def _find_chat(self, page, name: str) -> None:
        """Type the name in the search box and open the first match."""
        search = page.locator('div[contenteditable="true"][data-tab="3"]')
        search.click(timeout=10000)
        search.fill(name)
        page.wait_for_timeout(1500)
        result = page.locator(f'span[title="{name}"]').first
        if not result.count():
            raise RuntimeError(
                f"Could not find a WhatsApp chat called '{name}'. Check the "
                "spelling, or open the chat once yourself in WhatsApp Web."
            )
        result.click(timeout=10000)
        page.wait_for_timeout(1000)

    def read_unread(self) -> list[dict]:
        messages: list[dict] = []
        with self._launch() as playwright:
            browser, page = self._open_page(playwright)
            try:
                badges = page.locator('span[aria-label*="unread"]').all()
                for badge in badges[:10]:
                    chat = badge.locator("xpath=ancestor::div[@role='row']").first
                    title = chat.locator("span[title]").first.get_attribute("title") or ""
                    last = chat.locator("span[dir='ltr']").last.inner_text() if chat.locator("span[dir='ltr']").count() else ""
                    messages.append({"chat": title, "sender": title, "text": last})
            finally:
                browser.close()
        return messages

    def reply(self, name: str, message: str) -> str:
        with self._launch() as playwright:
            browser, page = self._open_page(playwright)
            try:
                self._find_chat(page, name)
                box = page.locator('div[contenteditable="true"][data-tab="10"]')
                box.click(timeout=10000)
                box.type(message)
                box.press("Enter")
            finally:
                browser.close()
        return f"Sent WhatsApp message to {name}: '{message}'"

    def send_file(self, name: str, path: str) -> str:
        with self._launch() as playwright:
            browser, page = self._open_page(playwright)
            try:
                self._find_chat(page, name)
                page.locator('span[data-icon="attach-menu-plus"]').click(timeout=10000)
                page.locator('input[accept*="image"], input[type="file"]').first.set_input_files(path)
                page.wait_for_timeout(1500)
                page.locator('span[data-icon="send"]').click(timeout=10000)
            finally:
                browser.close()
        return f"Sent file {Path(path).name} to {name} on WhatsApp"


class WhatsAppWorker(Worker):
    """Control WhatsApp by voice. Builder F implements this.

    ``reply`` / ``send_file`` return a ``WorkerResult``: when the boss has
    not confirmed yet, the result carries ``needs_confirmation`` (a SEND
    Confirmation reading back the recipient and the exact message or file
    name) and nothing is sent. Pass ``confirm`` — a callable taking a
    Confirmation and returning True/False — to answer it. ``run`` passes
    ``ctx.confirm`` through. Set ``worker.driver`` to a fake driver object
    with the same methods to test without launching a browser.
    """

    name = "whatsapp"
    intents = {"whatsapp_read", "whatsapp_reply", "whatsapp_send_file"}

    def __init__(self, settings: Settings | None = None, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.driver: object | None = None  # injectable Playwright layer (tests)

    # -- helpers ----------------------------------------------------------
    def _get_settings(self, ctx: TaskContext | None = None) -> Settings:
        if ctx is not None and ctx.settings is not None:
            return ctx.settings
        if self.settings is not None:
            return self.settings
        return get_settings()

    def _is_dry(self, ctx: TaskContext | None = None) -> bool:
        settings = self._get_settings(ctx)
        return bool(self.dry_run or settings.dry_run or (ctx is not None and ctx.dry_run))

    def _web_driver(self):
        """Return the driver, or raise a plain error if Playwright is missing."""
        if self.driver is not None:
            return self.driver
        try:
            import playwright  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(PLAYWRIGHT_MISSING) from exc
        return _WhatsAppWebDriver(self._get_settings())

    def _ask(self, confirm, confirmation: Confirmation) -> bool | None:
        """Ask the boss. True = yes, False = no, None = no way to ask."""
        ask = confirm if confirm is not None else getattr(self, "confirm", None)
        if ask is None:
            return None
        return bool(ask(confirmation))

    # -- public API -------------------------------------------------------
    def open_chat(self, name: str) -> str:
        """Open a chat by the person's (or group's) name."""
        if self._is_dry():
            return f"Dry run: would open WhatsApp chat with {name}."
        driver = self._web_driver()
        return driver.open_chat(name)

    def read_unread(self) -> list[dict]:
        """Unread messages: [{"chat": ..., "sender": ..., "text": ...}]."""
        if self._is_dry():
            return []
        driver = self._web_driver()
        return driver.read_unread()

    def reply(self, name: str, message: str, confirm=None) -> WorkerResult:
        """Reply to a chat — asks first (recipient + exact message)."""
        if self._is_dry():
            return WorkerResult.done(
                f"Dry run: would send WhatsApp message to {name}: '{message}'",
                planned=f"send to {name}: {message}",
            )
        confirmation = Confirmation(
            ActionKind.SEND,
            f"Send WhatsApp message to {name}: '{message}'",
            target=name,
        )
        answer = self._ask(confirm, confirmation)
        if answer is None:
            return WorkerResult(
                ok=False,
                summary=f"Boss, shall I send this WhatsApp message to {name}: "
                f"'{message}'? Say yes and I will send it.",
                needs_confirmation=confirmation,
            )
        if answer is False:
            return WorkerResult.failed(f"Not sent — boss said no to messaging {name}.")
        try:
            driver = self._web_driver()
            sent = driver.reply(name, message)
        except RuntimeError as exc:
            return WorkerResult.failed(str(exc))
        return WorkerResult.done(sent, chat=name, message=message)

    def send_file(self, name: str, path: str, confirm=None) -> WorkerResult:
        """Send a file (document, image, PDF) — asks first."""
        if self._is_dry():
            return WorkerResult.done(
                f"Dry run: would send file {Path(path).name} to {name} on WhatsApp",
                planned=f"send file {path} to {name}",
            )
        if not Path(path).is_file():
            return WorkerResult.failed(f"File not found, so nothing was sent: {path}")
        confirmation = Confirmation(
            ActionKind.SEND,
            f"Send file '{Path(path).name}' to {name} on WhatsApp",
            target=name,
        )
        answer = self._ask(confirm, confirmation)
        if answer is None:
            return WorkerResult(
                ok=False,
                summary=f"Boss, shall I send the file '{Path(path).name}' to "
                f"{name} on WhatsApp? Say yes and I will send it.",
                needs_confirmation=confirmation,
            )
        if answer is False:
            return WorkerResult.failed(f"Not sent — boss said no to sending the file to {name}.")
        try:
            driver = self._web_driver()
            sent = driver.send_file(name, path)
        except RuntimeError as exc:
            return WorkerResult.failed(str(exc))
        return WorkerResult.done(sent, chat=name, file=path)

    def reply_via_app(self, name: str, message: str, confirm=None) -> WorkerResult:
        """Reply through the Windows WhatsApp app (fallback route).

        Windows-only. On any other machine it reports that plainly.
        Scaffold on Windows: drives the app with pywinauto / UI Automation.
        """
        if self._is_dry():
            return WorkerResult.done(
                f"Dry run: would send WhatsApp message to {name} via the "
                f"Windows app: '{message}'",
                planned=f"send via Windows app to {name}: {message}",
            )
        if sys.platform != "win32":
            return WorkerResult.failed(WINDOWS_ONLY)
        confirmation = Confirmation(
            ActionKind.SEND,
            f"Send WhatsApp message to {name} via the Windows app: '{message}'",
            target=name,
        )
        answer = self._ask(confirm, confirmation)
        if answer is None:
            return WorkerResult(
                ok=False,
                summary=f"Boss, shall I send this WhatsApp message to {name} "
                f"via the Windows app: '{message}'?",
                needs_confirmation=confirmation,
            )
        if answer is False:
            return WorkerResult.failed(f"Not sent — boss said no to messaging {name}.")
        try:
            import pywinauto  # noqa: F401
        except ImportError as exc:
            return WorkerResult.failed(
                "The Windows WhatsApp app route needs pywinauto, which is "
                f"not installed: {exc}"
            )
        # Scaffold: find the app window, find the chat, type, send. Kept
        # short on purpose — the Web route is the main route, and the app
        # UI changes between WhatsApp versions, so every step reports
        # plainly instead of guessing when it cannot find a control.
        try:
            from pywinauto import Application

            app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=10)
            window = app.top_window()
            search = window.child_window(title_re=".*Search.*", control_type="Edit")
            search.click_input()
            search.type_keys(name, with_spaces=True)
            window.child_window(title=name, control_type="ListItem").click_input()
            box = window.child_window(title_re=".*Type a message.*", control_type="Edit")
            box.click_input()
            box.type_keys(message, with_spaces=True)
            box.type_keys("{ENTER}")
        except Exception as exc:  # noqa: BLE001 - UI Automation is best-effort
            return WorkerResult.failed(
                "The Windows WhatsApp app could not be driven just now "
                f"({exc}). Try WhatsApp Web instead, boss."
            )
        return WorkerResult.done(
            f"Sent WhatsApp message to {name} via the Windows app: '{message}'",
            chat=name,
            message=message,
        )

    # -- dispatch ---------------------------------------------------------
    def _intent_from_ctx(self, ctx: TaskContext) -> str:
        intent = (ctx.slots or {}).get("intent") or (ctx.slots or {}).get("name") or ""
        if intent in self.intents:
            return intent
        text = (ctx.text or "").lower()
        if "send" in text and ("file" in text or "photo" in text or "pdf" in text):
            return "whatsapp_send_file"
        if "read" in text or "unread" in text:
            return "whatsapp_read"
        return "whatsapp_reply"

    def run(self, ctx: TaskContext) -> WorkerResult:
        intent = self._intent_from_ctx(ctx)
        slots = ctx.slots or {}

        if self._is_dry(ctx):
            if intent == "whatsapp_read":
                return WorkerResult.done("Dry run: would read unread WhatsApp messages.")
            if intent == "whatsapp_send_file":
                name = slots.get("name") or slots.get("chat") or "?"
                path = slots.get("path") or slots.get("file") or "?"
                return WorkerResult.done(
                    f"Dry run: would send file {Path(str(path)).name} to {name} on WhatsApp",
                    planned=f"send file {path} to {name}",
                )
            name = slots.get("name") or slots.get("chat") or "?"
            message = slots.get("message") or ctx.text
            return WorkerResult.done(
                f"Dry run: would send WhatsApp message to {name}: '{message}'",
                planned=f"send to {name}: {message}",
            )

        if intent == "whatsapp_read":
            try:
                messages = self.read_unread()
            except RuntimeError as exc:
                return WorkerResult.failed(str(exc))
            if not messages:
                return WorkerResult.done("No unread WhatsApp messages.", messages=[])
            summary = "; ".join(
                f"{m.get('chat', '?')}: {m.get('text', '')}" for m in messages
            )
            return WorkerResult.done(f"Unread WhatsApp: {summary}", messages=messages)

        if intent == "whatsapp_send_file":
            name = slots.get("name") or slots.get("chat") or ""
            path = slots.get("path") or slots.get("file") or ""
            if not name or not path:
                return WorkerResult.failed(
                    "To send a file I need both a person and a file — "
                    "tell me who and which file, boss."
                )
            return self.send_file(name, path, confirm=ctx.confirm)

        name = slots.get("name") or slots.get("chat") or ""
        message = slots.get("message") or ""
        if not name or not message:
            return WorkerResult.failed(
                "To reply on WhatsApp I need both a person and a message — "
                "tell me who and what to say, boss."
            )
        return self.reply(name, message, confirm=ctx.confirm)
