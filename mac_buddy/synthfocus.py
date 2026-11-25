"""Synthetic focus: make a window key inside its own (background) process.

AppKit only validates menu items, routes key events and runs Save/Close
against the *key* window, and a background app has none. Pressing File > Save
through AX in a background app therefore "succeeds" and does nothing.

The WindowServer exposes a per-process event channel (SkyLight's
SLPSPostEventRecordTo). We post two records **to the target process only**:

  1. an "application activated" record, so AppKit in that process behaves as
     if it were active (menus validate, key window is honoured), and
  2. a "make key window" pair for the chosen window id.

We never call SetFrontProcess / activateWithOptions, and we never post a
"deactivate" record to the app the person is using, so the WindowServer's
notion of the front app -- which decides where the person's real keystrokes
go -- does not change. The same records are what tiling window managers use
for "focus without raise".

This is a private API. If it is missing or fails, we report that and the
caller must not pretend an action that needs a key window succeeded.
"""
from __future__ import annotations

import ctypes
import ctypes.util

_AS = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
try:
    _SL = ctypes.cdll.LoadLibrary(
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
except OSError:  # pragma: no cover
    _SL = None


class PSN(ctypes.Structure):
    _fields_ = [("high", ctypes.c_uint32), ("low", ctypes.c_uint32)]


_AS._AXUIElementGetWindow.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_AS._AXUIElementGetWindow.restype = ctypes.c_int32
_AS.GetProcessForPID.argtypes = [ctypes.c_int32, ctypes.POINTER(PSN)]
_AS.GetProcessForPID.restype = ctypes.c_int32
if _SL is not None:
    _SL.SLPSPostEventRecordTo.argtypes = [ctypes.POINTER(PSN), ctypes.c_char_p]
    _SL.SLPSPostEventRecordTo.restype = ctypes.c_int32


def available() -> bool:
    return _SL is not None and hasattr(_SL, "SLPSPostEventRecordTo")


def window_id(ax_window) -> int | None:
    wid = ctypes.c_uint32(0)
    ptr = ax_window.__c_void_p__()
    err = _AS._AXUIElementGetWindow(ptr, ctypes.byref(wid))
    return wid.value if err == 0 and wid.value else None


def _psn(pid: int) -> PSN | None:
    psn = PSN()
    return psn if _AS.GetProcessForPID(pid, ctypes.byref(psn)) == 0 else None


def _record(kind: int, wid: int, extra: dict[int, int] | None = None) -> bytes:
    b = bytearray(0xF8)
    b[0x04] = 0xF8
    b[0x08] = kind
    for off, val in (extra or {}).items():
        b[off] = val
    b[0x3C:0x40] = wid.to_bytes(4, "little")
    return bytes(b)


def key_target(ax_window):
    """A window with a sheet routes keys to the sheet (and a sheet's sheet to
    that one), so the deepest sheet is what must become key."""
    import ApplicationServices as AS
    el = ax_window
    for _ in range(4):
        err, kids = AS.AXUIElementCopyAttributeValue(el, "AXChildren", None)
        sheet = None
        for k in (kids or []) if err == 0 else []:
            e2, role = AS.AXUIElementCopyAttributeValue(k, "AXRole", None)
            if e2 == 0 and role == "AXSheet":
                sheet = k
        if sheet is None:
            break
        el = sheet
    return el


def focus_window(pid: int, ax_window) -> tuple[bool, str]:
    """Make ax_window (or its frontmost sheet) the key window of process pid
    without activating it."""
    if not available():
        return False, "SkyLight SLPSPostEventRecordTo unavailable"
    wid = window_id(key_target(ax_window)) or window_id(ax_window)
    if wid is None:
        return False, "could not resolve CGWindowID for window"
    psn = _psn(pid)
    if psn is None:
        return False, f"no process serial number for pid {pid}"
    # 0x0d / [0x8a]=1 : "you are now the active app" (sent to this pid only)
    activate = _record(0x0D, wid, {0x8A: 0x01})
    # 0x01 / 0x02 with [0x3a]=0x10 : make-key-window down/up
    key_down = bytearray(_record(0x01, wid, {0x3A: 0x10}))
    key_up = bytearray(_record(0x02, wid, {0x3A: 0x10}))
    key_down[0x20:0x30] = b"\xff" * 0x10
    key_up[0x20:0x30] = b"\xff" * 0x10
    for rec in (activate, bytes(key_down), bytes(key_up)):
        err = _SL.SLPSPostEventRecordTo(ctypes.byref(psn), rec)
        if err != 0:
            return False, f"SLPSPostEventRecordTo error {err}"
    return True, f"window {wid} made key in pid {pid}"
