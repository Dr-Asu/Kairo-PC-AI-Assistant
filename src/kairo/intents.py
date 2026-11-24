"""Intent registry — fixed commands are matched FIRST, LLM second.

Everyday commands (open an app, set the volume, lock the PC) never go
through the cloud brain. They match a template here, run in milliseconds,
offline, and predictably. Only when nothing matches does a task fall
through to the LLM (research lesson 1).

Commands and replies work in English and Roman Urdu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class IntentMatch:
    name: str
    slots: dict[str, str]
    raw: str


@dataclass
class Intent:
    name: str
    patterns: list[str]
    handler: str  # worker that handles it, e.g. "system", "health", "boss"
    needs_confirmation: bool = False
    description: str = ""
    _compiled: list[re.Pattern] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def match(self, text: str) -> IntentMatch | None:
        for pattern in self._compiled:
            m = pattern.search(text.strip())
            if m:
                slots = {k: v for k, v in m.groupdict().items() if v is not None}
                return IntentMatch(name=self.name, slots=slots, raw=text)
        return None


class IntentRegistry:
    """Ordered list of intents; the first match wins."""

    def __init__(self, intents: list[Intent] | None = None) -> None:
        self.intents: list[Intent] = list(intents) if intents else []

    def register(self, intent: Intent) -> None:
        self.intents.append(intent)

    def match(self, text: str) -> IntentMatch | None:
        for intent in self.intents:
            hit = intent.match(text)
            if hit:
                return hit
        return None


def builtin_intents() -> list[Intent]:
    """The fixed commands the assistant knows out of the box."""
    return [
        Intent(
            "open_file",
            [
                r"open\s+(?P<file>.+?)\s+(?:it\s+is\s+in|in)\s+(?:the\s+)?(?P<folder>download|downloads|desktop|documents)\s*(?:folder)?(?:\s+of\s+my\s+pc)?\s*$",
                r"open\s+(?P<file>.+?)\s+from\s+(?:the\s+)?(?P<folder>download|downloads|desktop|documents)\s*(?:folder)?\s*$",
                r"open\s+(?:the\s+)?(?P<file>.+?\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|png|jpe?g|zip|mp4|mp3))\s*$",
                r"open\s+(?:the\s+)?(?P<file>.+?)\s+file\s*$",
            ],
            "files",
            description="Open a file by name — 'open tabz report in downloads', 'open report.pdf'.",
        ),
        Intent(
            "go_to_site",
            [
                r"(?:open|kholo)\s+(?P<site>youtube|google|gmail|facebook|instagram|twitter|x|whatsapp|github|linkedin|reddit|netflix|chatgpt|wikipedia|stackoverflow|amazon|daraz)\s*$",
                r"(?:open|kholo)\s+(?P<site>[a-z0-9][a-z0-9\-]*\.(?:com|pk|org|net|io|ai|co|edu|gov)(?:\.[a-z]{2,})?)\s*$",
            ],
            "browser",
            description="Open a website — 'open youtube', 'open google', 'open example.com'.",
        ),
        Intent(
            "open_app",
            [r"(?:open|launch|start|kholo|khol\s+do)\s+(?P<app>.+?)\s*$"],
            "system",
            description="Open an app by name — 'open notepad', 'kholo chrome'.",
        ),
        Intent(
            "close_app",
            [r"(?:close|quit|band\s+karo|band\s+kar\s+do)\s+(?P<app>.+?)\s*$"],
            "system",
            description="Close an app by name — 'close notepad', 'band karo chrome'.",
        ),
        Intent(
            "list_apps",
            [
                r"(?:what|which)\s+apps?(?:\s+(?:do\s+you\s+know|can\s+you\s+open))?\s*$",
                r"list\s+(?:apps?|programs?)\s*$",
                r"(?:show|tell)\s+me\s+(?:the\s+)?apps?\s*$",
            ],
            "system",
            description="List the apps Kairo knows — 'list apps', 'what apps do you know'.",
        ),
        Intent(
            "rescan_apps",
            [r"(?:rescan|refresh|find\s+new|check\s+for\s+new)\s+apps?\s*$"],
            "system",
            description="Re-scan for newly installed apps — 'rescan apps'.",
        ),
        Intent(
            "set_volume",
            [r"(?:volume|awaz)\s*(?:to|set|kar\s+do)?\s*(?P<level>\d{1,3})"],
            "system",
            description="Set the volume 0–100 — 'volume 50', 'awaz 30'.",
        ),
        Intent(
            "set_brightness",
            [r"(?:brightness|roshni)\s*(?:to|set|kar\s+do)?\s*(?P<level>\d{1,3})"],
            "system",
            description="Set the brightness 0–100 — 'brightness 70'.",
        ),
        Intent(
            "lock_pc",
            [r"(?:lock\s+(?:my\s+)?(?:pc|computer)|pc\s+(?:ko\s+)?lock\s+karo)"],
            "system",
            description="Lock the PC — 'lock my pc'.",
        ),
        Intent(
            "sleep_pc",
            [r"(?:sleep|pc\s+(?:ko\s+)?sleep\s+karo)"],
            "system",
            description="Put the PC to sleep.",
        ),
        Intent(
            "shutdown_pc",
            [r"(?:shut\s?down|power\s+off|pc\s+(?:ko\s+)?band\s+karo|so\s+jao)"],
            "system",
            needs_confirmation=True,
            description="Shut the PC down — always asks first.",
        ),
        Intent(
            "restart_pc",
            [r"(?:restart|pc\s+restart\s+karo)"],
            "system",
            needs_confirmation=True,
            description="Restart the PC — always asks first.",
        ),
        Intent(
            "mute",
            [r"^(?:mute|awaz\s+band\s+karo)\s*$"],
            "system",
            description="Mute the sound.",
        ),
        Intent(
            "unmute",
            [r"^(?:unmute|awaz\s+chalu\s+karo)\s*$"],
            "system",
            description="Unmute the sound.",
        ),
        Intent(
            "what_time",
            [r"(?:what(?:'s| is)?\s+the\s+time|what\s+time\s+is\s+it|time\s+kya\s+hai|waqt\s+kya\s+hai)"],
            "boss",
            description="Tell the time — 'what time is it', 'waqt kya hai'.",
        ),
        Intent(
            "what_date",
            [r"(?:what(?:'s| is) (?:the )?date|aaj\s+(?:ki\s+)?tarikh\s+kya\s+hai|aaj\s+kya\s+hai)"],
            "boss",
            description="Tell the date.",
        ),
        Intent(
            "stop",
            [r"^(?:stop|cancel|ruk\s+jao|bas\s+karo)\s*$"],
            "boss",
            description="Stop the current task — 'stop', 'ruk jao'.",
        ),
        Intent(
            "focus_mode_on",
            [r"(?:focus\s+mode|focus\s+mode\s+on|focus\s+mode\s+lagao)"],
            "system",
            description="Silence notifications and block distracting apps.",
        ),
        Intent(
            "focus_mode_off",
            [r"(?:stop\s+focus\s+mode|focus\s+mode\s+off|focus\s+mode\s+band\s+karo)"],
            "system",
            description="End focus mode.",
        ),
        Intent(
            "screen_time",
            [r"(?:screen\s+time|aaj\s+kitni\s+der\s+screen|kitna\s+time\s+screen)"],
            "health",
            description="Today's screen time — 'screen time', 'aaj kitni der screen'.",
        ),
        Intent(
            "tiktok_time",
            [r"(?:tiktok\s+(?:time|minutes|kitna)|tiktok\s+pe\s+kitna\s+time)"],
            "health",
            description="Today's TikTok minutes.",
        ),
        Intent(
            "take_break",
            [r"(?:take\s+a\s+break|break\s+le\s+lo|main\s+thak\s+gaya|i'?m\s+tired|i\s+need\s+rest)"],
            "health",
            description="Start a rest handover — 'I'm tired', 'main thak gaya'.",
        ),
        Intent(
            "start_timer",
            [r"(?:timer|set\s+a\s+timer)\s*(?:for\s+)?(?P<minutes>\d+)\s*(?:minutes?|minute|min)"],
            "scheduler",
            description="Start a timer — 'timer for 10 minutes'.",
        ),
        Intent(
            "screenshot",
            [r"(?:screenshot|screen\s+ki\s+photo|screen\s+capture)"],
            "vision",
            description="Take a screenshot.",
        ),
        Intent(
            "complete_this",
            [r"(?:complete\s+this|finish\s+this(?:\s+for\s+me)?|ye\s+(?:poora|complete)\s+karo)"],
            "vision",
            description="Finish the task on screen where I left off.",
        ),
        Intent(
            "open_browser",
            [r"(?:open|kholo)\s+(?P<browser>chrome|edge|firefox)\s*$"],
            "browser",
            description="Open a browser — 'open chrome', 'kholo edge'.",
        ),
        Intent(
            "help",
            [r"^(?:help|madad|kya\s+kar\s+sakte\s+ho)\s*$"],
            "boss",
            description="List what the assistant can do.",
        ),
    ]


def default_registry() -> IntentRegistry:
    return IntentRegistry(builtin_intents())
