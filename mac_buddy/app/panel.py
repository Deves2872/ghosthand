"""The chat panel that opens next to the pointer.

It is a non-activating panel: you can type in it, but the app you were using
stays the front app. That matters, because Mac Buddy works on your apps in the
background.
"""
from __future__ import annotations

import AppKit
import objc
from Foundation import NSMakeRect, NSObject

from ..display import summarize

W, H = 460, 470
PAD = 14


def _color(name):
    return getattr(AppKit.NSColor, name)()


class _Panel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):  # Esc closes the buddy
        self.orderOut_(None)


class _HoldButton(AppKit.NSButton):
    """Mic button: hold to talk (release to stop), or click to start and click again to stop."""

    def mouseDown_(self, event):
        import time
        t0 = time.time()
        owner = self.owner
        if owner.voice_state == "listening":   # click while listening: stop
            owner.stop_listening()
            return
        owner.start_listening()
        objc.super(_HoldButton, self).mouseDown_(event)  # tracks until the mouse goes up
        if time.time() - t0 > 0.35 and owner.voice_state == "listening":
            owner.stop_listening()             # it was a hold: push-to-talk


class BuddyPanel(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(BuddyPanel, self).init()
        if self is None:
            return None
        self.owner = owner  # the app delegate
        self.voice_state = "idle"
        self.busy = False
        self.confirm_cb = None
        self._build()
        return self

    # ------------------------------------------------------------------ UI
    @objc.python_method
    def _build(self):
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskNonactivatingPanel | AppKit.NSWindowStyleMaskFullSizeContentView
                 | AppKit.NSWindowStyleMaskUtilityWindow)
        p = _Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, AppKit.NSBackingStoreBuffered, False)
        p.setTitle_("Mac Buddy")
        p.setTitlebarAppearsTransparent_(True)
        p.setFloatingPanel_(True)
        p.setLevel_(AppKit.NSFloatingWindowLevel)
        p.setHidesOnDeactivate_(False)
        p.setBecomesKeyOnlyIfNeeded_(False)
        p.setReleasedWhenClosed_(False)
        p.setMovableByWindowBackground_(True)
        p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel = p
        v = AppKit.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        v.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
        v.setState_(AppKit.NSVisualEffectStateActive)
        p.setContentView_(v)

        # header
        icon = AppKit.NSImageView.imageViewWithImage_(
            AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("hand.wave.fill", "Mac Buddy"))
        icon.setFrame_(NSMakeRect(PAD, H - 58, 26, 26))
        icon.setContentTintColor_(_color("systemPurpleColor"))
        v.addSubview_(icon)
        self.title = self._label("Hi! What can I do for you?", NSMakeRect(PAD + 34, H - 56, W - 2 * PAD - 34, 22),
                                 size=15, weight=0.4)
        v.addSubview_(self.title)

        # transcript
        top_of_bottom = 118
        sv = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, top_of_bottom, W - 2 * PAD,
                                                                   H - 70 - top_of_bottom))
        sv.setHasVerticalScroller_(True)
        sv.setDrawsBackground_(False)
        sv.setBorderType_(AppKit.NSNoBorder)
        tv = AppKit.NSTextView.alloc().initWithFrame_(sv.contentView().bounds())
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setTextContainerInset_((2, 4))
        tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        sv.setDocumentView_(tv)
        v.addSubview_(sv)
        self.text = tv
        self._hint()

        # tutorial step box (hidden until a step waits for the person)
        self.stepbox = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(PAD, 76, W - 2 * PAD, 36))
        self.b_do = self._button("Do it for me", "stepGo:", NSMakeRect(0, 2, 130, 30))
        self.b_did = self._button("I did it", "stepSkip:", NSMakeRect(138, 2, 100, 30))
        self.b_stop = self._button("Stop", "stepStop:", NSMakeRect(W - 2 * PAD - 80, 2, 80, 30))
        for b in (self.b_do, self.b_did, self.b_stop):
            b.setRefusesFirstResponder_(True)  # a stray Space or Return can't press it
            self.stepbox.addSubview_(b)
        self.stepbox.setHidden_(True)
        v.addSubview_(self.stepbox)

        # mode: do it / show me
        self.mode = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ["Do it for me", "Show me how"], AppKit.NSSegmentSwitchTrackingSelectOne, self, "modeChanged:")
        self.mode.setSelectedSegment_(0)
        self.mode.setFrame_(NSMakeRect(PAD, 46, 230, 24))
        v.addSubview_(self.mode)
        self.status = self._label("", NSMakeRect(PAD + 240, 48, W - 2 * PAD - 240, 18), size=11, dim=True)
        self.status.setAlignment_(AppKit.NSTextAlignmentRight)
        v.addSubview_(self.status)

        # input row
        self.field = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, 12, W - 2 * PAD - 84, 28))
        self.field.setPlaceholderString_("Ask me anything… e.g. “Add ‘milk’ to my shopping note”")
        self.field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        self.field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
        self.field.setTarget_(self)
        self.field.setAction_("send:")
        v.addSubview_(self.field)
        mic = _HoldButton.alloc().initWithFrame_(NSMakeRect(W - PAD - 78, 11, 36, 30))
        mic.owner = self
        mic.setBezelStyle_(AppKit.NSBezelStyleRounded)
        mic.setImage_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("mic.fill", "Talk"))
        mic.setToolTip_("Talk instead of typing: hold to talk, or click to start and click again to stop")
        v.addSubview_(mic)
        self.mic = mic
        send = self._button("", "send:", NSMakeRect(W - PAD - 38, 11, 38, 30))
        send.setImage_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("arrow.up.circle.fill",
                                                                                         "Send"))
        send.setToolTip_("Send (Return)")
        v.addSubview_(send)
        self.send_button = send

    @objc.python_method
    def _label(self, s, frame, size=13, weight=0.0, dim=False):
        l = AppKit.NSTextField.labelWithString_(s)
        l.setFrame_(frame)
        l.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
        if dim:
            l.setTextColor_(_color("secondaryLabelColor"))
        l.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        return l

    @objc.python_method
    def _button(self, title, action, frame, key=None):
        b = AppKit.NSButton.buttonWithTitle_target_action_(title, self, action)
        b.setFrame_(frame)
        b.setBezelStyle_(AppKit.NSBezelStyleRounded)
        if key:
            b.setKeyEquivalent_(key)
        return b

    @objc.python_method
    def _hint(self):
        self.text.setString_("")
        self._append("Tell me what to do in any app, by typing or talking. A few ideas:\n"
                     "  • “In TextEdit, add the line ‘call mom’ to my notes and save it”\n"
                     "  • “Show me how to make text bold in TextEdit”\n"
                     "  • “How many files are in my Downloads folder?”\n\n"
                     "Choose “Show me how” and I'll point at each step instead of doing it all for you.\n",
                     dim=True)

    # ------------------------------------------------------------ showing
    @objc.python_method
    def show_near(self, ax_point=None):
        """Open next to the pointer (AX top-left coords), kept on screen."""
        import Quartz
        if ax_point is None:
            p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            ax_point = (p.x, p.y)
        from .overlay import ax_to_cocoa
        cx, cy = ax_to_cocoa(*ax_point)
        screen = next((s for s in AppKit.NSScreen.screens()
                       if AppKit.NSPointInRect((cx, cy), s.frame())), AppKit.NSScreen.mainScreen())
        vf = screen.visibleFrame()
        x = min(max(cx + 16, vf.origin.x + 8), vf.origin.x + vf.size.width - W - 8)
        y = min(max(cy - H - 16, vf.origin.y + 8), vf.origin.y + vf.size.height - H - 8)
        self.panel.setFrameOrigin_((x, y))
        self.panel.orderFrontRegardless()
        self.panel.makeKeyWindow()
        self.panel.makeFirstResponder_(self.field)

    @objc.python_method
    def give_back_keyboard(self):
        """Stop being the keyboard target, so the person's typing goes to their own app
        again. The panel stays visible."""
        self.panel.makeFirstResponder_(None)
        self.panel.orderOut_(None)
        self.panel.orderFrontRegardless()

    @objc.python_method
    def _append(self, s, color=None, bold=False, dim=False, size=13):
        attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(size, 0.5 if bold else 0.0),
                 AppKit.NSForegroundColorAttributeName:
                     _color(color) if color else _color("secondaryLabelColor" if dim else "labelColor")}
        s = AppKit.NSAttributedString.alloc().initWithString_attributes_(s, attrs)
        self.text.textStorage().appendAttributedString_(s)
        self.text.scrollRangeToVisible_((self.text.string().length(), 0))

    @objc.python_method
    def set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        self.field.setEnabled_(not busy)
        self.send_button.setTitle_("")
        self.send_button.setImage_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "stop.circle.fill" if busy else "arrow.up.circle.fill", "Stop" if busy else "Send"))
        self.send_button.setToolTip_("Stop" if busy else "Send (Return)")
        self.status.setStringValue_(status)

    # ------------------------------------------------------------- actions
    def send_(self, sender):
        if self.busy:
            if sender is self.send_button:
                self.owner.stop_request()
            return
        text = str(self.field.stringValue()).strip()
        if not text:
            return
        self.field.setStringValue_("")
        if self.text.string().startswith("Tell me what to do"):
            self.text.setString_("")
        self._append(f"\n{text}\n", bold=True)
        self.set_busy(True, "Working…")
        self.give_back_keyboard()
        self.owner.submit(text, show_me=self.mode.selectedSegment() == 1)

    def modeChanged_(self, sender):
        self.status.setStringValue_("I'll point at each step" if sender.selectedSegment() == 1 else "")

    def stepGo_(self, sender):
        if self._answer_command(True):
            return
        self.owner.decide("go")

    def stepSkip_(self, sender):
        self.owner.decide("skip")

    def stepStop_(self, sender):
        if self._answer_command(False):
            return
        self.owner.decide("stop")

    @objc.python_method
    def ask_command(self, command, why, risky, callback):
        """Show the exact command and wait for a click on Run or Cancel."""
        self.confirm_cb = callback
        self._append("\nI'd like to run this in Terminal" + (f" ({why.rstrip('.')})" if why else "") + ":\n")
        mono = {AppKit.NSFontAttributeName: AppKit.NSFont.monospacedSystemFontOfSize_weight_(12.5, 0.2),
                AppKit.NSForegroundColorAttributeName: _color("labelColor")}
        self.text.textStorage().appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(f"  $ {command}\n", mono))
        if risky:
            self._append("  ⚠ Careful: this " + "; ".join(risky) + ".\n", color="systemRedColor", bold=True,
                         size=12)
        self.text.scrollRangeToVisible_((self.text.string().length(), 0))
        self.b_do.setTitle_("Run")
        self.b_stop.setTitle_("Cancel")
        self.b_did.setHidden_(True)
        self.stepbox.setHidden_(False)
        self.status.setStringValue_("Waiting for you: Run or Cancel")

    @objc.python_method
    def _answer_command(self, ok) -> bool:
        cb = getattr(self, "confirm_cb", None)
        if cb is None:
            return False
        self.confirm_cb = None
        self.b_do.setTitle_("Do it for me")
        self.b_stop.setTitle_("Stop")
        self.b_did.setHidden_(False)
        self.stepbox.setHidden_(True)
        self._append("  ✓ Running it…\n" if ok else "  ✗ Not run.\n", color="systemGreenColor" if ok else
                     "systemOrangeColor", size=12)
        self.status.setStringValue_("Working…")
        cb(ok)
        return True

    # voice (the delegate does the work)
    @objc.python_method
    def start_listening(self):
        if self.owner.start_listening():
            self.voice_state = "listening"
            self.mic.setContentTintColor_(_color("systemRedColor"))
            self.status.setStringValue_("Listening… release or click to stop")

    @objc.python_method
    def stop_listening(self):
        self.voice_state = "transcribing"
        self.mic.setContentTintColor_(None)
        self.status.setStringValue_("Turning your words into text…")
        self.owner.stop_listening()

    @objc.python_method
    def voice_text(self, text):
        self.voice_state = "idle"
        self.status.setStringValue_("")
        if text:
            cur = str(self.field.stringValue())
            self.field.setStringValue_((cur + " " if cur else "") + text)
            self.panel.makeFirstResponder_(self.field)
            self.status.setStringValue_("Press Return to send")

    # ----------------------------------------------- events from the worker
    @objc.python_method
    def on_event(self, kind: str, data: dict):
        if kind == "plan":
            lead = "I'll do this from a plan I made before:" if data.get("cached") else (
                data.get("summary") or "Here's my plan:")
            self._append(f"{lead}\n", dim=False)
            for n, say in enumerate(data.get("steps") or [], 1):
                self._append(f"  {n}. {say}\n", dim=True)
        elif kind == "result":
            summary, alerts = summarize(data["name"], data["text"])
            color = ("systemRedColor" if summary.startswith("✗") else
                     "systemOrangeColor" if summary.startswith("⚠") else "systemGreenColor")
            mark = summary[:1] if summary[:1] in "✓⚠✗" else "•"
            self._append(f"  {mark} ", color=color)
            self._append(f"{_friendly(data['name'])}\n", dim=True, size=12)
        elif kind == "thought":
            self.status.setStringValue_(data.get("text", "")[:60])
        elif kind == "info" and data.get("text", "").startswith("plan stopped"):
            msg = ("  I can't show the next step from here, so I'll finish it for you, step by step…\n"
                   if self.mode.selectedSegment() == 1 else
                   "  Something didn't go as planned, so I'm working it out step by step…\n")
            self.stepbox.setHidden_(True)
            self._append(msg, color="systemOrangeColor", size=12)
        elif kind == "reply":
            self._append(data.get("text", "") + "\n")

    @objc.python_method
    def show_step(self, i, step, frame, show_me, real_pointer):
        total = frame.get("total") or "?"
        self.status.setStringValue_(f"Step {i + 1} of {total}")
        if show_me:
            if frame.get("retry"):
                self._append("  I don't see that change yet. Try again, or click “Do it for me”.\n",
                             color="systemOrangeColor", size=12)
            else:
                self._append(f"\nStep {i + 1}: {step['say']}\n", color="systemPurpleColor", bold=True)
            self.stepbox.setHidden_(False)  # never takes the keyboard: you may be typing elsewhere

    @objc.python_method
    def hide_step(self):
        self.stepbox.setHidden_(True)

    @objc.python_method
    def on_done(self, out: dict):
        self.stepbox.setHidden_(True)
        st = out.get("status")
        if st == "done":
            self._append("\n✓ Done. ", color="systemGreenColor", bold=True)
            if out.get("answer"):
                self._append(out["answer"] + "\n", bold=True)
            else:
                self._append("\n")
        elif st == "reply":
            pass
        elif st == "stopped":
            self._append("\n■ " + out.get("evidence", "Stopped.") + "\n", color="systemOrangeColor")
        else:
            self._append("\n✗ I couldn't finish this. ", color="systemRedColor", bold=True)
            self._append((out.get("evidence") or "") + "\n", size=12)
        how = []
        if out.get("cached"):
            how.append("used a saved plan")
        elif out.get("fell_back"):
            how.append("planned, then step by step")
        elif out.get("planned"):
            how.append("planned")
        if out.get("seconds"):
            how.append(f"{out['seconds']:.0f}s")
        self.set_busy(False, " · ".join(how))  # the keyboard stays where the person has it


_FRIENDLY = {"launch_app": "Opened the app", "new_document": "Made a new document", "observe": "Looked at the window",
             "click": "Clicked", "set_value": "Filled in a field", "replace_text": "Changed the text",
             "insert_text": "Added the text", "read_text": "Read the text", "press_key": "Pressed a key",
             "type_text": "Typed", "menu": "Looked in a menu", "select_menu": "Chose a menu item",
             "scroll": "Scrolled", "scroll_to": "Scrolled to it", "select_rows": "Selected items",
             "wait_for_save": "Checked it was saved", "list_apps": "Looked at your open apps",
             "open_url": "Opened the web page", "find": "Looked on the page", "media_control": "Checked the video",
             "bring_to_front": "Brought it to the front for you", "run_command": "Ran the command in Terminal"}


def _friendly(tool: str) -> str:
    return _FRIENDLY.get(tool, tool)
