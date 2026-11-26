"""Permissions, asked for the moment they're needed, as real popups.

No "go to System Settings › Privacy & Security › …" instructions: the buddy
shows a short explanation with one button. The button triggers the system's
own permission dialog where macOS has one (microphone), and otherwise opens
System Settings on the exact pane (Accessibility, Dictation, …). The buddy then
waits and carries on by itself once the switch is on.
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable

import ApplicationServices as AS
import AppKit
import objc

PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
}

WHY = {
    "accessibility": ("Let Mac Buddy see and use your apps",
                      "Mac Buddy reads buttons and text in your apps and presses them for you, in the "
                      "background. macOS asks you to switch this on once.\n\nClick “Open Settings”, then turn "
                      "on “{app}”. Mac Buddy carries on by itself."),
    "microphone": ("Let Mac Buddy hear you",
                   "So you can talk instead of type. Your voice is turned into text on this Mac; "
                   "nothing is sent anywhere."),
}

_AV = None


def _avcapture():
    global _AV
    if _AV is None:
        objc.loadBundle("AVFoundation", globals(), bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        _AV = objc.lookUpClass("AVCaptureDevice")
    return _AV


def granted(kind: str) -> bool:
    if kind == "accessibility":
        return bool(AS.AXIsProcessTrusted())
    if kind == "microphone":
        return _avcapture().authorizationStatusForMediaType_("soun") == 3  # authorized
    return True


def app_name() -> str:
    """What the person will see in the Settings list: the .app we run in, or the terminal."""
    from ..permissions import responsible_app
    return responsible_app()


def open_pane(kind: str):
    subprocess.run(["open", PANES[kind]], check=False)


def ask(kind: str, on_done: Callable[[bool], None], wait_s: float = 180.0):
    """Explain, then get the permission with one click. Calls on_done(granted) on the
    main thread. Must be called on the main thread."""
    if granted(kind):
        on_done(True)
        return
    title, body = WHY[kind]
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(body.format(app=app_name()))
    alert.addButtonWithTitle_("Open Settings" if kind != "microphone" else "Allow Microphone")
    alert.addButtonWithTitle_("Not now")
    alert.setIcon_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "hand.raised.fill" if kind == "accessibility" else "mic.fill", None))
    AppKit.NSApp.activateIgnoringOtherApps_(True)  # the person asked us for something; show the alert
    if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
        on_done(False)
        return
    if kind == "microphone":
        status = _avcapture().authorizationStatusForMediaType_("soun")
        if status == 0:  # never asked: the system's own dialog
            def handler(ok):
                from PyObjCTools import AppHelper
                AppHelper.callAfter(on_done, bool(ok))
            _avcapture().requestAccessForMediaType_completionHandler_("soun", handler)
            return
        open_pane("microphone")  # asked before and refused: only Settings can change it
    else:
        AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
        open_pane(kind)
    _wait(kind, on_done, wait_s)


def _wait(kind: str, on_done: Callable[[bool], None], wait_s: float):
    """Poll in the background until the switch is on; report back on the main thread."""
    from PyObjCTools import AppHelper

    def run():
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if granted(kind):
                AppHelper.callAfter(on_done, True)
                return
            time.sleep(0.7)
        AppHelper.callAfter(on_done, False)
    threading.Thread(target=run, daemon=True).start()
