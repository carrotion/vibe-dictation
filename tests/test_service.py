"""One-utterance-per-press pipeline, with audio and STT stubbed out."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vibe_dictation import service as service_module
from vibe_dictation.config import AppConfig
from vibe_dictation.service import DictationService
from vibe_dictation.state import DictationState

AUDIO = np.zeros(1600, dtype=np.float32)


class FakeResult:
    def __init__(self, text: str):
        self.text = text


@pytest.fixture()
def pasted(monkeypatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    def fake_copy_and_paste(text: str, paste: bool = True) -> None:
        calls.append((text, paste))

    monkeypatch.setattr(service_module, "copy_and_paste", fake_copy_and_paste)
    return calls


@pytest.fixture()
def service(tmp_path: Path) -> DictationService:
    return DictationService(AppConfig(paste=False), state=DictationState(tmp_path / "state.json"))


def stub_worker(monkeypatch, service: DictationService, reason: str = "ok",
                text: str = "받아쓴 문장", raises: bool = False,
                cancels: bool = False) -> None:
    """Make one recording pass and shut the worker down afterwards."""
    from vibe_dictation.audio import RecordResult

    def fake_record(*_args, **_kwargs):
        if cancels:
            service.active.clear()
        service.shutdown_event.set()
        if reason == "ok":
            return RecordResult(AUDIO, "ok", None, 100.0)
        return RecordResult(None, reason, None, 100.0)

    class FakeStt:
        def transcribe(self, audio):
            if raises:
                raise RuntimeError("model exploded")
            return FakeResult(text)

    monkeypatch.setattr(service_module, "record_utterance", fake_record)
    monkeypatch.setattr(service, "_stt", FakeStt())


def snapshot(service: DictationService) -> dict:
    return service.snapshot()


def test_toggle_on_starts_listening(service: DictationService) -> None:
    assert service.toggle() is True
    assert service.active.is_set()
    assert snapshot(service)["active"] is True
    assert snapshot(service)["status"] == "listening"


def test_toggle_off_cancels(service: DictationService) -> None:
    service.toggle()
    assert service.toggle() is False
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "idle"


def test_stop_clears_active(service: DictationService) -> None:
    service.start()
    service.stop()
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "idle"


def test_worker_pastes_and_goes_idle(monkeypatch, service: DictationService, pasted) -> None:
    stub_worker(monkeypatch, service)
    service.start()
    service._worker()
    assert pasted == [("받아쓴 문장", False)]
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "pasted"


def test_worker_trims_transcript(monkeypatch, service: DictationService, pasted) -> None:
    stub_worker(monkeypatch, service, text="  공백 정리  ")
    service.start()
    service._worker()
    assert pasted == [("공백 정리", False)]


def test_no_speech_turns_off_without_pasting(monkeypatch, service: DictationService,
                                             pasted) -> None:
    stub_worker(monkeypatch, service, reason="no_speech")
    service.start()
    service._worker()
    assert pasted == []
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "idle"


def test_cancelled_recording_leaves_idle(monkeypatch, service: DictationService, pasted) -> None:
    stub_worker(monkeypatch, service, reason="cancelled", cancels=True)
    service.start()
    service._worker()  # the fake recording clears active, like a second hotkey press
    assert pasted == []
    assert not service.active.is_set()


def test_empty_transcript_turns_off(monkeypatch, service: DictationService, pasted) -> None:
    stub_worker(monkeypatch, service, text="   ")
    service.start()
    service._worker()
    assert pasted == []
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "idle"


def test_stt_error_is_reported(monkeypatch, service: DictationService, pasted) -> None:
    stub_worker(monkeypatch, service, raises=True)
    service.start()
    service._worker()
    assert pasted == []
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "error"
    assert "model exploded" in snapshot(service)["last_error"]


def test_clipboard_error_is_reported(monkeypatch, service: DictationService) -> None:
    def boom(text: str, paste: bool = True) -> None:
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(service_module, "copy_and_paste", boom)
    stub_worker(monkeypatch, service)
    service.start()
    service._worker()
    assert snapshot(service)["status"] == "error"
    assert "clipboard busy" in snapshot(service)["last_error"]


def test_start_is_idempotent(service: DictationService) -> None:
    assert service.start() is True
    service.state.set(status="transcribing")
    assert service.start() is True
    assert snapshot(service)["status"] == "transcribing"


def test_on_change_receives_snapshots(tmp_path: Path) -> None:
    seen: list[dict] = []
    service = DictationService(AppConfig(), on_change=seen.append,
                               state=DictationState(tmp_path / "state.json"))
    service.start()
    assert seen
    assert seen[-1]["active"] is True


def test_callback_exception_does_not_break_service(tmp_path: Path) -> None:
    def boom(_snapshot: dict) -> None:
        raise RuntimeError("callback failed")

    service = DictationService(AppConfig(), on_change=boom,
                               state=DictationState(tmp_path / "state.json"))
    service.start()
    assert service.active.is_set()


def test_shutdown_resets_state(service: DictationService) -> None:
    service.start()
    service.shutdown()
    assert service.shutdown_event.is_set()
    assert not service.active.is_set()
    assert snapshot(service)["status"] == "idle"


def test_start_engine_runs_a_single_worker(service: DictationService) -> None:
    service.start_engine()
    first = service._worker_thread
    service.start_engine()
    assert service._worker_thread is first
    assert first is not None and first.daemon
    service.shutdown()
