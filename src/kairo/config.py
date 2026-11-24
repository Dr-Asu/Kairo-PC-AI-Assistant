"""Settings for the Kairo Assistant.

Keys come from the environment or a local ``.env`` file — never from code.
No key is ever hardcoded. Without a cloud key the assistant runs in
local-only mode (intents + local voice) and says so plainly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a core dep
    load_dotenv = None


def _load_dotenv() -> None:
    """Load a .env file from the project root or the current directory."""
    if load_dotenv is not None:
        load_dotenv()
        return
    # Tiny fallback parser so a missing python-dotenv never breaks startup.
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent.parent / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
            break


@dataclass
class Settings:
    """Everything the assistant needs to know about its setup."""

    groq_api_key: str = ""
    gemini_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"  # Llama 3.3 went enterprise-only Aug 2026
    gemini_model: str = "gemini-3.8-flash"  # 2.0-flash retired by Google Nov 2026
    kairo_token: str = ""
    language: str = "en"  # "en" English, "ur" Roman Urdu
    data_dir: Path = field(default_factory=lambda: Path.home() / ".kairo")
    wake_word: str = "assistant"
    tts_voice: str = ""  # empty = Piper default voice
    dry_run: bool = False

    # Health / warning thresholds (plan section 6 + rest ladder).
    tiktok_limit_min: int = 60
    sitting_warn_min: int = 120
    screen_cap_hours: int = 8
    sleep_min_hours: int = 6
    hard_limit_hours: int = 5
    bedtime: str = "02:00"

    @property
    def has_cloud_brain(self) -> bool:
        return bool(self.groq_api_key or self.gemini_api_key)

    @property
    def brain_name(self) -> str:
        if self.groq_api_key:
            return "groq"
        if self.gemini_api_key:
            return "gemini"
        return "local"


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    """Load settings once; every module shares the same instance."""
    global _SETTINGS
    if _SETTINGS is None:
        _load_dotenv()
        data_dir = os.environ.get("KAIRO_DATA_DIR") or str(Path.home() / ".kairo")
        _SETTINGS = Settings(
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
            or "openai/gpt-oss-120b",
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
            or "gemini-3.8-flash",
            kairo_token=os.environ.get("KAIRO_TOKEN", "").strip(),
            language=os.environ.get("KAIRO_LANGUAGE", "en").strip().lower() or "en",
            data_dir=Path(data_dir),
            wake_word=os.environ.get("KAIRO_WAKE_WORD", "assistant").strip(),
            tts_voice=os.environ.get("KAIRO_TTS_VOICE", "").strip(),
            dry_run=os.environ.get("KAIRO_DRY_RUN", "false").strip().lower() in {"1", "true", "yes"},
        )
        _SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
    return _SETTINGS


def reset_settings() -> None:
    """For tests only — forget the cached settings."""
    global _SETTINGS
    _SETTINGS = None
