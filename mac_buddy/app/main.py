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
import time

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
        m.addItem_(self._mi("Take meeting notes…", "meetingNotes:"))
        m.addItem_(self._mi("Open meeting notes", "openNotes:"))
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
        r = perms.modal(alert)
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
                perms.modal(bad)
                return self.askKey_(sender)

    def checkPerms_(self, sender):
        def done(ok):
            msg = AppKit.NSAlert.alloc().init()
            msg.setMessageText_("All set" if ok else "Mac Buddy still can't use your apps")
            msg.setInformativeText_("Mac Buddy can see and use your apps." if ok else
                                    "Turn on Mac Buddy under Accessibility, then try again.")
            perms.modal(msg)
            if ok:
                self._start_worker()
        perms.ask("accessibility", done)

    def forgetPlans_(self, sender):
        from ..planner import CACHE_PATH
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass

    def meetingNotes_(self, sender):
        self.start_meeting_notes()

    def openNotes_(self, sender):
        from ..meeting import NOTES_DIR
        os.makedirs(NOTES_DIR, exist_ok=True)
        subprocess.run(["open", NOTES_DIR])

    def stopNotes_(self, sender):
        self.stop_meeting_notes()

    def badgeTick_(self, timer):
        t = getattr(self, "taker", None)
        if t is None:
            return
        secs = int(time.time() - t.meeting.started)
        n = len(t.meeting.lines) + len(t.live)
        self.badge_label.setStringValue_(f"📝 Taking notes · {secs // 60}:{secs % 60:02d} · {n} lines")

    @objc.python_method
    def start_meeting_notes(self):
        from ..meeting import NoteTaker, find_meeting, turn_on_captions
        if getattr(self, "taker", None) is not None:
            self.panel._append("\nI'm already taking notes. Click Stop on the badge to finish.\n")
            return
        found = find_meeting()
        if found is None:
            self.panel.show_near()
            self.panel._append("\nI don't see a meeting. Open it (Google Meet in Chrome, Zoom or Teams), "
                               "then ask me again.\n", color="systemOrangeColor")
            self.panel.set_busy(False, "")
            return
        app, win, title = found
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Have you told everyone you're taking notes?")
        alert.setInformativeText_(f"Mac Buddy will take notes of “{title}” from its live captions. Let the "
                                  "other people in the meeting know before you start. It's the polite thing "
                                  "to do, and in many places the law requires it.")
        alert.addButtonWithTitle_("Yes, start taking notes")
        alert.addButtonWithTitle_("Not yet")
        if perms.modal(alert) != AppKit.NSAlertFirstButtonReturn:
            self.panel.set_busy(False, "")
            return
        pressed = turn_on_captions(app, win)
        self.taker = NoteTaker(app, win, title)
        self.taker.start()
        self._show_badge()
        self.panel._append(f"\n📝 Taking notes of “{title}”"
                           + (" (I turned on its captions)" if pressed else
                              ". Make sure the meeting's captions (CC) are on.") + "\n", color="systemPurpleColor")
        self.panel.set_busy(False, "Taking notes…")

    @objc.python_method
    def _show_badge(self):
        scr = AppKit.NSScreen.mainScreen().visibleFrame()
        w, h = 300, 40
        b = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(scr.origin.x + scr.size.width - w - 16, scr.origin.y + scr.size.height - h - 10, w, h),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        b.setLevel_(AppKit.NSFloatingWindowLevel)
        b.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces)
        b.setOpaque_(False)
        b.setBackgroundColor_(AppKit.NSColor.clearColor())
        v = AppKit.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        v.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        v.setState_(AppKit.NSVisualEffectStateActive)
        v.setWantsLayer_(True)
        v.layer().setCornerRadius_(10)
        lab = AppKit.NSTextField.labelWithString_("📝 Taking notes")
        lab.setFrame_(NSMakeRect(12, 11, w - 92, 18))
        lab.setFont_(AppKit.NSFont.systemFontOfSize_weight_(12.5, 0.4))
        v.addSubview_(lab)
        stop = AppKit.NSButton.buttonWithTitle_target_action_("Stop", self, "stopNotes:")
        stop.setFrame_(NSMakeRect(w - 74, 6, 64, 28))
        stop.setRefusesFirstResponder_(True)
        v.addSubview_(stop)
        b.setContentView_(v)
        b.orderFrontRegardless()
        self.badge, self.badge_label = b, lab
        from Foundation import NSTimer
        self.badge_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "badgeTick:", None, True)

    @objc.python_method
    def stop_meeting_notes(self):
        t = getattr(self, "taker", None)
        if t is None:
            return
        self.taker = None
        self.badge_timer.invalidate()
        self.badge.orderOut_(None)
        meeting = t.stop()
        self.panel.show_near()
        self.panel._append(f"\nWriting up the notes ({len(meeting.lines)} caption lines)…\n", dim=True)
        self.panel.set_busy(True, "Writing notes…")
        self._start_worker()
        import asyncio
        from ..meeting import write_notes

        async def go():
            self.worker.ready.wait(30)
            return await write_notes(meeting, self.worker.client, self.worker.model)

        def done(fut):
            try:
                path, notes = fut.result()
                AppHelper.callAfter(self._notes_done, path, notes, "")
            except Exception as e:
                AppHelper.callAfter(self._notes_done, "", "", str(e))
        asyncio.run_coroutine_threadsafe(go(), self.worker.loop).add_done_callback(done)

    @objc.python_method
    def _notes_done(self, path, notes, err):
        if err:
            self.panel.on_done({"status": "failed", "evidence": f"Couldn't write the notes: {err}"})
            return
        self.panel._append("\n" + notes + "\n")
        self.panel._append(f"\nSaved to {path}\n", dim=True, size=11)
        self.panel.set_busy(False, "Notes saved")

    def openLogs_(self, sender):
        from ..display import LOG_DIR
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    # ----------------------------------------------- requests (from the panel)
    @objc.python_method
    def submit(self, text: str, show_me: bool = False):
        import re
        if re.search(r"\b(take|taking|start)\b.*\bnotes?\b.*\b(meeting|call|standup|sync)\b|"
                     r"\bmeeting notes\b|\bnote[- ]?taker\b", text, re.I):
            if re.search(r"\b(stop|end|finish)\b", text, re.I):
                self.stop_meeting_notes()
            else:
                self.start_meeting_notes()
            return
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
    def ask_command(self, command, why, risky, callback):
        self.panel.ask_command(command, why, risky, callback)
        if not self.panel.panel.isVisible():
            self.panel.show_near()

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
