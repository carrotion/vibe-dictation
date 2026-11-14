"""Dictation service: one utterance per hotkey press, pasted at the cursor.

The hotkey sets ``active``. A background worker records one utterance, transcribes
it locally, pastes it into the focused input, and returns to idle. Pressing the
hotkey while active cancels the recording and discards it.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .audio import record_utterance
from .clipboard import copy_and_paste
from .config import AppConfig
from .state import DictationState
from .stt import WhisperStt

logger = logging.getLogger(__name__)


class DictationService:
    def __init__(self, config: AppConfig, on_change: Callable[[dict], None] | None = None,
                 state: DictationState | None = None):
        self.config = config
        self.state = state or DictationState()
        self.on_change = on_change
        self.active = threading.Event()
        self.shutdown_event = threading.Event()
        self._stt = WhisperStt(config)
        self._worker_thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------
    def start_engine(self) -> None:
        if self._worker_thread is None:
            self._worker_thread = threading.Thread(target=self._worker, daemon=True,
                                                   name="dictation-worker")
            self._worker_thread.start()

    def warmup(self) -> None:
        self._stt.warmup()

    def shutdown(self) -> None:
        self.shutdown_event.set()
        self.active.clear()
        self._set("idle", elapsed_s=0.0)

    # -- commands --------------------------------------------------------
    def snapshot(self) -> dict:
        return self.state.snapshot()

    def start(self) -> bool:
        if self.active.is_set():
            return True
        self.active.set()
        self._set("listening", elapsed_s=0.0, last_error="")
        return True

    def stop(self) -> None:
        self.active.clear()
        self._set("idle", elapsed_s=0.0)

    def toggle(self) -> bool:
        if self.active.is_set():
            self.stop()
            return False
        return self.start()

    # -- internals -------------------------------------------------------
    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.snapshot())
            except Exception:
                pass

    def _set(self, status: str, **fields) -> None:
        self.state.set(active=self.active.is_set(), status=status, **fields)
        self._notify()

    def _deliver(self, text: str) -> None:
        """Paste the transcript and go idle. Clipboard errors surface in the log."""
        self.active.clear()
        try:
            copy_and_paste(text, paste=self.config.paste)
        except Exception as exc:
            logger.warning("clipboard failed: %s", exc)
            self._set("error", last_error=str(exc)[:200], elapsed_s=0.0)
            return
        logger.info("pasted %d characters", len(text))
        self._set("pasted", elapsed_s=0.0)

    def _transcribe(self, audio):
        """Run transcription with a watchdog; switch to CPU if it stalls."""
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = self._stt.transcribe(audio)
            except Exception as exc:  # noqa: BLE001 - re-raised on the caller side
                box["error"] = exc

        thread = threading.Thread(target=run, daemon=True, name="stt-call")
        thread.start()
        thread.join(max(1.0, float(self.config.transcribe_timeout_s)))
        if thread.is_alive():
            logger.error("transcription timed out after %ss", self.config.transcribe_timeout_s)
            self._stt.fallback_to_cpu()
            raise TimeoutError("transcription timed out; switched to CPU")
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _worker(self) -> None:
        audio_config = self.config.audio()
        wait_s = max(2.0, float(self.config.wait_s))

        def on_frame(rms: float, _event: str, elapsed: float) -> None:
            self.state.level(rms, elapsed)
            self._notify()

        while not self.shutdown_event.is_set():
            if not self.active.is_set():
                time.sleep(0.15)
                continue
            self._set("listening", elapsed_s=0.0)
            rec = record_utterance(
                audio_config, max_wait_s=wait_s,
                on_frame=on_frame,
                should_stop=lambda: not self.active.is_set(),
            )
            if not self.active.is_set():
                continue
            if rec.reason != "ok" or rec.audio is None:
                # Nothing usable was said (or the press was cancelled): turn off.
                logger.info("recording ended without speech: %s", rec.reason)
                self.stop()
                continue
            logger.info("recorded %s ms of audio", rec.elapsed_ms)
            self._set("transcribing")
            try:
                result = self._transcribe(rec.audio)
            except Exception as exc:
                self.active.clear()
                logger.warning("transcription failed: %s", exc)
                self._set("error", last_error=str(exc)[:200], elapsed_s=0.0)
                continue
            if not self.active.is_set():
                logger.info("cancelled during transcription; result dropped")
                continue
            text = result.text.strip()
            if text:
                self._deliver(text)
            else:
                logger.info("nothing intelligible in %.2fs; nothing pasted", result.audio_s)
                self.stop()
