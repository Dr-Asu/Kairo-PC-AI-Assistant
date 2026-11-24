"""Browser Agent — drives Chrome, Edge, and Firefox with Playwright.

Opens sites, clicks, fills forms, downloads files — always in the boss's
own browser profile, so his logins and cookies are already there. It never
handles passwords, never bypasses login walls or paywalls, and if a site
shows a CAPTCHA it stops and tells the boss plainly.

Playwright is optional and guarded: without it the agent reports itself
unavailable instead of crashing.
Builder E owns this file.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from urllib.parse import quote

from .base import TaskContext, Worker, WorkerResult


class BrowserNotAvailableError(RuntimeError):
    """Playwright (or the browser) is not installed / not usable."""


# Well-known sites, so "open youtube" needs no URL and no Playwright.
KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://www.twitter.com",
    "x": "https://www.x.com",
    "whatsapp": "https://web.whatsapp.com",
    "github": "https://github.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "wikipedia": "https://www.wikipedia.org",
    "stackoverflow": "https://stackoverflow.com",
    "amazon": "https://www.amazon.com",
    "daraz": "https://www.daraz.pk",
}


def resolve_site(site: str) -> str:
    """Turn a spoken site name into a URL.

    A full URL passes through unchanged, a known name maps to its site,
    a dotted name gets https://, and anything else becomes a Google
    search — so the boss always gets something opened, never a shrug.
    """
    name = (site or "").strip().lower()
    if not name:
        return "https://www.google.com"
    if name.startswith("http://") or name.startswith("https://"):
        return name
    if name in KNOWN_SITES:
        return KNOWN_SITES[name]
    if "." in name:
        return "https://" + name
    return "https://www.google.com/search?q=" + quote(name)


# Phrases that mean "this page is a bot check, not the real site".
_CAPTCHA_MARKERS = (
    "captcha",
    "are you a robot",
    "verify you are human",
    "verify you're human",
    "human verification",
    "i'm not a robot",
    "unusual traffic",
)


def _looks_like_captcha(page) -> bool:
    """True if the loaded page is a CAPTCHA / bot-check wall."""
    try:
        text = (page.title() + "\n" + page.content()).lower()
    except Exception:  # noqa: BLE001 - any read failure means "can't tell"
        return False
    return any(marker in text for marker in _CAPTCHA_MARKERS)


def _looks_like_login_wall(page) -> bool:
    """True if the page is a hard login gate (redirected to a login page
    whose only content is a sign-in form). A normal page that merely has
    a 'Sign in' link is NOT a wall."""
    try:
        url = page.url.lower()
        title = page.title().lower()
    except Exception:  # noqa: BLE001
        return False
    if "/login" not in url and "/signin" not in url and "sign in" not in title:
        return False
    try:
        has_password = page.locator('input[type="password"]').count() > 0
    except Exception:  # noqa: BLE001
        has_password = False
    return has_password


class BrowserAgent(Worker):
    """Drive browsers. Builder E implements this."""

    name = "browser"
    intents = {"open_browser", "go_to_site", "download_file"}

    def __init__(self, settings=None, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run

    # -- helpers ------------------------------------------------------------
    def _data_dir(self) -> Path:
        if self.settings is not None and getattr(self.settings, "data_dir", None):
            return Path(self.settings.data_dir)
        return Path.home() / ".kairo"

    def _profile_dir(self) -> Path:
        return self._data_dir() / "browser-profile"

    def _is_dry_run(self, ctx: TaskContext | None = None) -> bool:
        return bool(self.dry_run or (ctx is not None and ctx.dry_run))

    def _load_playwright(self):
        """Import Playwright lazily; raise a plain-English error if absent."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserNotAvailableError(
                "Playwright is not installed, so I can't drive a browser. "
                "Install it with `pip install playwright` and run "
                "`playwright install` (both are free) — then I'll be able "
                "to open sites in your own Chrome profile.") from exc
        return sync_playwright

    def _launch_context(self, playwright, browser: str):
        """Launch the named browser with the boss's persistent profile."""
        name = browser.lower()
        profile = self._profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        if name == "firefox":
            return playwright.firefox.launch_persistent_context(
                str(profile), headless=False)
        if name in {"edge", "msedge"}:
            return playwright.chromium.launch_persistent_context(
                str(profile), channel="msedge", headless=False)
        # Default: Chrome.
        return playwright.chromium.launch_persistent_context(
            str(profile), channel="chrome", headless=False)

    # -- public actions ------------------------------------------------------
    def open_url(self, url: str, browser: str = "chrome") -> str:
        """Open a URL in the boss's own browser profile. In dry_run mode
        nothing is launched — the planned action is returned as a string."""
        if self.dry_run:
            return f"Would open {url} in {browser} (dry run — nothing launched)."
        sync_playwright = self._load_playwright()
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, browser)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if _looks_like_captcha(page):
                context.close()
                raise BrowserNotAvailableError(
                    f"{url} showed a CAPTCHA / bot check — I stopped there. "
                    "Boss, please open it yourself once and I'll carry on "
                    "after you've passed the check.")
            if _looks_like_login_wall(page):
                context.close()
                raise BrowserNotAvailableError(
                    f"{url} is behind a login wall — I never type passwords "
                    "or bypass logins. Boss, please sign in yourself in "
                    "your browser and I'll carry on from there.")
            title = page.title()
            context.close()
        return f"Opened {url} in {browser}" + (f" — {title}" if title else "") + "."

    def download(self, url: str, dest: str, browser: str = "chrome") -> str:
        """Download a file to ``dest`` via the browser. Dry run only plans."""
        if self.dry_run:
            return (f"Would download {url} to {dest} in {browser} "
                    "(dry run — nothing downloaded).")
        sync_playwright = self._load_playwright()
        dest_p = Path(dest)
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, browser)
            page = context.new_page()
            with page.expect_download(timeout=60000) as download_info:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if _looks_like_captcha(page):
                context.close()
                raise BrowserNotAvailableError(
                    f"{url} showed a CAPTCHA / bot check — I stopped there. "
                    "Boss, please open it yourself once and I'll carry on "
                    "after you've passed the check.")
            download = download_info.value
            download.save_as(str(dest_p))
            context.close()
        return f"Downloaded {url} to {dest_p}."

    # -- worker entry point --------------------------------------------------
    def run(self, ctx: TaskContext) -> WorkerResult:
        if ctx.settings is not None and self.settings is None:
            self.settings = ctx.settings
        dry = self._is_dry_run(ctx)
        # The boss dispatches with slots["_intent"]; older callers used
        # "intent"/"kind". Read _intent first or site intents never fire
        # (same latent bug the files worker had before v0.3.1).
        intent = ctx.slots.get("_intent") or ctx.slots.get("intent") or ctx.slots.get("kind") or ""
        url = ctx.slots.get("url", "")
        browser = ctx.slots.get("browser", "chrome")

        if intent == "go_to_site":
            # Plain "open youtube": open the site in the boss's own
            # default browser — no Playwright, no new profile, his
            # logins and cookies are already there.
            site = (ctx.slots.get("site") or url or "").strip()
            if not site:
                return WorkerResult.failed(
                    "Open which site, boss? Give me the name and I'll open it.")
            target = resolve_site(site)
            if dry:
                return WorkerResult.done(
                    f"Would open {target} in your browser "
                    "(dry run — nothing launched).",
                    url=target)
            try:
                opened = webbrowser.open(target)
            except Exception as exc:  # noqa: BLE001 - report plainly
                return WorkerResult.failed(f"Couldn't open {target} ({exc}).")
            if not opened:
                return WorkerResult.failed(
                    f"Couldn't open {target} — no browser answered.")
            return WorkerResult.done(
                f"Opened {site} in your browser, boss.", url=target)

        if intent == "open_browser":
            if not url:
                return WorkerResult.failed(
                    "Open which site, boss? Give me the URL and I'll open it.")
            if dry:
                return WorkerResult.done(
                    f"Would open {url} in {browser} (dry run — nothing launched).",
                    url=url, browser=browser)
            try:
                summary = self.open_url(url, browser=browser)
            except BrowserNotAvailableError as exc:
                return WorkerResult.failed(str(exc))
            except Exception as exc:  # noqa: BLE001 - report plainly
                return WorkerResult.failed(f"Couldn't open {url} ({exc}).")
            return WorkerResult.done(summary, url=url, browser=browser)

        if intent == "download_file":
            dest = ctx.slots.get("dest") or ctx.slots.get("path", "")
            if not url or not dest:
                return WorkerResult.failed(
                    "Download needs a URL and a destination — boss, give me "
                    "both and I'll fetch it.")
            if dry:
                return WorkerResult.done(
                    f"Would download {url} to {dest} in {browser} "
                    "(dry run — nothing downloaded).",
                    url=url, dest=dest, browser=browser)
            try:
                summary = self.download(url, dest, browser=browser)
            except BrowserNotAvailableError as exc:
                return WorkerResult.failed(str(exc))
            except Exception as exc:  # noqa: BLE001
                return WorkerResult.failed(f"Couldn't download {url} ({exc}).")
            return WorkerResult(ok=True, summary=summary, files=[dest],
                                data={"url": url, "dest": dest, "browser": browser})

        return WorkerResult.failed(
            f"I don't know how to do '{intent or 'that'}' in the browser — "
            "I can open a site or download a file. Tell me which, boss.")
