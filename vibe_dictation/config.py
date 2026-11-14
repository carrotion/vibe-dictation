"""Configuration and well-known paths for Vibe Dictation."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "Vibe Dictation"
APP_SLUG = "vibe-dictation"
VERSION = "0.1.0"

APPDATA = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
CONFIG_DIR = APPDATA / APP_SLUG
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_DIR = LOCALAPPDATA / APP_SLUG
STATE_PATH = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
LOG_PATH = LOG_DIR / "app.log"
STARTUP_DIR = APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    input_device: str = ""
    vad_noise_ratio: float = 3.0
    vad_min_rms: float = 0.010
    vad_start_frames: int = 6
    vad_end_silence_ms: int = 1000
    vad_min_speech_ms: int = 250
    vad_preroll_ms: int = 400
    vad_calibrate_ms: int = 350
    vad_max_utterance_s: float = 20.0


@dataclass
class AppConfig:
    model: str = "medium"           # tiny / base / small / medium / large-v3
    device: str = "auto"            # auto / cuda / cpu
    gpu_index: int = 0
    language: str = "ko"            # "" = auto-detect
    hotwords: str = ""              # optional domain words
    input_device: str = ""          # substring; empty = system default
    end_silence_ms: int = 1200      # silence that closes the utterance
    wait_s: float = 8.0             # how long to wait for speech after a press
    transcribe_timeout_s: float = 20.0  # watchdog; falls back to CPU if exceeded
    paste: bool = True              # False = copy to clipboard only
    warmup: bool = True             # preload the model at startup
    hotkey: str = "ctrl+shift+d"    # toggle dictation

    def audio(self) -> AudioConfig:
        return AudioConfig(
            input_device=self.input_device,
            vad_end_silence_ms=max(500, int(self.end_silence_ms)),
            vad_max_utterance_s=20.0,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: value for key, value in data.items() if key in allowed}
        return cls(**values)


DEFAULT_CONFIG = AppConfig()


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | None = None) -> AppConfig:
    ensure_dirs()
    path = path or CONFIG_PATH
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return DEFAULT_CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_CONFIG
    if not isinstance(data, dict):
        return DEFAULT_CONFIG
    config = AppConfig.from_dict(data)
    # Write missing keys back so the file documents every option.
    path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return config
