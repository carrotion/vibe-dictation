"""Config defaults and round-trip behaviour."""
from __future__ import annotations

import json
from pathlib import Path

from vibe_dictation.config import AppConfig, load_config


def test_defaults_are_stable() -> None:
    config = AppConfig()
    assert config.model == "medium"
    assert config.device == "auto"
    assert config.paste is True
    assert config.hotkey == "ctrl+shift+d"


def test_round_trip_keeps_values() -> None:
    original = AppConfig(model="small", device="cpu", end_silence_ms=800, paste=False)
    restored = AppConfig.from_dict(original.to_dict())
    assert restored == original


def test_unknown_keys_are_ignored() -> None:
    config = AppConfig.from_dict({"model": "base", "not_a_real_option": 1})
    assert config.model == "base"
    assert not hasattr(config, "not_a_real_option")


def test_audio_view_tracks_end_silence() -> None:
    config = AppConfig(end_silence_ms=900, input_device="USB Mic")
    audio = config.audio()
    assert audio.vad_end_silence_ms == 900
    assert audio.input_device == "USB Mic"


def test_low_end_silence_is_clamped() -> None:
    assert AppConfig(end_silence_ms=10).audio().vad_end_silence_ms == 500


def test_load_config_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = load_config(path)
    assert config == AppConfig()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["model"] == "medium"
    assert written["hotkey"] == "ctrl+shift+d"


def test_load_config_fills_missing_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "tiny"}), encoding="utf-8")
    config = load_config(path)
    assert config.model == "tiny"
    assert config.language == "ko"
    assert json.loads(path.read_text(encoding="utf-8"))["paste"] is True


def test_load_config_survives_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == AppConfig()
