"""Thin, defensive wrappers over the macOS Accessibility (AX) C API via pyobjc.

Nothing in here activates an app, raises a window, warps the cursor or posts
events to the global (HID) event stream. Everything is either an AX attribute
read/write, an AX action, or an event posted to one specific pid.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import ApplicationServices as AS
import Quartz
from AppKit import NSRunningApplication, NSWorkspace

ERR_OK = AS.kAXErrorSuccess
ERR_INVALID = AS.kAXErrorInvalidUIElement
ERR_CANNOT_COMPLETE = AS.kAXErrorCannotComplete

AX_TIMEOUT_S = 1.5

_system = AS.AXUIElementCreateSystemWide()
AS.AXUIElementSetMessagingTimeout(_system, AX_TIMEOUT_S)


class AXError(Exception):
    def __init__(self, code: int, what: str):
        super().__init__(f"{what}: AX error {code} ({ERROR_NAMES.get(code, '?')})")
        self.code = code


ERROR_NAMES = {
    AS.kAXErrorSuccess: "success",
    AS.kAXErrorFailure: "failure",
    AS.kAXErrorIllegalArgument: "illegal argument",
    AS.kAXErrorInvalidUIElement: "element no longer exists",
    AS.kAXErrorCannotComplete: "cannot complete (app busy or gone)",
    AS.kAXErrorAttributeUnsupported: "attribute unsupported",
    AS.kAXErrorActionUnsupported: "action unsupported",
    AS.kAXErrorNotImplemented: "not implemented",
    AS.kAXErrorAPIDisabled: "accessibility permission missing",
    AS.kAXErrorNoValue: "no value",
}


def trusted() -> bool:
    return bool(AS.AXIsProcessTrusted())


# ---------------------------------------------------------------- attributes

def get(el, attr: str, default=None):
    err, val = AS.AXUIElementCopyAttributeValue(el, attr, None)
    if err != ERR_OK:
        return default
    return val


def get_strict(el, attr: str):
    err, val = AS.AXUIElementCopyAttributeValue(el, attr, None)
    if err != ERR_OK:
        raise AXError(err, f"read {attr}")
    return val


def get_many(el, attrs: list[str]) -> dict:
    """One IPC round trip for many attributes. Missing ones map to None."""
    err, vals = AS.AXUIElementCopyMultipleAttributeValues(el, attrs, 0, None)
    out = {}
    if err != ERR_OK or vals is None:
        if err == ERR_INVALID:
            raise AXError(err, "read attributes")
        for a in attrs:
            out[a] = get(el, a)
        return out
    for a, v in zip(attrs, vals):
        # Errors come back as AXValue wrapping an AXError; treat as missing.
        if v is not None and AS.AXValueGetTypeID() == Quartz.CFGetTypeID(v):
            if AS.AXValueGetType(v) == AS.kAXValueAXErrorType:
                v = None
        out[a] = v
    return out


def attr_names(el) -> list[str]:
    err, names = AS.AXUIElementCopyAttributeNames(el, None)
    return list(names) if err == ERR_OK and names else []


def action_names(el) -> list[str]:
    err, names = AS.AXUIElementCopyActionNames(el, None)
    return list(names) if err == ERR_OK and names else []


def settable(el, attr: str) -> bool:
    err, ok = AS.AXUIElementIsAttributeSettable(el, attr, None)
    return err == ERR_OK and bool(ok)


def set_attr(el, attr: str, value) -> None:
    err = AS.AXUIElementSetAttributeValue(el, attr, value)
    if err != ERR_OK:
        raise AXError(err, f"set {attr}")


def perform(el, action: str) -> None:
    err = AS.AXUIElementPerformAction(el, action)
    if err != ERR_OK:
        raise AXError(err, f"perform {action}")


def pid_of(el) -> int | None:
    err, pid = AS.AXUIElementGetPid(el, None)
    return pid if err == ERR_OK else None


def same(a, b) -> bool:
    return a is not None and b is not None and bool(Quartz.CFEqual(a, b))


def alive(el) -> bool:
    err, _ = AS.AXUIElementCopyAttributeValue(el, "AXRole", None)
    return err != ERR_INVALID and err != ERR_CANNOT_COMPLETE


def point(v):
    if v is None:
        return None
    ok, p = AS.AXValueGetValue(v, AS.kAXValueCGPointType, None)
    return (round(p.x), round(p.y)) if ok else None


def size(v):
    if v is None:
        return None
    ok, s = AS.AXValueGetValue(v, AS.kAXValueCGSizeType, None)
    return (round(s.width), round(s.height)) if ok else None


def cfrange(v):
    if v is None:
        return None
    ok, r = AS.AXValueGetValue(v, AS.kAXValueCFRangeType, None)
    return (r.location, r.length) if ok else None


def make_range(loc: int, length: int):
    return AS.AXValueCreate(AS.kAXValueCFRangeType, AS.CFRangeMake(loc, length))


# ------------------------------------------------------------------- apps

@dataclass
class App:
    pid: int
    name: str
    bundle_id: str
    el: object


def _live_pids() -> list[int]:
    """Live list of GUI processes from the process manager (GetNextProcess).
    NSWorkspace.runningApplications is a cached snapshot that goes stale in a
    process without a main run loop (e.g. after an app quits and relaunches)."""
    import ctypes
    from .synthfocus import PSN, _AS
    _AS.GetNextProcess.argtypes = [ctypes.POINTER(PSN)]
    _AS.GetProcessPID.argtypes = [ctypes.POINTER(PSN), ctypes.POINTER(ctypes.c_int32)]
    psn = PSN(0, 0)  # kNoProcess
    pids = []
    while _AS.GetNextProcess(ctypes.byref(psn)) == 0:
        pid = ctypes.c_int32()
        if _AS.GetProcessPID(ctypes.byref(psn), ctypes.byref(pid)) == 0:
            pids.append(pid.value)
    return pids


def running_apps() -> list[App]:
    out = []
    for pid in _live_pids():
        ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if ra is None or ra.activationPolicy() != 0:  # regular apps only
            continue
        el = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(el, AX_TIMEOUT_S)
        out.append(App(pid, str(ra.localizedName() or ""), str(ra.bundleIdentifier() or ""), el))
    return out


def find_app(name_or_bundle: str) -> App | None:
    key = name_or_bundle.lower()
    for a in running_apps():
        if a.name.lower() == key or a.bundle_id.lower() == key:
            return a
    return None


def app_is_running(pid: int) -> bool:
    return pid in _live_pids()


def frontmost() -> tuple[int | None, str]:
    """Live frontmost app. Uses AX system-wide element, which (unlike
    NSWorkspace.frontmostApplication in a process without a run loop) is
    never stale."""
    app = get(_system, "AXFocusedApplication")
    if app is None:
        return None, ""
    pid = pid_of(app)
    ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid else None
    return pid, (str(ra.localizedName()) if ra else "")


def mouse_location() -> tuple[float, float]:
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (round(p.x, 1), round(p.y, 1))


def windows(app: App) -> list:
    return list(get(app.el, "AXWindows") or [])


# ------------------------------------------------------- per-pid key events

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "`": 50, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53, "forwarddelete": 117, "home": 115,
    "end": 119, "pageup": 116, "pagedown": 121, "left": 123, "right": 124,
    "down": 125, "up": 126,
}
MODS = {
    "cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift, "opt": Quartz.kCGEventFlagMaskAlternate,
    "option": Quartz.kCGEventFlagMaskAlternate, "alt": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl, "control": Quartz.kCGEventFlagMaskControl,
    "fn": Quartz.kCGEventFlagMaskSecondaryFn,
}


def parse_combo(combo: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combo")
    key = parts[-1]
    flags = 0
    for m in parts[:-1]:
        if m not in MODS:
            raise ValueError(f"unknown modifier {m!r}")
        flags |= MODS[m]
    if key not in KEYCODES:
        raise ValueError(f"unknown key {key!r}; known: {sorted(KEYCODES)}")
    return KEYCODES[key], flags


# A private source state so our synthetic modifiers never combine with the
# modifiers the person is physically holding, and vice versa.
_src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)


def post_key_to_pid(pid: int, combo: str) -> None:
    code, flags = parse_combo(combo)
    down = Quartz.CGEventCreateKeyboardEvent(_src, code, True)
    up = Quartz.CGEventCreateKeyboardEvent(_src, code, False)
    Quartz.CGEventSetFlags(down, flags)
    Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPostToPid(pid, down)
    time.sleep(0.01)
    Quartz.CGEventPostToPid(pid, up)


def post_text_to_pid(pid: int, text: str) -> None:
    for ch in text:
        down = Quartz.CGEventCreateKeyboardEvent(_src, 0, True)
        up = Quartz.CGEventCreateKeyboardEvent(_src, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(down, len(ch), ch)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(ch), ch)
        Quartz.CGEventSetFlags(down, 0)
        Quartz.CGEventSetFlags(up, 0)
        Quartz.CGEventPostToPid(pid, down)
        Quartz.CGEventPostToPid(pid, up)
        time.sleep(0.004)
