# Kairo Assistant — a free, local-first PC AI assistant

One assistant that lives on your Windows 11 laptop, listens to your voice, and
does the work for you. A **boss agent** understands your task, hands it to
specialist **worker agents** (Office, Files, Browser, WhatsApp, Health Coach,
and more), checks their work, and reports back — spoken out loud and on a
small dashboard.

**100% free.** Small models run on your laptop; the big brain runs on a free
cloud tier (Groq or Google AI Studio Gemini). Everything else is open source.
No paid service, no card, no trial.

> This project is completely separate from StudyBot. Nothing here shares code,
> accounts, or data with StudyBot.

## Quick start (Windows 11)

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/
   (tick "Add python.exe to PATH" during install).
2. Open PowerShell in this folder and create the environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   pip install -e . --no-deps
   ```
   (The second line makes the `kairo` command and `python -m kairo`
   work from this folder.)
3. Copy `.env.example` to `.env` and add **one** free key — either
   `GROQ_API_KEY` (https://console.groq.com/keys) or `GEMINI_API_KEY`
   (https://aistudio.google.com/apikey). Without a key the assistant still
   works in **local mode** (fixed commands, intents, local voice) and says so
   plainly.
4. Run it:
   ```powershell
   # Text mode (type commands — the fastest way to try it)
   python -m kairo
   # Voice mode (push-to-talk; needs the optional voice packages)
   python -m kairo --voice
   # Web dashboard on http://127.0.0.1:8765
   python -m kairo --hud
   ```
5. Talk to it. Try: `open notepad`, `what time is it`, `volume 50`,
   `kholo chrome` (Roman Urdu works too), `help`.

## What it can do

- **Voice loop** — push-to-talk first, wake word later; openWakeWord →
  faster-whisper → Piper, all local and free (see `src/kairo/voice/`).
- **Boss + workers** — intents matched first (fast, offline), LLM fallback
  second; workers run in parallel where possible and the Tester verifies
  before anything is delivered (see `src/kairo/boss.py`).
- **Full PC control** — open/close any app, volume, brightness, lock, sleep,
  shutdown (always asks first), screenshots, and winget installs (ask first).
- **Office Worker** — Excel (openpyxl), Word (python-docx), PowerPoint
  (python-pptx) with real formulas and formatting, verified by the Tester.
- **File Organizer, Browser Agent** (Playwright: Chrome, Edge, Firefox),
  **Scheduler/routines**, **briefings**, **calendar/deadlines**.
- **Memory & learning** — local SQLite memory, nightly learning job, weekly
  memory report. Nothing leaves your PC except the text of a request to the
  free cloud brain.
- **Health protection** — usage tracking, warnings, and the rest-enforcement
  ladder (PC only), plus **rest handover**: you rest, it finishes the work.
- **Screen Vision + "finish this for me"** — it reads your screen and
  completes a task where you left off.
- **WhatsApp Worker** — read, reply, and send files through WhatsApp Web
  (Playwright) and the Windows WhatsApp app (UI Automation). Ask-first:
  it always reads back the recipient and the exact message before sending.
  Completely separate from StudyBot.
- **Phone bridge** — the TECNO companion app posts usage/health to the PC,
  and the phone can command the PC over Tailscale with a secret token.

## Safety (built into the code, not just words)

- Deleting, moving, overwriting, sending, installing, spending, shutting
  down, or sharing data **always asks first** (`src/kairo/safety.py`).
- The boss **never obeys instructions hidden in files, emails, or web
  pages** — untrusted content is wrapped and treated as data, never as
  orders. Only your voice or typed message is an order.
- No passwords are ever handled by the assistant. You log in once, in your
  own browser; the assistant never types a password or bypasses a login wall.

## Layout

```
src/kairo/
  config.py    settings from .env / environment
  safety.py    ask-first permission gate + injection guard
  intents.py   intent registry (fixed commands first, EN + Roman Urdu)
  state.py     assistant + task state machines, barge-in
  brain.py     cloud brain (Groq/Gemini free tiers) + local fallback
  boss.py      the boss agent: route, dispatch, verify, report
  memory.py    local SQLite memory store + learning job
  voice/       wake word, STT, TTS, push-to-talk, voice loop
  pc/          System Controller, usage tracking, health ladder, tray
  workers/     Office, Files, Browser, WhatsApp, Vision, Scheduler, Phone
  hud/         FastAPI dashboard + phone bridge
