"""Vibe Dictation tray app (Windows, user session).

Press the hotkey, speak one utterance, and the text is pasted at the cursor.
Runs from the Startup folder (scripts/install.ps1). No console window; logs to
%LOCALAPPDATA%\\vibe-dictation\\logs\\app.log.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pystray
from PIL import Image
from pystray import Menu, MenuItem

from .config import (APP_NAME, CONFIG_PATH, LOG_DIR, LOG_PATH, STARTUP_DIR,
                     AppConfig, load_config)
from .hotkeys import HotkeyManager, parse_hotkey
from .overlay import StatusOverlay
from .service import DictationService

APP_ID = "VibeDictationTray"
ICON_DIR = Path(__file__).resolve().parent / "assets"
logger = logging.getLogger("vibe_dictation")

STATUS_LABELS = {
    "idle": "idle",
    "listening": "listening",
    "transcribing": "transcribing",
    "pasted": "pasted",
    "error": "error",
}


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def hook(exc_type, exc, tb):
        logger.error("uncaught", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook


def _parse_or_default(spec: str, default: str) -> tuple[int, int]:
    try:
        return parse_hotkey(spec)
    except Exception as exc:
        logger.warning("bad hotkey %r, using %s: %s", spec, default, exc)
        return parse_hotkey(default)


def build_bindings(config: AppConfig) -> dict[str, tuple[int, int]]:
    return {"toggle": _parse_or_default(config.hotkey, "ctrl+shift+d")}


def main() -> int:
    setup_logging()
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Local\\VibeDictationTray")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        logger.info("another tray instance is running")
        return 0

    config = load_config()
    logger.info("starting %s model=%s device=%s", APP_NAME, config.model, config.device)

    idle = Image.open(ICON_DIR / "icon_idle.png")
    active = Image.open(ICON_DIR / "icon_active.png")
    icon_ref: dict[str, pystray.Icon] = {}
    overlay = StatusOverlay()

    def update(snapshot: dict) -> None:
        overlay.update(snapshot)
        icon = icon_ref.get("icon")
        if icon is None:
            return
        is_active = bool(snapshot.get("active"))
        status = str(snapshot.get("status") or "idle")
        icon.icon = active if is_active else idle
        icon.title = f"{APP_NAME} — {STATUS_LABELS.get(status, status)}"
        try:
            icon.update_menu()
        except Exception:
            pass

    service = DictationService(config, on_change=update)

    def open_config() -> None:
        os.startfile(CONFIG_PATH)  # noqa: S606

    def open_log() -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_PATH)  # noqa: S606

    def open_startup() -> None:
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(STARTUP_DIR)  # noqa: S606

    def quit_app(icon: pystray.Icon, item=None) -> None:
        logger.info("quitting")
        icon.stop()

    def status_text(item) -> str:
        snap = service.snapshot()
        label = STATUS_LABELS.get(str(snap.get("status")), str(snap.get("status")))
        return f"Status: {label}"

    menu = Menu(
        MenuItem(status_text, lambda icon, item: None, enabled=False),
        MenuItem(lambda item: "Cancel" if service.active.is_set() else "Start dictation",
                 lambda icon, item: service.toggle(), default=True),
        Menu.SEPARATOR,
        MenuItem("Open config", lambda icon, item: open_config()),
        MenuItem("Open log", lambda icon, item: open_log()),
        MenuItem("Startup folder", lambda icon, item: open_startup()),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon(APP_ID, idle, APP_NAME, menu)
    icon_ref["icon"] = icon
    hotkeys_ref: dict[str, HotkeyManager] = {}

    def setup(icon: pystray.Icon) -> None:
        icon.visible = True
        service.start_engine()
        hotkeys = HotkeyManager(
            build_bindings(config),
            {"toggle": service.toggle},
            on_error=lambda message: logger.error("hotkey: %s", message),
        )
        if not hotkeys.start():
            logger.error("hotkey registration failed; use the tray menu")
        hotkeys_ref["manager"] = hotkeys
        if config.warmup:
            threading.Thread(target=_warmup, args=(service,), daemon=True,
                             name="dictation-warmup").start()
        logger.info("tray ready")

    try:
        icon.run(setup=setup)
    finally:
        hotkeys = hotkeys_ref.get("manager")
        if hotkeys is not None:
            hotkeys.stop()
        service.shutdown()
        overlay.close()
        kernel32.CloseHandle(mutex)
    return 0


def _warmup(service: DictationService) -> None:
    try:
        service.warmup()
        logger.info("STT model warmed")
    except Exception:
        logger.exception("model warmup failed")


if __name__ == "__main__":
    raise SystemExit(main())
