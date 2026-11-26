"""Mac Buddy: a helper that lives in the menu bar.

Call it up with ⌘ + double-click anywhere (or ⌃⌥Space, or the menu bar icon),
and a chat opens next to the pointer. Ask in words, typed or spoken. It either
does the task in the background or, in "Show me how" mode, points at each step
with a ghost pointer.

    uv run mac-buddy-app
"""
from __future__ import annotations

import os
import subprocess
import sys

import AppKit
import objc
from Foundation import NSMakeRect, NSObject
from PyObjCTools import AppHelper

from . import keychain, perms
from .overlay import Overlay
from .panel import BuddyPanel

PREFS = AppKit.NSUserDefaults.standardUserDefaults()


class AppDelegate(NSObject):
    # ------------------------------------------------------------- startup
    def applicationDidFinishLaunching_(self, note):
        from ..harness import load_dotenv
        load_dotenv()
        keychain.load_into_env()
        self.worker = None
        self.voice = None
        self.overlay = Overlay.alloc().init()
        self.panel = BuddyPanel.alloc().initWithOwner_(self)
        self._status_item()
        self._triggers()
        AppHelper.callLater(0.3, self._onboard)

    @objc.python_method
    def _status_item(self):
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("hand.wave.fill", "Mac Buddy")
        img.setTemplate_(True)
        self.item.button().setImage_(img)
        self.item.button().setToolTip_("Mac Buddy: ⌘ + double-click anywhere to ask")
        m = AppKit.NSMenu.alloc().init()
        m.addItem_(self._mi("Ask Mac Buddy…", "openPanel:", "b"))
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        self.real_item = self._mi("Use my real pointer in “Show me how”", "toggleReal:")
        self.real_item.setState_(PREFS.boolForKey_("realPointer"))
        m.addItem_(self.real_item)
        self.trigger_item = self._mi("Open with ⌘ + double-click", "toggleTrigger:")
        self.trigger_item.setState_(not PREFS.boolForKey_("noCmdDoubleClick"))
        m.addItem_(self.trigger_item)
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        m.addItem_(self._mi("API key…", "askKey:"))
        m.addItem_(self._mi("Check permissions…", "checkPerms:"))
        m.addItem_(self._mi("Forget saved plans", "forgetPlans:"))
        m.addItem_(self._mi("Open activity log", "openLogs:"))
        m.addItem_(AppKit.NSMenuItem.separatorItem())
        m.addItem_(self._mi("Quit Mac Buddy", "terminate:", "q", target=AppKit.NSApp))
        self.item.setMenu_(m)

    @objc.python_method
    def _mi(self, title, action, key="", target=None):
        it = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        it.setTarget_(target or self)
        return it

    @objc.python_method
    def _triggers(self):
        """⌘ + double-click anywhere, or ⌃⌥Space. Global monitors only listen: the click
        still reaches the app under the pointer."""
        def on_click(ev):
            if PREFS.boolForKey_("noCmdDoubleClick"):
                return
            if ev.clickCount() == 2 and (ev.modifierFlags() & AppKit.NSEventModifierFlagCommand):
                AppHelper.callAfter(self.openPanel_, None)

        def on_key(ev):
            flags = ev.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
            if ev.keyCode() == 49 and flags == (AppKit.NSEventModifierFlagControl | AppKit.NSEventModifierFlagOption):
                AppHelper.callAfter(self.openPanel_, None)
        self._mon_click = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, on_click)
        self._mon_key = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown, on_key)

    @objc.python_method
    def _onboard(self):
        """First run: API key, then Accessibility. Each as one simple popup."""
        if not keychain.get_key():
            self.askKey_(None)
            if not keychain.get_key():
                return
        if not perms.granted("accessibility"):
            perms.ask("accessibility", self._after_access)
        else:
            self._after_access(True)

    @objc.python_method
    def _after_access(self, ok):
        if ok:
            self._start_worker()
            if not PREFS.boolForKey_("welcomed"):
                PREFS.setBool_forKey_(True, "welcomed")
                self._notify_welcome()

    @objc.python_method
    def _notify_welcome(self):
        self.panel.show_near()

    @objc.python_method
    def _start_worker(self):
        if self.worker is None and keychain.get_key():
            from .worker import Worker
            self.worker = Worker(self)

    # ------------------------------------------------------------- actions
    def openPanel_(self, sender):
        self.panel.show_near()

    def toggleReal_(self, sender):
        v = not PREFS.boolForKey_("realPointer")
        PREFS.setBool_forKey_(v, "realPointer")
        sender.setState_(v)

    def toggleTrigger_(self, sender):
        off = not PREFS.boolForKey_("noCmdDoubleClick")
        PREFS.setBool_forKey_(off, "noCmdDoubleClick")
        sender.setState_(not off)

    def askKey_(self, sender):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Connect Mac Buddy to Claude")
        alert.setInformativeText_("Mac Buddy uses Claude to understand what you ask. Paste your Anthropic API key "
                                  "below. It's kept safely in your Mac's Keychain.\n\nDon't have one? Click "
                                  "“Get a key” to create one at console.anthropic.com.")
        field = AppKit.NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 24))
        field.setPlaceholderString_("sk-ant-…")
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Get a key")
        alert.addButtonWithTitle_("Cancel")
        alert.window().setInitialFirstResponder_(field)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        r = alert.runModal()
        if r == AppKit.NSAlertSecondButtonReturn:
            subprocess.run(["open", "https://console.anthropic.com/settings/keys"])
            return self.askKey_(sender)
        if r == AppKit.NSAlertFirstButtonReturn:
            key = str(field.stringValue()).strip()
            if keychain.save_key(key):
                os.environ["ANTHROPIC_API_KEY"] = key
                if self.worker is not None:
                    self.worker.reset_client()
                elif sender is not None:
                    self._onboard()
            else:
                bad = AppKit.NSAlert.alloc().init()
                bad.setMessageText_("That didn't look like an API key")
                bad.setInformativeText_("Keys start with “sk-ant-”. Copy it again from console.anthropic.com.")
                bad.runModal()
                return self.askKey_(sender)

    def checkPerms_(self, sender):
        def done(ok):
            msg = AppKit.NSAlert.alloc().init()
            msg.setMessageText_("All set" if ok else "Mac Buddy still can't use your apps")
            msg.setInformativeText_("Mac Buddy can see and use your apps." if ok else
                                    "Turn on Mac Buddy under Accessibility, then try again.")
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            msg.runModal()
            if ok:
                self._start_worker()
        perms.ask("accessibility", done)

    def forgetPlans_(self, sender):
        from ..planner import CACHE_PATH
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass

    def openLogs_(self, sender):
        from ..display import LOG_DIR
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    # ----------------------------------------------- requests (from the panel)
    @objc.python_method
    def submit(self, text: str, show_me: bool = False):
        if not keychain.get_key():
            self.panel.on_done({"status": "failed", "evidence": "I need an API key first."})
            self.askKey_(None)
            return
        if not perms.granted("accessibility"):
            def then(ok):
                if ok:
                    self._start_worker()
                    self.submit(text, show_me)
                else:
                    self.panel.on_done({"status": "failed",
                                        "evidence": "I need permission to use your apps for that."})
            perms.ask("accessibility", then)
            return
        self._start_worker()
        self.worker.submit(text, show_me=show_me, real_pointer=PREFS.boolForKey_("realPointer"))

    @objc.python_method
    def stop_request(self):
        if self.worker:
            self.worker.stop()
        self.overlay.hide()

    @objc.python_method
    def decide(self, d: str):
        if d != "go":
            self.overlay.hide()
        else:
            self.overlay.pulse()
        if self.worker:
            self.worker.decide(d)

    # ------------------------------------------------- worker → UI (main thread)
    @objc.python_method
    def on_event(self, kind, data):
        self.panel.on_event(kind, data)

    @objc.python_method
    def show_step(self, i, step, frame, show_me, real_pointer):
        self.panel.show_step(i, step, frame, show_me, real_pointer)
        if show_me:
            total = frame.get("total") or "?"
            self.overlay.showStep({"frame": frame, "caption": step["say"],
                                     "label": f"Step {i + 1} of {total}", "real_pointer": real_pointer})

    @objc.python_method
    def hide_step(self):
        self.panel.hide_step()

    @objc.python_method
    def on_done(self, out):
        self.overlay.hide()
        self.panel.on_done(out)

    # ------------------------------------------------------------- voice
    @objc.python_method
    def start_listening(self) -> bool:
        if not perms.granted("microphone"):
            def then(ok):
                if ok:
                    self.panel.status.setStringValue_("Microphone on. Hold the mic button and talk.")
            perms.ask("microphone", then)
            return False
        if self.voice is None:
            from .voice import Voice
            self.voice = Voice()
        err = self.voice.start()
        if err:
            self.panel.status.setStringValue_(err)
            return False
        return True

    @objc.python_method
    def stop_listening(self):
        if self.voice:
            self.voice.stop(self.panel.voice_text, self.panel.status.setStringValue_)


def main():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # menu bar only, no Dock icon
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
