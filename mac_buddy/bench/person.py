"""A simulated person: keeps an app in front and types into it with real
(HID-level) key events, the same path as a physical keyboard.

Safety: before every character it checks that its own app is still the
front app. If not, it stops typing at once and records a focus loss, so it
can never type into whatever app ended up in front.
"""
from __future__ import annotations

import subprocess
import threading
import time

import Quartz

from .. import ax
from ..focus import front_app

_src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)


_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
            "(": "9", ")": "0", "_": "-", "+": "=", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/"}


def _hid_char(ch: str):
    """Type one character with its real key code, like a keyboard would."""
    if ch == " ":
        key, shift = "space", False
    elif ch == "\n":
        key, shift = "return", False
    elif ch.isalpha() and ch.isupper():
        key, shift = ch.lower(), True
    elif ch in _SHIFTED:
        key, shift = _SHIFTED[ch], True
    else:
        key, shift = ch, False
    code = ax.KEYCODES[key]
    for down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(_src, code, down)
        Quartz.CGEventSetFlags(e, Quartz.kCGEventFlagMaskShift if shift else 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.005)


def _hid_combo(combo: str):
    code, flags = ax.parse_combo(combo)
    for down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(_src, code, down)
        Quartz.CGEventSetFlags(e, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.01)


class Person:
    APP = "Stickies"

    def __init__(self, text: str, cps: float = 8.0):
        self.text = text
        self.cps = cps
        self.typed = ""
        self.lost_focus_at: float | None = None
        self.lost_to: str = ""
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.pid: int | None = None

    def start_app(self) -> bool:
        for attempt in range(3):
            subprocess.run(["open", "-a", self.APP])
            for _ in range(60):
                p, name = front_app()
                if name == self.APP:
                    self.pid = p
                    break
                time.sleep(0.1)
            if self.pid:
                break
        else:
            return False
        time.sleep(0.4)
        _hid_combo("cmd+n")          # a fresh note to type into
        time.sleep(0.8)
        return front_app()[1] == self.APP

    def _run(self):
        delay = 1.0 / self.cps
        i = 0
        while not self._stop.is_set():
            p, name = front_app()
            if p != self.pid:
                self.lost_focus_at = time.time()
                self.lost_to = name
                return
            if i < len(self.text):
                _hid_char(self.text[i])
                self.typed += self.text[i]
                i += 1
            self._stop.wait(delay)

    def start_typing(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join()

    def read_note(self) -> str | None:
        """Text of the Stickies note that holds what we typed (via AX)."""
        app = ax.find_app(self.APP)
        if app is None:
            return None
        probe = self.typed[:12]
        for w in ax.windows(app):
            for sa in ax.get(w, "AXChildren") or []:
                for ta in ax.get(sa, "AXChildren") or []:
                    if ax.get(ta, "AXRole") == "AXTextArea":
                        v = str(ax.get(ta, "AXValue") or "")
                        if probe and probe in v:
                            return v
        return None

    def close_note(self):
        """Close the note we created without saving it anywhere."""
        app = ax.find_app(self.APP)
        if app is None:
            return
        probe = self.typed[:12]
        for w in ax.windows(app):
            txt = ""
            for sa in ax.get(w, "AXChildren") or []:
                for ta in ax.get(sa, "AXChildren") or []:
                    if ax.get(ta, "AXRole") == "AXTextArea":
                        txt = str(ax.get(ta, "AXValue") or "")
            if probe and probe in txt:
                for b in ax.get(w, "AXChildren") or []:
                    if ax.get(b, "AXSubrole") == "AXCloseButton":
                        try:
                            ax.perform(b, "AXPress")
                        except ax.AXError:
                            pass
                time.sleep(0.6)
                # Stickies asks whether to keep the text; choose not to save.
                for w2 in ax.windows(app):
                    for el in _descendants(w2):
                        if ax.get(el, "AXRole") == "AXButton" and \
                                str(ax.get(el, "AXTitle") or "") in ("Don’t Save", "Don't Save", "Delete", "Delete Note"):
                            ax.perform(el, "AXPress")
                            return


def _descendants(el, depth=0):
    if depth > 6:
        return
    for c in ax.get(el, "AXChildren") or []:
        yield c
        yield from _descendants(c, depth + 1)
