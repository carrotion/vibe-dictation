"""Native Win32 status HUD that never takes focus.

A single WS_POPUP window with WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW and
WS_EX_TRANSPARENT: it is never activated, never appears in Alt+Tab, and lets
mouse clicks pass straight through to whatever is underneath. It shows
"Listening / Transcribing / Pasted / Error" plus a small input-level bar, then
hides itself. No toolkit, no Tcl, no focus change.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Window styles / messages
WS_POPUP = 0x8000_0000
WS_EX_TOPMOST = 0x0000_0008
WS_EX_TRANSPARENT = 0x0000_0020
WS_EX_TOOLWINDOW = 0x0000_0080
WS_EX_LAYERED = 0x0008_0000
WS_EX_NOACTIVATE = 0x0800_0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_TIMER = 0x0113
WM_MOUSEACTIVATE = 0x0021
WM_APP_UPDATE = 0x8001
MA_NOACTIVATE = 3
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
DT_LEFT = 0x0000
DT_SINGLELINE = 0x0020
DT_VCENTER = 0x0004
DT_NOPREFIX = 0x0800
LWA_ALPHA = 0x0000_0002
TRANSPARENT = 1

WIDTH = 248
HEIGHT = 58
MARGIN_BOTTOM = 92
PAD = 16
TIMER_ID = 1
HIDE_AFTER_MS = 900
ERROR_MS = 2600

BG = 0x2A170F          # COLORREF (0x00BBGGRR) for #0f172a
BAR_BG = 0x3B291E      # #1e293b
TEXT = 0xF0E8E2        # #e2e8f0
MUTED = 0xA8A194       # #94a3b8
ACCENT = 0x3C92FB      # #fb923c
INFO = 0xFAA560        # #60a5fa
OK = 0x99D334          # #34d399
ERR = 0x7171F8         # #f87171

STATUS_TEXT = {
    "listening": ("Listening", ACCENT),
    "transcribing": ("Transcribing", INFO),
    "pasted": ("Pasted", OK),
    "error": ("Error", ERR),
    "idle": ("Idle", MUTED),
}

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


def _enable_dpi_awareness() -> None:
    """Physical pixels everywhere, so the HUD lands inside the screen at any scale."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _protocols() -> None:
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.WORD
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.LPVOID]
    user32.KillTimer.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = wintypes.HDC
    user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                  ctypes.c_byte, wintypes.DWORD]
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                wintypes.HBRUSH]
    user32.FillRect.restype = ctypes.c_int
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
    user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                 ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetDpiForSystem.restype = wintypes.UINT
    gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int]
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class StatusOverlay:
    """Public API: update(snapshot) and close()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._closed = False
        self._hwnd = None
        _enable_dpi_awareness()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="status-overlay")
        self._thread.start()

    # -- public ----------------------------------------------------------
    def update(self, snapshot: dict) -> None:
        with self._lock:
            if self._closed:
                return
            self._pending = dict(snapshot)
            hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_UPDATE, 0, 0)

    def close(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._closed = True
            hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout)

    # -- window thread ---------------------------------------------------
    def _run(self) -> None:
        _protocols()
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "VibeDictationHud"
        wndproc = WNDPROC(self._wnd_proc)
        wc = WNDCLASSW(
            style=0,
            lpfnWndProc=wndproc,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=hinst,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=class_name,
        )
        if not user32.RegisterClassW(ctypes.byref(wc)):
            error = ctypes.get_last_error()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                logger.error("HUD: RegisterClassW failed (%s)", error)
                return

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        try:
            dpi = int(user32.GetDpiForSystem() or 96)
        except Exception:
            dpi = 96
        scale = max(1.0, dpi / 96.0)
        self._scale = scale
        self._w = round(WIDTH * scale)
        self._h = round(HEIGHT * scale)
        self._pad = round(PAD * scale)
        margin = round(MARGIN_BOTTOM * scale)
        x = max(0, (screen_w - self._w) // 2)
        y = max(0, screen_h - self._h - margin)

        ex_style = (WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                    | WS_EX_LAYERED | WS_EX_NOACTIVATE)
        self._x, self._y = x, y
        self._font = gdi32.CreateFontW(
            -round(15 * scale), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI",
        )
        hwnd = user32.CreateWindowExW(
            ex_style, class_name, "Vibe Dictation", WS_POPUP,
            x, y, self._w, self._h, None, None, hinst, None,
        )
        if not hwnd:
            logger.error("HUD: CreateWindowExW failed (%s)", ctypes.get_last_error())
            return
        with self._lock:
            self._hwnd = hwnd
        user32.SetLayeredWindowAttributes(hwnd, 0, 235, LWA_ALPHA)
        radius = round(14 * scale)
        rgn = gdi32.CreateRoundRectRgn(0, 0, self._w + 1, self._h + 1, radius, radius)
        user32.SetWindowRgn(hwnd, rgn, True)
        self._apply(hwnd)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        with self._lock:
            self._hwnd = None

    def _apply(self, hwnd) -> None:
        with self._lock:
            snapshot = self._pending
        if not snapshot:
            return
        status = str(snapshot.get("status") or "idle")
        self._level = min(1.0, float(snapshot.get("level") or 0.0) / 0.08)
        if status in {"idle", "cleared"}:
            self._hide(hwnd)
            return
        self._status = status
        self._hide_at = 0.0
        if status == "pasted":
            self._hide_at = time.monotonic() + HIDE_AFTER_MS / 1000
        elif status == "error":
            self._hide_at = time.monotonic() + ERROR_MS / 1000
        self._show(hwnd)
        if self._hide_at:
            user32.SetTimer(hwnd, TIMER_ID, 60, None)
        else:
            user32.KillTimer(hwnd, TIMER_ID)
        user32.InvalidateRect(hwnd, None, True)

    def _show(self, hwnd) -> None:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, self._x, self._y, self._w, self._h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _hide(self, hwnd) -> None:
        user32.KillTimer(hwnd, TIMER_ID)
        user32.ShowWindow(hwnd, SW_HIDE)

    def _paint(self, hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        w, h = self._w, self._h
        pad, s = self._pad, self._scale
        try:
            rect = wintypes.RECT(0, 0, w, h)
            bg = gdi32.CreateSolidBrush(BG)
            user32.FillRect(hdc, ctypes.byref(rect), bg)
            gdi32.DeleteObject(bg)

            status = getattr(self, "_status", "")
            text, color = STATUS_TEXT.get(status, (status.title() or "Idle", MUTED))

            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, color)
            old = gdi32.SelectObject(hdc, self._font)
            text_rect = wintypes.RECT(pad, round(8 * s), w - pad, round(36 * s))
            user32.DrawTextW(hdc, f"\u25cf  {text}", -1, ctypes.byref(text_rect),
                             DT_LEFT | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX)
            gdi32.SelectObject(hdc, old)

            bar_top = h - round(16 * s)
            bar_bottom = h - round(12 * s)
            bar_rect = wintypes.RECT(pad, bar_top, w - pad, bar_bottom)
            bar_bg = gdi32.CreateSolidBrush(BAR_BG)
            user32.FillRect(hdc, ctypes.byref(bar_rect), bar_bg)
            gdi32.DeleteObject(bar_bg)

            if status == "listening":
                width = int((w - 2 * pad) * getattr(self, "_level", 0.0))
                if width > 0:
                    fill_rect = wintypes.RECT(pad, bar_top, pad + width, bar_bottom)
                    fill = gdi32.CreateSolidBrush(color)
                    user32.FillRect(hdc, ctypes.byref(fill_rect), fill)
                    gdi32.DeleteObject(fill)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_UPDATE:
            self._apply(hwnd)
            return 0
        if msg == WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == WM_TIMER:
            hide_at = getattr(self, "_hide_at", 0.0)
            if hide_at and time.monotonic() >= hide_at:
                self._hide(hwnd)
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
