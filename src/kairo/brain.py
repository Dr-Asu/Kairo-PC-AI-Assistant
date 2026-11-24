"""The brain — the cloud LLM on a free tier, with a local fallback.

Keys are read from the environment / .env only — never hardcoded, never
invented. Groq is tried first, then Gemini. With no key at all the
assistant runs in local-only mode and says so plainly (fixed commands still
work offline through the intent registry).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Settings
from .safety import SYSTEM_PROMPT_SAFETY


class BrainError(RuntimeError):
    """The cloud brain could not be reached or refused the request."""


@dataclass
class BrainReply:
    text: str
    model: str
    local: bool = False


class Brain:
    """The interface every brain implements."""

    name: str = "brain"

    def complete(self, system: str, user: str) -> BrainReply:  # pragma: no cover - interface
        raise NotImplementedError


class GroqBrain(Brain):
    """Groq free tier — OpenAI-compatible chat completions endpoint."""

    name = "groq"
    MODEL = "llama-3.3-70b-versatile"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str = MODEL) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> BrainReply:
        try:
            resp = httpx.post(
                self.URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.3,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BrainError(f"Groq request failed: {exc}") from exc
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise BrainError(f"Groq returned an unexpected reply: {data!r}") from exc
        return BrainReply(text=text, model=f"groq/{self.model}")


class GeminiBrain(Brain):
    """Google AI Studio (Gemini) free tier — REST generateContent endpoint."""

    name = "gemini"
    MODEL = "gemini-3.8-flash"  # 2.0-flash retired by Google 24 Nov 2026

    def __init__(self, api_key: str, model: str = MODEL) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> BrainReply:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
            f":generateContent?key={self.api_key}"
        )
        try:
            resp = httpx.post(
                url,
                json={"contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}]},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BrainError(f"Gemini request failed: {exc}") from exc
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            raise BrainError(f"Gemini returned an unexpected reply: {data!r}") from exc
        return BrainReply(text=text, model=f"gemini/{self.model}")


class LocalBrain(Brain):
    """No cloud key — local-only mode. Fixed commands still work offline.

    This brain never pretends to be the cloud. It says plainly that the
    assistant is in local mode and that open-ended thinking needs a free
    API key (Groq or Gemini) in the .env file.
    """

    name = "local"

    def complete(self, system: str, user: str) -> BrainReply:  # noqa: ARG002
        return BrainReply(
            text=(
                "Boss, I am in local mode right now — there is no cloud brain "
                "key in the .env file, so I can only run fixed commands "
                "(open apps, volume, timers, and the like). Add a free "
                "GROQ_API_KEY or GEMINI_API_KEY to the .env file and I will "
                "be able to think, plan, and write for you."
            ),
            model="local",
            local=True,
        )


def get_brain(settings: Settings) -> Brain:
    """Pick the brain: Groq first, then Gemini, then local-only."""
    if settings.groq_api_key:
        return GroqBrain(settings.groq_api_key)
    if settings.gemini_api_key:
        return GeminiBrain(settings.gemini_api_key)
    return LocalBrain()


def boss_system_prompt(memory_snippet: str = "") -> str:
    """System prompt for the boss, with the safety rules baked in."""
    prompt = SYSTEM_PROMPT_SAFETY
    if memory_snippet:
        prompt += f"\n\nWhat you remember about the boss (use only what is relevant):\n{memory_snippet}"
    return prompt
