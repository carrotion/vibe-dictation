"""faster-whisper wrapper with GPU probing, noise filtering and CPU fallback.

Loading a CUDA model does not prove it can transcribe: ctranslate2 resolves
cuBLAS/cuDNN lazily on the first encode, and a missing or mismatched DLL can
fail or stall there. ``probe()`` runs a tiny silent decode at startup and the
model falls back to CPU when that fails. Silero VAD strips non-speech before
decoding, and low-confidence segments are dropped so room noise does not turn
into invented text.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from .config import AppConfig
from .gpu_env import add_nvidia_dll_dirs

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 25.0
NO_SPEECH_PROB = 0.55
LOG_PROB = -1.0


@dataclass
class SttResult:
    text: str
    ms: float
    audio_s: float
    segments: int = 0
    device: str = ""


class WhisperStt:
    def __init__(self, config: AppConfig):
        self.config = config
        self._model = None
        self._lock = threading.Lock()
        self._use_cpu = (config.device or "auto").strip().lower() == "cpu"
        self.active_device = "cpu" if self._use_cpu else ""

    # -- model lifecycle -------------------------------------------------
    def _create(self, device: str):
        from faster_whisper import WhisperModel
        compute = "float16" if device == "cuda" else "int8"
        index = self.config.gpu_index if device == "cuda" else 0
        return WhisperModel(self.config.model, device=device, device_index=index,
                            compute_type=compute)

    def _ensure_model(self):
        with self._lock:
            if self._model is not None:
                return self._model
            add_nvidia_dll_dirs()
            if self._use_cpu:
                self._model = self._create("cpu")
                self.active_device = "cpu"
                logger.info("Whisper loaded on CPU")
                return self._model
            try:
                self._model = self._create("cuda")
                self.active_device = "cuda"
                logger.info("Whisper loaded on cuda:%s", self.config.gpu_index)
            except Exception as exc:
                logger.warning("CUDA unavailable (%s); loading CPU", exc)
                self._use_cpu = True
                self._model = self._create("cpu")
                self.active_device = "cpu"
            return self._model

    def fallback_to_cpu(self) -> None:
        with self._lock:
            if self._use_cpu:
                return
            logger.warning("switching STT to CPU")
            self._use_cpu = True
            self.active_device = "cpu"
            self._model = None

    def probe(self, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
        """Decode a short silent clip; catches DLL failures and CUDA stalls."""
        box: dict = {}

        def run() -> None:
            try:
                model = self._ensure_model()
                silence = np.zeros(8000, dtype=np.float32)
                segments, _ = model.transcribe(
                    silence, language=self.config.language or None, beam_size=1,
                    condition_on_previous_text=False, vad_filter=False,
                )
                list(segments)
                box["ok"] = True
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                box["error"] = str(exc)

        thread = threading.Thread(target=run, daemon=True, name="stt-probe")
        thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            logger.warning("STT probe timed out on %s", self.active_device or "cuda")
            return False
        if not box.get("ok"):
            logger.warning("STT probe failed: %s", box.get("error"))
            return False
        return True

    def warmup(self) -> None:
        if self.probe():
            logger.info("STT ready on %s", self.active_device)
            return
        if not self._use_cpu:
            self.fallback_to_cpu()
            if self.probe():
                logger.info("STT ready on CPU fallback")
                return
        logger.error("STT could not be initialised")

    # -- transcription ---------------------------------------------------
    def _decode(self, model, audio: np.ndarray, extra: dict):
        kwargs = {
            "beam_size": 5,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "log_prob_threshold": LOG_PROB,
        }
        if self.config.language:
            kwargs["language"] = self.config.language
        if self.config.hotwords:
            kwargs["hotwords"] = self.config.hotwords
        kwargs.update(extra)
        segments, _ = model.transcribe(audio, **kwargs)
        return list(segments)

    def transcribe(self, audio: np.ndarray) -> SttResult:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio_s = round(audio.size / 16_000, 2)
        if audio.size == 0:
            return SttResult("", 0.0, audio_s, 0, self.active_device)
        model = self._ensure_model()
        started = time.perf_counter()
        try:
            segments = self._decode(model, audio, {
                "vad_filter": True,
                "vad_parameters": {"min_silence_duration_ms": 500, "speech_pad_ms": 200},
            })
        except Exception as exc:
            # A broken Silero VAD model must not disable dictation.
            logger.warning("VAD filter failed (%s); retrying without it", exc)
            segments = self._decode(model, audio, {"vad_filter": False})
        ms = round((time.perf_counter() - started) * 1000, 1)

        parts = []
        kept = 0
        for segment in segments:
            if getattr(segment, "no_speech_prob", 0.0) > NO_SPEECH_PROB:
                continue
            if getattr(segment, "avg_logprob", 0.0) < LOG_PROB:
                continue
            parts.append(segment.text)
            kept += 1
        text = "".join(parts).strip()
        logger.info("transcribed %.2fs -> %.0f ms on %s (%d segs, %d chars)",
                    audio_s, ms, self.active_device, len(segments), len(text))
        return SttResult(text, ms, audio_s, kept, self.active_device)
