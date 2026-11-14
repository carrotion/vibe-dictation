"""Energy-based endpoint detection (speech start / end).

Whisper invents text when handed silence, so transcription must only run on audio
that passed speech detection. This detector is deliberately simple: an adaptive
noise floor, hysteresis between the start and end thresholds, a short preroll so
the first syllable is kept, and a trailing-silence trim.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class VadConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    start_ratio: float = 2.5       # speech must be this much above the noise floor
    min_start_rms: float = 0.003   # absolute floor; quiet room measured ~0.001
    start_frames: int = 3          # consecutive frames above threshold before starting
    end_silence_ms: int = 700
    min_speech_ms: int = 250
    preroll_ms: int = 300          # keep audio from before the trigger (first syllable)
    max_utterance_s: float = 20.0
    floor_alpha: float = 0.05      # noise floor EMA per frame (~0.4 s at 20 ms)


class EndpointDetector:
    """Feed fixed-size frames; get start / speech / end / max events."""

    def __init__(self, cfg: VadConfig):
        self.cfg = cfg
        self.frame_len = int(cfg.sample_rate * cfg.frame_ms / 1000)
        self.noise: float | None = None
        self.quiet_floor: float | None = None
        self.in_speech = False
        self.frames: list[np.ndarray] = []
        self.preroll: deque[np.ndarray] = deque(maxlen=max(1, cfg.preroll_ms // cfg.frame_ms))
        self.voiced_frames = 0
        self.silence_frames = 0
        self.ended_by = ""
        self.finished = False
        self.start_hits = 0

    # -- helpers ---------------------------------------------------------
    def _speech_threshold(self) -> float:
        floor = self.noise if self.noise is not None else 0.0
        return max(self.cfg.min_start_rms, floor * self.cfg.start_ratio)

    def _is_quiet(self, rms: float) -> bool:
        # The end threshold follows the ambient level with a ~1 s constant, so a
        # slightly noisy room still counts as silence. It is deliberately lower
        # than the start threshold (hysteresis).
        floor = self.quiet_floor if self.quiet_floor is not None else 0.0
        return rms < max(self.cfg.min_start_rms * 0.5, floor * 1.5)

    # -- main ------------------------------------------------------------
    def update(self, frame: np.ndarray) -> str:
        if self.finished:
            return "end" if self.ended_by == "silence" else "max"
        frame = np.asarray(frame, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0

        if self.quiet_floor is None:
            self.quiet_floor = rms
        elif rms < self._speech_threshold() * 1.2:
            # Only near-ambient frames move the floor; loud speech must not raise it.
            self.quiet_floor = 0.98 * self.quiet_floor + 0.02 * rms

        if not self.in_speech:
            if self.noise is None:
                # Start conservatively: if the very first frame is speech, do not
                # mistake it for the room noise floor (which would hide the start).
                self.noise = min(rms, self.cfg.min_start_rms)
            elif rms < self._speech_threshold():
                self.noise = (1 - self.cfg.floor_alpha) * self.noise + self.cfg.floor_alpha * rms

            self.preroll.append(frame)
            if rms >= self._speech_threshold():
                self.start_hits += 1
                if self.start_hits >= self.cfg.start_frames:
                    self.in_speech = True
                    self.frames = list(self.preroll)
                    self.preroll.clear()
                    self.voiced_frames = self.start_hits
                    self.silence_frames = 0
                    return "start"
            else:
                self.start_hits = 0
            return "silence"

        self.frames.append(frame)
        if self._is_quiet(rms):
            self.silence_frames += 1
        else:
            self.voiced_frames += 1
            # Hangover: a stray click must not restart the whole end timer.
            self.silence_frames = max(0, self.silence_frames - 2)

        if self.silence_frames * self.cfg.frame_ms >= self.cfg.end_silence_ms:
            self.ended_by = "silence"
            self.finished = True
            return "end"
        if len(self.frames) * self.cfg.frame_ms / 1000 >= self.cfg.max_utterance_s:
            self.ended_by = "max_length"
            self.finished = True
            return "max"
        return "speech"

    # -- results ---------------------------------------------------------
    @property
    def voiced_ms(self) -> float:
        return self.voiced_frames * self.cfg.frame_ms

    def audio(self, trim_trailing_silence: bool = True) -> np.ndarray:
        if not self.frames:
            return np.zeros(0, dtype=np.float32)
        frames = self.frames
        if trim_trailing_silence and self.silence_frames:
            frames = frames[: len(frames) - self.silence_frames + 1]
        return np.concatenate(frames)

    def reset(self) -> None:
        self.in_speech = False
        self.frames = []
        self.preroll.clear()
        self.voiced_frames = 0
        self.silence_frames = 0
        self.ended_by = ""
        self.finished = False
        self.start_hits = 0
