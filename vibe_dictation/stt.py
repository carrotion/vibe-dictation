"""faster-whisper wrapper with CUDA -> CPU fallback."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from .config import AppConfig
from .gpu_env import add_nvidia_dll_dirs

logger = logging.getLogger(__name__)


@dataclass
class SttResult:
    text: str
    ms: float
    audio_s: float
    segments: int = 0


class WhisperStt:
    def __init__(self, config: AppConfig):
        self.config = config
        self._model = None
        self.active_device = ""

    def _create(self, device: str):
        from faster_whisper import WhisperModel
        compute = "float16" if device == "cuda" else "int8"
        index = self.config.gpu_index if device == "cuda" else 0
        return WhisperModel(self.config.model, device=device, device_index=index,
                            compute_type=compute)

    def _load(self):
        if self._model is not None:
            return self._model
        add_nvidia_dll_dirs()
        wanted = (self.config.device or "auto").lower()
        if wanted in {"auto", "cuda"}:
            try:
                self._model = self._create("cuda")
                self.active_device = "cuda"
                logger.info("Whisper loaded on cuda:%s", self.config.gpu_index)
                return self._model
            except Exception as exc:
                if wanted == "cuda":
                    raise
                logger.warning("CUDA failed, falling back to CPU: %s", exc)
        self._model = self._create("cpu")
        self.active_device = "cpu"
        logger.info("Whisper loaded on CPU")
        return self._model

    def warmup(self) -> None:
        self._load()

    def transcribe(self, audio: np.ndarray) -> SttResult:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio_s = round(len(audio) / 16000, 2)
        if audio.size == 0:
            return SttResult("", 0.0, audio_s)
        model = self._load()
        kwargs = {
            "beam_size": 5,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "vad_filter": False,
        }
        if self.config.language:
            kwargs["language"] = self.config.language
        if self.config.hotwords:
            kwargs["hotwords"] = self.config.hotwords
        started = time.perf_counter()
        segments, _ = model.transcribe(audio, **kwargs)
        segments = list(segments)
        ms = round((time.perf_counter() - started) * 1000, 1)
        text = "".join(segment.text for segment in segments).strip()
        return SttResult(text, ms, audio_s, len(segments))
