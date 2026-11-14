"""Windows clipboard + Ctrl+V helper for dictation."""
from __future__ import annotations

import ctypes
import os
import time


def copy_text(text: str) -> None:
    import pyperclip
    pyperclip.copy(text)


def paste_ctrl_v() -> None:
    if os.name != "nt":
        return
    time.sleep(0.15)  # let the clipboard settle
    user32 = ctypes.windll.user32
    vk_control, vk_v = 0x11, 0x56
    user32.keybd_event(vk_control, 0, 0, 0)
    user32.keybd_event(vk_v, 0, 0, 0)
    user32.keybd_event(vk_v, 0, 2, 0)
    user32.keybd_event(vk_control, 0, 2, 0)


def copy_and_paste(text: str, paste: bool = True) -> None:
    copy_text(text)
    if paste:
        paste_ctrl_v()
