"""Wake-word adapters — openWakeWord (default) and Porcupine (alternative).

Both are optional and guarded: if the library is not installed,
``available()`` is False and ``listen_once()`` raises ``NotAvailable`` —
construction never raises, so the assistant can probe for engines and
fall back to push-to-talk instead of crashing at import time.
Builder B owns this file.
"""

from __future__ import annotations

import os
import time

from .base import NotAvailable

# A prediction score at or above this counts as the wake word being heard.
WAKE_THRESHOLD = 0.5


class OpenWakeWordAdapter:
    """openWakeWord — free, local, CPU-friendly (default).

    ``model_path`` optionally points at a custom ``.onnx`` wake-word
    model (e.g. one trained for "Kairo"); without it the bundled
    default models are used. The model is loaded lazily on the first
    listen, so merely constructing the adapter costs nothing.
    """

    def __init__(self, wake_word: str = "assistant", model_path: str | None = None) -> None:
        self.wake_word = wake_word
        self.model_path = model_path
        self._model = None
        try:
            import openwakeword  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"openWakeWord is not installed ({exc})."
        else:
            self._available = True
            self._error = ""

    def available(self) -> bool:
        return self._available

    def _load_model(self):
        """Load the openWakeWord model on first use.

        openWakeWord 0.2.x defaults to tflite models and crashes without
        tflite-runtime (``ValueError: Tried to import the tflite runtime
        ...``); 0.3+ is ONNX-only and needs no extra install. That crash
        is converted to NotAvailable with the fix, so the caller falls
        back to push-to-talk instead of dying with a raw traceback.
        """
        if self._model is not None:
            return self._model
        from openwakeword.model import Model

        try:
            if self.model_path:
                self._model = Model(wakeword_models=[self.model_path])
            else:
                self._model = Model()
        except ValueError as exc:
            if "tflite" not in str(exc).lower():
                raise
            raise NotAvailable(
                "openWakeWord is too old (0.2.x needs tflite-runtime). "
                'Upgrade it: pip install "openwakeword>=0.3" — 0.3+ is '
                "ONNX-only and needs no extra install."
            ) from exc
        return self._model

    def listen_once(self, timeout: float = 10.0) -> bool:
        """Block until the wake word is heard; False if the timeout passes.

        Raises NotAvailable when openWakeWord (or a microphone) is missing.
        """
        if not self._available:
            raise NotAvailable(self._error or "openWakeWord is not installed.")
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise NotAvailable(
                "No microphone input is available (sounddevice is not installed)."
            ) from exc
        model = self._load_model()
        deadline = time.monotonic() + timeout
        # 16 kHz mono int16, 80 ms blocks — what openWakeWord expects.
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280) as stream:
            while time.monotonic() < deadline:
                block, _overflow = stream.read(1280)
                scores = model.predict(block)
                if any(score >= WAKE_THRESHOLD for score in scores.values()):
                    return True
        return False


class PorcupineAdapter:
    """Porcupine (Picovoice) — tiny footprint, free for personal use.

    Needs a free Picovoice access key: pass ``access_key`` or set the
    ``PICOVOICE_ACCESS_KEY`` environment variable. Without a key the
    adapter reports itself unavailable instead of crashing.
    """

    # Built-in keywords Porcupine ships; anything else needs a custom
    # keyword file (``keyword_paths``) from Picovoice Console.
    BUILTIN_KEYWORDS = {
        "alexa", "americano", "blueberry", "bumblebee", "computer",
        "grapefruit", "grasshopper", "hey google", "hey siri", "jarvis",
        "ok google", "picovoice", "porcupine", "terminator",
    }

    def __init__(
        self,
        wake_word: str = "assistant",
        access_key: str | None = None,
        keyword_paths: list[str] | None = None,
    ) -> None:
        self.wake_word = wake_word
        self.access_key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
        self.keyword_paths = keyword_paths
        try:
            import pvporcupine  # noqa: F401
        except ImportError as exc:
            self._available = False
            self._error = f"pvporcupine is not installed ({exc})."
        else:
            if not self.access_key:
                self._available = False
                self._error = (
                    "Porcupine needs a free Picovoice access key "
                    "(PICOVOICE_ACCESS_KEY is not set)."
                )
            elif keyword_paths is None and wake_word.lower() not in self.BUILTIN_KEYWORDS:
                self._available = False
                self._error = (
                    f"Porcupine has no built-in keyword {wake_word!r}; "
                    "train one in Picovoice Console and pass keyword_paths."
                )
            else:
                self._available = True
                self._error = ""

    def available(self) -> bool:
        return self._available

    def _create(self):
        import pvporcupine

        if self.keyword_paths:
            return pvporcupine.create(
                access_key=self.access_key, keyword_paths=self.keyword_paths
            )
        return pvporcupine.create(
            access_key=self.access_key, keywords=[self.wake_word.lower()]
        )

    def listen_once(self, timeout: float = 10.0) -> bool:
        """Block until the wake word is heard; False if the timeout passes.

        Raises NotAvailable when Porcupine (or a microphone) is missing.
        """
        if not self._available:
            raise NotAvailable(self._error or "Porcupine is not installed.")
        porcupine = self._create()
        try:
            # PvRecorder is the supported recorder; fall back to sounddevice.
            try:
                from pvrecorder import PvRecorder

                recorder = PvRecorder(
                    device_index=-1, frame_length=porcupine.frame_length
                )
                recorder.start()
                try:
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if porcupine.process(recorder.read()) >= 0:
                            return True
                    return False
                finally:
                    recorder.stop()
                    recorder.delete()
            except ImportError:
                try:
                    import sounddevice as sd
                except ImportError as exc:
                    raise NotAvailable(
                        "No microphone input is available "
                        "(pvrecorder / sounddevice is not installed)."
                    ) from exc
                deadline = time.monotonic() + timeout
                with sd.InputStream(
                    samplerate=porcupine.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=porcupine.frame_length,
                ) as stream:
                    while time.monotonic() < deadline:
                        block, _overflow = stream.read(porcupine.frame_length)
                        if porcupine.process(block.reshape(-1)) >= 0:
                            return True
                return False
        finally:
            porcupine.delete()
