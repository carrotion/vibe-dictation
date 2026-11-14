"""Microphone recording and endpoint detection for dictation."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd

from .config import AudioConfig
from .vad import EndpointDetector, VadConfig


@dataclass
class RecordResult:
    audio: np.ndarray | None
    reason: str
    speech_end_perf: float | None
    elapsed_ms: float


def list_devices() -> str:
    lines = []
    for index, device in enumerate(sd.query_devices()):
        kind = []
        if device["max_input_channels"] > 0:
            kind.append(f"in:{device['max_input_channels']}")
        if device["max_output_channels"] > 0:
            kind.append(f"out:{device['max_output_channels']}")
        lines.append(f"{index:>3}  {' '.join(kind):<12} {device['name']}")
    return "\n".join(lines)


def resolve_device(name: str, kind: str) -> int | None:
    if not name:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    matches = [i for i, device in enumerate(sd.query_devices())
               if device[key] > 0 and name.lower() in device["name"].lower()]
    if not matches:
        raise RuntimeError(f"{kind} device not found: {name!r}\n{list_devices()}")
    return matches[0]


def vad_config(config: AudioConfig) -> VadConfig:
    return VadConfig(
        sample_rate=config.sample_rate,
        frame_ms=config.frame_ms,
        start_ratio=config.vad_noise_ratio,
        min_start_rms=config.vad_min_rms,
        start_frames=config.vad_start_frames,
        end_silence_ms=config.vad_end_silence_ms,
        min_speech_ms=config.vad_min_speech_ms,
        preroll_ms=config.vad_preroll_ms,
        max_utterance_s=config.vad_max_utterance_s,
    )


def record_utterance(config: AudioConfig, max_wait_s: float = 10.0,
                     on_frame: Callable[[float, str, float], None] | None = None,
                     should_stop: Callable[[], bool] | None = None) -> RecordResult:
    detector = EndpointDetector(vad_config(config))
    device = resolve_device(config.input_device, "input")
    frame_len = detector.frame_len
    started = False
    begin = time.perf_counter()

    with sd.InputStream(samplerate=config.sample_rate, channels=1, dtype="float32",
                        blocksize=frame_len, device=device) as stream:
        while True:
            data, _ = stream.read(frame_len)
            frame = data[:, 0]
            event = detector.update(frame)
            now = time.perf_counter()
            if should_stop is not None and should_stop():
                return RecordResult(None, "cancelled", None, round((now - begin) * 1000, 1))
            if on_frame is not None:
                try:
                    rms = float(np.sqrt(np.mean(frame * frame)))
                    on_frame(rms, event, now - begin)
                except Exception:
                    pass

            if event == "start":
                started = True
            elif event in ("end", "max"):
                elapsed = (now - begin) * 1000
                if detector.voiced_ms < config.vad_min_speech_ms:
                    return RecordResult(None, "too_short", None, round(elapsed, 1))
                speech_end = now - (config.vad_end_silence_ms / 1000 if event == "end" else 0)
                return RecordResult(detector.audio(), "ok", speech_end, round(elapsed, 1))
            elif not started and now - begin > max_wait_s:
                return RecordResult(None, "no_speech", None, round((now - begin) * 1000, 1))
