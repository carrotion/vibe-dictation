"""The shared state file."""
from __future__ import annotations

import json
from pathlib import Path

from vibe_dictation.state import DictationState, read_state


def test_initial_state(tmp_path: Path) -> None:
    state = DictationState(tmp_path / "state.json")
    snapshot = state.snapshot()
    assert snapshot["active"] is False
    assert snapshot["status"] == "idle"
    assert "text" not in snapshot  # transcripts are never written to disk


def test_set_persists_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = DictationState(path)
    state.set(active=True, status="listening")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active"] is True
    assert data["status"] == "listening"
    assert read_state(path)["status"] == "listening"


def test_updated_at_changes(tmp_path: Path) -> None:
    state = DictationState(tmp_path / "state.json")
    first = state.snapshot()["updated_at"]
    state.set(status="transcribing")
    assert state.snapshot()["updated_at"] >= first


def test_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "state.json"
    DictationState(path)
    assert path.exists()


def test_level_rounds_values(tmp_path: Path) -> None:
    state = DictationState(tmp_path / "state.json")
    state.level(0.123456, 1.23456)
    snapshot = state.snapshot()
    assert snapshot["level"] == 0.12346
    assert snapshot["elapsed_s"] == 1.23


def test_read_state_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_state(tmp_path / "missing.json") == {}


def test_read_state_corrupt_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{", encoding="utf-8")
    assert read_state(path) == {}


def test_snapshot_is_a_copy(tmp_path: Path) -> None:
    state = DictationState(tmp_path / "state.json")
    snapshot = state.snapshot()
    snapshot["status"] = "mutated"
    assert state.snapshot()["status"] == "idle"
