"""Global Windows hotkeys with a dedicated message-loop thread."""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

KEY_NAMES = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "pause": 0x13,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse 'ctrl+shift+d' or 'ctrl+shift+enter' into (modifiers, virtual key)."""
    parts = [part.strip().lower() for part in spec.split("+") if part.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    key = parts[-1]
    mods = 0
    for part in parts[:-1]:
        if part in {"ctrl", "control"}:
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in {"win", "windows"}:
            mods |= MOD_WIN
        else:
            raise ValueError(f"unknown modifier: {part}")
    if key in KEY_NAMES:
        vk = KEY_NAMES[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"unsupported key: {key}")
    return mods | MOD_NOREPEAT, vk


class HotkeyManager:
    """Registers global hotkeys on its own thread and dispatches callbacks there."""

    def __init__(self, bindings: dict[str, tuple[int, int]],
                 callbacks: dict[str, Callable[[], None]],
                 on_error: Callable[[str], None] | None = None):
        self.bindings = bindings
        self.callbacks = callbacks
        self.on_error = on_error
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._ok = False

    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()
        self._ready.wait(timeout)
        return self._ok

    def _fail(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)
        self._ready.set()

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        registered: list[tuple[int, str]] = []
        try:
            for index, (name, (mods, vk)) in enumerate(self.bindings.items(), 1):
                if not user32.RegisterHotKey(None, index, mods, vk):
                    raise RuntimeError(f"hotkey already in use: {name}")
                registered.append((index, name))
            self._ok = True
            self._ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message != WM_HOTKEY:
                    continue
                for index, name in registered:
                    if msg.wParam == index:
                        try:
                            self.callbacks[name]()
                        except Exception as exc:
                            if self.on_error:
                                self.on_error(f"{name}: {exc}")
                        break
        except Exception as exc:
            self._fail(str(exc))
        finally:
            for index, _ in registered:
                user32.UnregisterHotKey(None, index)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)
