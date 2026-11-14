"""Hotkey spec parsing."""
from __future__ import annotations

import pytest

from vibe_dictation.hotkeys import (KEY_NAMES, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT,
                                    MOD_SHIFT, MOD_WIN, parse_hotkey)


def test_letter_hotkey() -> None:
    assert parse_hotkey("ctrl+shift+d") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("D"))


def test_named_key() -> None:
    mods, vk = parse_hotkey("ctrl+shift+enter")
    assert vk == KEY_NAMES["enter"]
    assert mods & MOD_CONTROL and mods & MOD_SHIFT


def test_backspace_hotkey() -> None:
    assert parse_hotkey("ctrl+shift+backspace")[1] == 0x08


def test_all_modifiers() -> None:
    mods, _ = parse_hotkey("ctrl+alt+shift+win+f")
    assert mods & MOD_CONTROL
    assert mods & MOD_ALT
    assert mods & MOD_SHIFT
    assert mods & MOD_WIN


def test_case_and_spacing_are_tolerated() -> None:
    assert parse_hotkey(" Ctrl + Shift + D ") == parse_hotkey("ctrl+shift+d")


def test_modifier_aliases() -> None:
    assert parse_hotkey("control+shift+d") == parse_hotkey("ctrl+shift+d")
    assert parse_hotkey("windows+shift+d") == parse_hotkey("win+shift+d")


@pytest.mark.parametrize("spec", ["", "ctrl+", "hyper+d", "ctrl+f13", "ctrl+shift+"])
def test_bad_specs_raise(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(spec)
