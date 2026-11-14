"""Shared state for the tray app and any status preview."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import STATE_PATH


class DictationState:
    """A tiny JSON status file. Never stores transcript text."""

    def __init__(self, path: Path | str = STATE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_level = 0.0
        self._state: dict[str, Any] = {
            "active": False,
            "status": "idle",
            "level": 0.0,
            "elapsed_s": 0.0,
            "last_error": "",
            "updated_at": time.time(),
        }
        self._write()

    def set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)
            self._state["updated_at"] = time.time()
            self._write()

    def level(self, rms: float, elapsed_s: float) -> None:
        now = time.monotonic()
        if now - self._last_level < 0.06:
            return
        self._last_level = now
        self.set(level=round(float(rms), 5), elapsed_s=round(float(elapsed_s), 2))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _write(self) -> None:
        data = json.dumps(self._state, ensure_ascii=False, separators=(",", ":"))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            try:
                self.path.write_text(data, encoding="utf-8")
            except OSError:
                pass


def read_state(path: Path | str = STATE_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
