"""Pytest configuration for the Kairo Assistant test suite."""

import os
import sys
from pathlib import Path

# Make the src layout importable: `import kairo...` works in tests.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Tests never touch the real home directory.
os.environ.setdefault("KAIRO_DATA_DIR", str(Path(__file__).parent / ".test-data"))


def pytest_configure(config):
    """Register the windows_only marker used by Windows-only tests."""
    config.addinivalue_line("markers", "windows_only: tests that only run on Windows")
