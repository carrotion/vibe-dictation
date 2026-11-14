"""The endpoint detector state machine, driven by synthetic frames."""
from __future__ import annotations

import numpy as np
import pytest

from vibe_dictation.vad import EndpointDetector, VadConfig

SAMPLE_RATE = 16_000
FRAME_LEN = 320  # 20 ms


def quiet_frame(level: float = 0.0005) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(FRAME_LEN) * level).astype(np.float32)


def loud_frame(level: float = 0.15) -> np.ndarray:
    t = np.arange(FRAME_LEN) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 220 * t) * level).astype(np.float32)


def config(**overrides) -> VadConfig:
    base = VadConfig(end_silence_ms=100, min_speech_ms=100, start_frames=3, preroll_ms=60)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def feed(detector: EndpointDetector, frames) -> list[str]:
    return [detector.update(frame) for frame in frames]


def test_silence_only_never_starts() -> None:
    detector = EndpointDetector(config())
    events = feed(detector, [quiet_frame() for _ in range(60)])
    assert "start" not in events
    assert not detector.in_speech


def test_speech_then_silence_ends() -> None:
    detector = EndpointDetector(config())
    feed(detector, [quiet_frame() for _ in range(20)])
    events = feed(detector, [loud_frame() for _ in range(15)])
    assert "start" in events
    assert detector.in_speech

    events = feed(detector, [quiet_frame() for _ in range(20)])
    assert "end" in events
    assert detector.ended_by == "silence"
    assert detector.voiced_ms >= 100


def test_preroll_is_kept() -> None:
    detector = EndpointDetector(config(preroll_ms=100))
    feed(detector, [quiet_frame() for _ in range(20)])
    feed(detector, [loud_frame() for _ in range(5)])
    # preroll (5 frames) + the frame that triggered the start
    assert len(detector.frames) >= 5


def test_long_speech_ends_at_max_length() -> None:
    detector = EndpointDetector(config(max_utterance_s=0.2, end_silence_ms=10_000))
    feed(detector, [quiet_frame() for _ in range(10)])
    events = feed(detector, [loud_frame() for _ in range(40)])
    assert "max" in events
    assert detector.ended_by == "max_length"


def test_trim_removes_trailing_silence() -> None:
    detector = EndpointDetector(config())
    feed(detector, [quiet_frame() for _ in range(20)])
    feed(detector, [loud_frame() for _ in range(10)])
    feed(detector, [quiet_frame() for _ in range(8)])
    trimmed = detector.audio(trim_trailing_silence=True)
    untrimmed = detector.audio(trim_trailing_silence=False)
    assert 0 < trimmed.size < untrimmed.size


def test_reset_restores_initial_state() -> None:
    detector = EndpointDetector(config())
    feed(detector, [quiet_frame() for _ in range(10)])
    feed(detector, [loud_frame() for _ in range(10)])
    detector.reset()
    assert not detector.in_speech
    assert not detector.finished
    assert detector.frames == []
    assert detector.voiced_frames == 0


def test_empty_frame_is_ignored_safely() -> None:
    detector = EndpointDetector(config())
    assert detector.update(np.zeros(0, dtype=np.float32)) == "silence"


def test_audio_returns_float32() -> None:
    detector = EndpointDetector(config())
    feed(detector, [quiet_frame() for _ in range(5)])
    feed(detector, [loud_frame() for _ in range(5)])
    audio = detector.audio()
    assert audio.dtype == np.float32
    assert audio.ndim == 1


@pytest.mark.parametrize("silence_ms", [60, 200])
def test_end_silence_threshold_is_respected(silence_ms: int) -> None:
    detector = EndpointDetector(config(end_silence_ms=silence_ms))
    feed(detector, [quiet_frame() for _ in range(10)])
    feed(detector, [loud_frame() for _ in range(10)])
    needed = silence_ms // 20 + 2
    events = feed(detector, [quiet_frame() for _ in range(needed)])
    assert "end" in events
