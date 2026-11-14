"""Energy-based endpoint detection (speech start / end).

Whisper invents text when handed silence, so transcription must only run on audio
that passed speech detection. The detector uses a calibrated noise floor, a
hysteresis gap between the start and end thresholds, a preroll so the first
syllable is kept, and a trailing-silence trim.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class VadConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    start_ratio: float = 3.0       # speech must be this much above the noise floor
    min_start_rms: float = 0.010   # absolute floor for very quiet rooms
    start_frames: int = 6          # consecutive frames above threshold (~120 ms)
    end_silence_ms: int = 1000
    min_speech_ms: int = 250
    preroll_ms: int = 400          # keep audio from before the trigger
    max_utterance_s: float = 20.0
    floor_alpha: float = 0.02      # slow noise-floor tracking while not speaking


class EndpointDetector:
    """Feed fixed-size frames; get start / speech / end / max events."""

    def __init__(self, cfg: VadConfig, noise: float | None = None,
                 preroll: list[np.ndarray] | None = None):
        self.cfg = cfg
        self.frame_len = int(cfg.sample_rate * cfg.frame_ms / 1000)
        # `noise` comes from a short calibration at recording start; without it
        # the first frame is used as a rough guess.
        self.noise: float | None = noise
        self.in_speech = False
        self.frames: list[np.ndarray] = []
        self.preroll: deque[np.ndarray] = deque(maxlen=max(1, cfg.preroll_ms // cfg.frame_ms))
        if preroll:
            for frame in preroll:
                self.preroll.append(np.asarray(frame, dtype=np.float32).reshape(-1))
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
        # Hysteresis: the end threshold sits below the start threshold so a
        # slightly noisy room still counts as silence between phrases.
        floor = self.noise if self.noise is not None else 0.0
        return rms < max(self.cfg.min_start_rms * 0.7, floor * 1.7)

    # -- main ------------------------------------------------------------
    def update(self, frame: np.ndarray) -> str:
        if self.finished:
            return "end" if self.ended_by == "silence" else "max"
        frame = np.asarray(frame, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0

        if not self.in_speech:
            if self.noise is None:
                # Start conservatively: if the very first frame is speech, do not
                # mistake it for the room noise floor (which would hide the start).
                self.noise = min(rms, self.cfg.min_start_rms)
            elif rms < self._speech_threshold() * 0.9:
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

    @property
    def recorded_ms(self) -> float:
        return len(self.frames) * self.cfg.frame_ms

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
