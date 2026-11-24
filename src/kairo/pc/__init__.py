"""PC control layer — System Controller, usage tracking, health ladder.

Windows-only libraries (pywin32, pycaw, pyautogui) are imported inside
functions and guarded: on Linux they raise ``NotAvailable`` and the
controller runs in dry-run mode instead. The assistant never crashes on
the wrong OS — it says plainly what it cannot do there.
"""

from .controller import SystemController
from .focus import FocusMode
from .health import HealthLadder
from .usage import UsageTracker

__all__ = ["SystemController", "FocusMode", "HealthLadder", "UsageTracker"]
