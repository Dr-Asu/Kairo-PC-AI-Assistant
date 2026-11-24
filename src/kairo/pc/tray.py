"""System-tray app — a small window that shows what the assistant is doing.

Sits quietly in the tray (near the clock). Click it for: listening
indicator, current task, active workers, today's usage cards, warnings,
and quick buttons (mute, stop task, lock PC). Built with pystray when
available; on a machine without it, ``run()`` prints a plain message and
returns instead of crashing.

Builder C owns this file.
"""

from __future__ import annotations


def run_tray(boss) -> None:
    """Show the tray icon and its small window. Builder C implements this."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("Tray app needs pystray — install it on Windows")
        return

    def _status(icon, _item):  # noqa: ANN001, ANN202
        status = ""
        if hasattr(boss, "status_text"):
            try:
                status = boss.status_text()
            except Exception:  # noqa: BLE001 - tray must never crash
                status = ""
        print(status or "Kairo Assistant is running, boss.")

    def _mute(icon, _item):  # noqa: ANN001, ANN202
        mute = getattr(boss, "mute", None)
        if callable(mute):
            mute()
        print("Muted, boss.")

    def _stop_task(icon, _item):  # noqa: ANN001, ANN202
        stop = getattr(boss, "stop_task", None) or getattr(boss, "stop", None)
        if callable(stop):
            stop()
        print("Stopped the current task, boss.")

    def _lock(icon, _item):  # noqa: ANN001, ANN202
        from .controller import SystemController

        print(SystemController().lock())

    def _quit(icon, _item):  # noqa: ANN001, ANN202
        icon.stop()

    # A small plain icon: a dark square with a light "S".
    image = Image.new("RGB", (64, 64), (10, 58, 110))
    draw = ImageDraw.Draw(image)
    draw.text((20, 18), "S", fill=(143, 208, 232))

    menu = pystray.Menu(
        pystray.MenuItem("Show status", _status),
        pystray.MenuItem("Mute", _mute),
        pystray.MenuItem("Stop task", _stop_task),
        pystray.MenuItem("Lock PC", _lock),
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("kairo", image, "Kairo Assistant", menu)
    icon.run()
