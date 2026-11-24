"""Screen Vision worker — the assistant's eyes (plan: screen vision).

Captures the screen and reads it with a vision model: which app is open,
which fields are filled, what is unsaved, where the error is, how far a
task has got. Powers three features:
* "finish this for me" handover — complete a task where the boss left off.
* "explain this simply" — teach what is on the screen, in simple English
  or Roman Urdu.
* learn-by-watching — record the steps of a repeated task as a routine draft.

Honest note: a small local vision model is slow on an old CPU with no GPU,
so the practical free route is a cloud vision model on a free tier (Gemini,
already in the tool stack) — screen vision needs the internet in the
practical setup. Without internet it says so plainly.

Builder F owns this file. Keep the public API exactly as declared here.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from pathlib import Path

import httpx

from ..config import Settings, get_settings
from ..safety import ActionKind, Confirmation
from .base import TaskContext, Worker, WorkerResult

NO_KEY_MESSAGE = (
    "Boss, screen vision needs a Gemini key (a free GEMINI_API_KEY in the "
    ".env file) and the internet — without it I can take a screenshot, but "
    "I cannot read what is on it."
)

_GEMINI_MODEL = "gemini-2.0-flash"

# A remaining step that would submit, send, pay or publish something.
_SUBMIT_WORDS = ("submit", "send", "pay", "publish", "post ", "confirm payment", "place order")

_ROMAN_URDU_WORDS = {
    "samjhao", "samjha", "samjhaye", "yeh", "ye", "kya", "hai", "kaise",
    "asan", "asaan", "matlab", "batao", "bata", "isko", "is", "ka", "ki",
    "mujhe", "zara", "thoda",
}


def _gemini_url(api_key: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GEMINI_MODEL}:generateContent?key={api_key}"
    )


def _mime_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


class ScreenVisionWorker(Worker):
    """See the screen and understand it. Builder F implements this."""

    name = "vision"
    intents = {"screenshot", "complete_this", "explain_screen"}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    # -- helpers ----------------------------------------------------------
    def _get_settings(self, ctx: TaskContext | None = None) -> Settings:
        if ctx is not None and ctx.settings is not None:
            return ctx.settings
        if self.settings is not None:
            return self.settings
        return get_settings()

    def _vision_call(self, image_path: str, prompt: str) -> str | None:
        """Call Gemini vision once. Returns None when there is no key.

        Raises RuntimeError when the image cannot be read or the API
        call fails, so callers can report it plainly.
        """
        settings = self._get_settings()
        api_key = (settings.gemini_api_key or "").strip()
        if not api_key:
            return None
        path = Path(image_path)
        if not path.is_file():
            raise RuntimeError(f"I could not find that screenshot: {image_path}")
        try:
            image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise RuntimeError(f"I could not read that screenshot: {exc}") from exc
        try:
            resp = httpx.post(
                _gemini_url(api_key),
                json={
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {
                                    "inline_data": {
                                        "mime_type": _mime_type(path),
                                        "data": image_b64,
                                    }
                                },
                            ]
                        }
                    ]
                },
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Gemini screen vision failed: {exc}") from exc
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Gemini returned an unexpected reply: {data!r}") from exc
        return text

    # -- public API -------------------------------------------------------
    def screenshot(self, path: str | None = None) -> str:
        """Capture the screen and return the image path.

        Raises RuntimeError with a plain message when screenshots are not
        available on this machine (no pyautogui / no display).
        """
        try:
            import pyautogui
        except Exception as exc:  # noqa: BLE001 - any import/display failure
            raise RuntimeError(
                "Screenshot not available on this machine: pyautogui could "
                f"not be loaded ({exc}). Screenshots need a graphical "
                "desktop with pyautogui installed."
            ) from exc
        if path is None:
            settings = self._get_settings()
            folder = Path(settings.data_dir) / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = str(folder / f"screenshot-{stamp}.png")
        try:
            pyautogui.screenshot().save(path)
        except Exception as exc:  # noqa: BLE001 - e.g. no display
            raise RuntimeError(
                "Screenshot not available on this machine: the screen "
                f"could not be captured ({exc})."
            ) from exc
        return path

    def describe(self, image_path: str) -> str:
        """Describe what is on the screen, in plain words."""
        prompt = (
            "Describe this screenshot in plain words: which app is open, "
            "which fields are filled in, what is still empty or unsaved, "
            "and any error message you can see. Keep it short and simple."
        )
        try:
            text = self._vision_call(image_path, prompt)
        except RuntimeError as exc:
            return str(exc)
        if text is None:
            return NO_KEY_MESSAGE
        return text

    def handover_plan(self, image_path: str) -> dict:
        """Work out the task and how far it got: {"app", "task", "done", "remaining"}."""
        prompt = (
            "Look at this screenshot and work out what task the user was "
            "doing. Reply with ONLY a JSON object, no other text, with "
            "these keys: \"app\" (the app name), \"task\" (one short line "
            "describing the task), \"done\" (list of steps already finished), "
            "\"remaining\" (list of steps still to do). Example: "
            "{\"app\": \"Excel\", \"task\": \"Fill the attendance sheet\", "
            "\"done\": [\"Opened the file\"], \"remaining\": [\"Fill column B\"]}."
        )
        try:
            text = self._vision_call(image_path, prompt)
        except RuntimeError as exc:
            return {
                "app": "unknown",
                "task": "",
                "done": [],
                "remaining": [],
                "note": str(exc),
            }
        if text is None:
            return {
                "app": "unknown",
                "task": "",
                "done": [],
                "remaining": [],
                "note": NO_KEY_MESSAGE,
            }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                plan = json.loads(match.group(0))
                return {
                    "app": str(plan.get("app") or "unknown"),
                    "task": str(plan.get("task") or ""),
                    "done": [str(s) for s in (plan.get("done") or [])],
                    "remaining": [str(s) for s in (plan.get("remaining") or [])],
                }
            except (json.JSONDecodeError, AttributeError):
                pass
        # The model did not return JSON — keep its words as the task.
        return {
            "app": "unknown",
            "task": text.strip()[:500],
            "done": [],
            "remaining": [text.strip()[:500]],
        }

    def explain(self, image_path: str, question: str = "explain this simply") -> str:
        """Explain the screen in simple words, in the question's language."""
        words = set(re.findall(r"[a-z]+", question.lower()))
        roman_urdu = bool(words & _ROMAN_URDU_WORDS)
        if roman_urdu:
            prompt = (
                "Is screenshot ko bohat asaan Roman Urdu mein samjhao, jaise "
                "aap kisi dost ko samjha rahe hon. Mushkil alfaz istemal na "
                "karo. Sawal: " + question
            )
        else:
            prompt = (
                "Explain this screenshot in very simple words, as if to a "
                f"friend who is not technical. Keep it short. Question: {question}"
            )
        try:
            text = self._vision_call(image_path, prompt)
        except RuntimeError as exc:
            return str(exc)
        if text is None:
            return NO_KEY_MESSAGE
        return text

    # -- dispatch ---------------------------------------------------------
    def _intent_from_ctx(self, ctx: TaskContext) -> str:
        intent = (ctx.slots or {}).get("intent") or (ctx.slots or {}).get("name") or ""
        if intent in self.intents:
            return intent
        text = (ctx.text or "").lower()
        if any(w in text for w in ("complete", "finish", "poora", "poori")):
            return "complete_this"
        if any(w in text for w in ("explain", "samjhao", "samjha", "ye kya")):
            return "explain_screen"
        return "screenshot"

    def run(self, ctx: TaskContext) -> WorkerResult:
        intent = self._intent_from_ctx(ctx)
        settings = self._get_settings(ctx)
        dry_run = bool(ctx.dry_run or settings.dry_run)

        if intent == "screenshot":
            if dry_run:
                return WorkerResult.done("Dry run: would take a screenshot of the screen.")
            try:
                path = self.screenshot((ctx.slots or {}).get("path"))
            except RuntimeError as exc:
                return WorkerResult.failed(str(exc))
            return WorkerResult.done(f"Screenshot saved to {path}.", path=path, image=path)

        # explain_screen / complete_this both need a picture of the screen.
        image = (ctx.slots or {}).get("image") or (ctx.slots or {}).get("path")
        if image is None:
            if dry_run:
                image = "<screenshot>"
            else:
                try:
                    image = self.screenshot()
                except RuntimeError as exc:
                    return WorkerResult.failed(str(exc))

        if intent == "explain_screen":
            if dry_run:
                return WorkerResult.done(
                    "Dry run: would take a screenshot and explain it simply."
                )
            question = (ctx.slots or {}).get("question") or ctx.text or "explain this simply"
            explanation = self.explain(image, question)
            return WorkerResult.done(explanation, image=image, explanation=explanation)

        # complete_this — hand over, never silently submit or send.
        if dry_run:
            return WorkerResult.done(
                "Dry run: would take a screenshot and work out the handover plan."
            )
        plan = self.handover_plan(image)
        app = plan.get("app", "unknown")
        task = plan.get("task", "")
        done = plan.get("done", [])
        remaining = plan.get("remaining", [])
        lines = [f"On screen: {app} — {task or 'task unclear'}."]
        if done:
            lines.append("Already done: " + "; ".join(done) + ".")
        if remaining:
            lines.append("Still to do: " + "; ".join(remaining) + ".")
        if plan.get("note"):
            lines.append(plan["note"])
        summary = " ".join(lines)

        next_step = remaining[0] if remaining else ""
        if next_step and any(w in next_step.lower() for w in _SUBMIT_WORDS):
            confirmation = Confirmation(
                ActionKind.SEND,
                f"Next step on screen ({app}): {next_step} — this would submit/send it",
                target=app,
            )
            return WorkerResult(
                ok=False,
                summary=summary
                + " The next step would submit or send something, so I am "
                "waiting for your yes before I do it, boss.",
                data={"plan": plan, "image": image},
                needs_confirmation=confirmation,
            )
        return WorkerResult.done(summary, plan=plan, image=image)