tests/         pytest suite (run: .venv/bin/pytest -q  — on Windows: .venv\Scripts\pytest -q)
```

## Status

See `../STATUS.md` for what is working, what is scaffolded, and what still
needs your PC or your free API key. Nothing is claimed as done unless it is
tested — `pytest` results are recorded there.

**v0.1 (23 Nov 2026) — what runs:** text mode, voice mode (text fallback
without a microphone), the web dashboard, the nightly learning job, the
background service (usage tracking + health ladder, one tick a minute),
and all workers in dry-run / local mode. Voice, WhatsApp Web, and the
Windows-app routes need their optional libraries and your own logins on
the Windows PC — see STATUS.md.

**v0.2 (24 Nov 2026):** renamed from Stonic Assistant to Kairo (name chosen by the user); no behaviour change.

**v0.2.1 (24 Nov 2026):** Gemini model updated — Google retired `gemini-2.0-flash`, now uses `gemini-3.8-flash` (brain + screen vision).

**v0.2.2 (24 Nov 2026):** brain fallback — with both keys set, if Groq fails the boss now tries Gemini instead of dropping to local mode (and vice versa). One provider being down or overloaded no longer kills the cloud brain.

**v0.2.3 (24 Nov 2026):** Groq model updated — `llama-3.3-70b-versatile` went enterprise-only on Groq (Aug 2026), now uses `openai/gpt-oss-120b`. Model names are now changeable in `.env` (`GROQ_MODEL` / `GEMINI_MODEL`), so a future retirement needs no code fix.

**v0.3.0 (24 Nov 2026):** apps are no longer a hardcoded list. Kairo scans the PC for every installed app (Start Menu + App Paths registry), matches names forgivingly ("excell" still finds Excel), re-scans automatically when a name is not found, and — the real fix — **verifies the app actually started** before saying "Opened". A launch that never starts is now reported as failed, with the reason, instead of a false success. New commands: `list apps`, `rescan apps`; `close <app>` also verifies the app really closed.

**v0.3.1 (24 Nov 2026):** "open <file>" now works. Kairo searches Downloads, Desktop and Documents for the file by name ("open tabz dashboard report in downloads" finds the TABZ report PDF), asks when two files tie, and says plainly when nothing matches. If you say "open <name>" without a folder phrase and it is not an app, Kairo now checks your files too before giving up — and "open excel file" falls back to the Excel app. Also fixed a latent bug: the files worker never read the boss's `_intent` slot, so copy/move/arrange sent by voice never fired.

**v0.3.2 (24 Nov 2026):** hands-free voice. `python -m kairo --voice --wake` listens for the wake word all the time — say "Hey Jarvis" (or "Alexa"), give your command, Kairo does it and answers out loud, then waits for the wake word again. No Enter key, no typing. Say "quit" or press Ctrl+C to leave. Needs `pip install "openwakeword>=0.3"` (it downloads the wake-word models on first use; 0.2.x crashes without tflite-runtime). A custom "Kairo" wake word needs a trained model — a later step. Also pinned `av<19` for faster-whisper (PyAV 19 broke transcription).

**v0.3.3 (24 Nov 2026):** voice replies now actually play on stock Windows. edge-tts was synthesizing the reply MP3 and then printing "No audio player found" — it only knew mpv/ffplay/mpg123, none of which a normal Windows PC has. Added a built-in Windows MCI player (winmm, no extra installs) that plays the MP3 through Windows itself; barge-in stop still works.

**v0.3.4 (24 Nov 2026):** hands-free no longer crashes on old openWakeWord. If the installed openWakeWord is 0.2.x (tflite models, needs tflite-runtime), the adapter now says so plainly and falls back to push-to-talk instead of dying with a traceback. Install `pip install "openwakeword>=0.3"` — 0.3+ is ONNX-only, no extra install needed.

**v0.3.5 (24 Nov 2026):** `--voice` is now fully automatic by default. If openWakeWord is installed, plain `python -m kairo --voice` goes hands-free on its own — no `--wake` flag needed, no Enter key. It only falls back to push-to-talk when no wake engine is installed, and then it tells you the install command instead of just asking for Enter.

**v0.3.6 (24 Nov 2026):** hands-free now actually works on Python 3.14. The real cause: pip installs openWakeWord **0.6.0** on Python 3.14, and 0.6.0 defaults to tflite models — but tflite-runtime has no Python 3.14 wheel, so it can never install there. Kairo now downloads the wake-word models itself on first run and loads the **ONNX** versions instead (onnxruntime is already there). No tflite needed, ever. First run downloads the models once (~15 MB, needs internet).
