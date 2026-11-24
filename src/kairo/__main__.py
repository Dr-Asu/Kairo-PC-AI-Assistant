"""Kairo Assistant entry point.

Usage:
    python -m kairo            text mode — type commands, get replies
    python -m kairo --voice    voice mode — hands-free when a wake engine is installed, else push-to-talk
    python -m kairo --voice --wake   hands-free voice — wake word, no Enter key
    python -m kairo --hud      serve the web dashboard on 127.0.0.1:8765
    python -m kairo --learn    run the nightly learning job once, then exit
    python -m kairo --check    print status (brain, voice, memory) and exit
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kairo", description="Kairo Assistant")
    parser.add_argument("--voice", action="store_true", help="voice mode (hands-free when a wake engine is installed, else push-to-talk)")
    parser.add_argument("--wake", action="store_true", help="hands-free voice: wake word instead of Enter (needs openWakeWord; use with --voice)")
    parser.add_argument("--hud", action="store_true", help="serve the web dashboard")
    parser.add_argument("--learn", action="store_true", help="run the nightly learning job once")
    parser.add_argument("--check", action="store_true", help="print status and exit")
    parser.add_argument("--port", type=int, default=8765, help="dashboard port (default 8765)")
    args = parser.parse_args(argv)

    from .boss import build_boss
    from .config import get_settings

    settings = get_settings()
    boss = build_boss(settings)

    # Background service: usage tracking + health ladder, ticking every
    # minute. Started for every interactive mode (text, voice, HUD), but
    # not for --check / --learn, which exit immediately.
    service = None
    if not (args.check or args.learn):
        from .service import BackgroundService

        service = BackgroundService(settings, memory=getattr(boss, "memory", None))
        service.start()

    if args.check:
        print(boss.status_text())
        return 0

    if args.learn:
        from .memory import MemoryStore

        store = MemoryStore(settings.data_dir)
        learned = store.run_learning_job(boss.brain)
        print(f"Learning job done, boss. {learned} new learning(s) written.")
        return 0

    if args.hud:
        from .hud.app import create_app
        import uvicorn

        app = create_app(boss)
        print(f"Dashboard at http://127.0.0.1:{args.port} — boss, open it in your browser.")
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return 0

    if args.voice:
        from .voice.base import NotAvailable
        from .voice.loop import VoiceLoop, pick_voice_mode

        loop = VoiceLoop(boss, settings)
        if pick_voice_mode(loop, args.wake) == "hands_free":
            try:
                loop.run_hands_free()
            except NotAvailable as exc:
                print(f"[kairo] {exc}")
                print("[kairo] Falling back to push-to-talk voice mode.")
                loop.run()
        else:
            print(
                '[kairo] Hands-free needs openWakeWord — install it with '
                'pip install "openwakeword>=0.3" and restart, and voice '
                "mode will listen on its own. Push-to-talk for now."
            )
            loop.run()
        return 0

    # Text mode — the default, and the fastest way to try it.
    print("Kairo Assistant — text mode. Type a command, boss (or 'help', 'quit').")
    print(boss.status_text())
    while True:
        try:
            text = input("boss> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye, boss.")
            return 0
        if not text:
            continue
        if text.lower() in {"quit", "exit", "bye"}:
            print("Goodbye, boss.")
            return 0
        print(boss.handle(text))


if __name__ == "__main__":
    sys.exit(main())
