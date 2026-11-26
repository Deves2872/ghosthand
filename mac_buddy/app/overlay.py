"""Tutorial overlay: a ghost pointer that glides to each control, a highlight
around it and a caption saying what to do.

The overlay is one transparent, click-through window over all screens, so it
never gets in the way of the person's own clicks. With "use my real pointer"
on, the real cursor travels the same path instead (CGWarpMouseCursorPosition,
which posts no events). If the person moves the mouse meanwhile, the buddy lets
go at once and falls back to the ghost.
"""
from __future__ import annotations

import math
import time

import AppKit
import objc
import Quartz
from Foundation import NSMakeRect, NSObject, NSTimer

PURPLE = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.30, 0.95, 1.0)


def _primary_height() -> float:
    return AppKit.NSScreen.screens()[0].frame().size.height


def _union_frame():
    fr = None
    for s in AppKit.NSScreen.screens():
        fr = s.frame() if fr is None else AppKit.NSUnionRect(fr, s.frame())
    return fr


def ax_to_cocoa(x: float, y: float) -> tuple[float, float]:
    """Accessibility uses a top-left origin; Cocoa a bottom-left one."""
    return x, _primary_height() - y


def mouse_ax() -> tuple[float, float]:
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return p.x, p.y


def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


class GhostView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(GhostView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.pointer = None      # (x, y) in view coords
        self.target = None       # NSRect in view coords
        self.caption = ""
        self.step_label = ""
        self.ring = 0.0          # click ripple 0..1
        self.show_pointer = True
        return self

    def isFlipped(self):
        return False

    def drawRect_(self, rect):
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)
        if self.target is not None:
            r = AppKit.NSInsetRect(self.target, -6, -6)
            path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, 8, 8)
            PURPLE.colorWithAlphaComponent_(0.12).set()
            path.fill()
            PURPLE.set()
            path.setLineWidth_(3)
            path.stroke()
        if self.pointer is not None and self.ring > 0:
            x, y = self.pointer
            rad = 6 + 26 * self.ring
            c = AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - rad, y - rad, 2 * rad, 2 * rad))
            PURPLE.colorWithAlphaComponent_(0.6 * (1 - self.ring)).set()
            c.setLineWidth_(3)
            c.stroke()
        if self.pointer is not None and self.show_pointer:
            self._draw_arrow(*self.pointer)
        if self.caption and (self.target is not None or self.pointer is not None):
            self._draw_caption()

    @objc.python_method
    def _draw_arrow(self, x, y):
        pts = [(0, 0), (0, -22), (5.5, -17), (9.5, -26), (13, -24.5), (9, -15.5), (16, -15.5)]
        p = AppKit.NSBezierPath.bezierPath()
        p.moveToPoint_((x + pts[0][0], y + pts[0][1]))
        for dx, dy in pts[1:]:
            p.lineToPoint_((x + dx, y + dy))
        p.closePath()
        shadow = AppKit.NSShadow.alloc().init()
        shadow.setShadowBlurRadius_(4)
        shadow.setShadowOffset_((0, -1))
        shadow.setShadowColor_(AppKit.NSColor.colorWithWhite_alpha_(0, 0.35))
        AppKit.NSGraphicsContext.saveGraphicsState()
        shadow.set()
        PURPLE.set()
        p.fill()
        AppKit.NSGraphicsContext.restoreGraphicsState()
        AppKit.NSColor.whiteColor().set()
        p.setLineWidth_(1.5)
        p.stroke()

    @objc.python_method
    def _draw_caption(self):
        text = (self.step_label + "\n" if self.step_label else "") + self.caption
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setLineSpacing_(2)
        attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(15, 0.3),
                 AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
                 AppKit.NSParagraphStyleAttributeName: para}
        s = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        box = s.boundingRectWithSize_options_((320, 400), AppKit.NSStringDrawingUsesLineFragmentOrigin)
        w, h = box.size.width + 24, box.size.height + 18
        if self.target is not None:
            t = self.target
            x = t.origin.x
            y = t.origin.y - h - 14  # below the highlight
            if y < 10:
                y = t.origin.y + t.size.height + 14
        else:
            x, y = self.pointer[0] + 22, self.pointer[1] - h - 10
        b = self.bounds()
        x = max(10, min(x, b.size.width - w - 10))
        y = max(10, min(y, b.size.height - h - 10))
        bg = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), 10, 10)
        AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.92).set()
        bg.fill()
        s.drawWithRect_options_(NSMakeRect(x + 12, y + 9, w - 24, h - 18), AppKit.NSStringDrawingUsesLineFragmentOrigin)


class Overlay(NSObject):
    def init(self):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        fr = _union_frame()
        self.origin = fr.origin
        self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            fr, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        self.win.setOpaque_(False)
        self.win.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.win.setIgnoresMouseEvents_(True)
        self.win.setLevel_(AppKit.NSStatusWindowLevel)
        self.win.setHasShadow_(False)
        self.win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                        | AppKit.NSWindowCollectionBehaviorStationary)
        self.view = GhostView.alloc().initWithFrame_(NSMakeRect(0, 0, fr.size.width, fr.size.height))
        self.win.setContentView_(self.view)
        self.timer = None
        self.anim = None
        self.real = False
        self.last_warp = None
        return self

    # coordinates: AX (top-left, global) → this view
    @objc.python_method
    def _to_view(self, x, y):
        cx, cy = ax_to_cocoa(x, y)
        return cx - self.origin.x, cy - self.origin.y

    @objc.python_method
    def showStep(self, info):
        """info: dict(frame={x,y,w,h} in AX coords, caption, label, real_pointer)."""
        f = info.get("frame") or {}
        caption, label, real = info.get("caption", ""), info.get("label", ""), info.get("real_pointer", False)
        self.view.caption, self.view.step_label = caption, label
        if f.get("w"):
            vx, vy = self._to_view(f["x"], f["y"] + f["h"])
            self.view.target = NSMakeRect(vx, vy, f["w"], f["h"])
            dest_ax = (f["x"] + min(f["w"] / 2, 40), f["y"] + f["h"] / 2)
        else:
            self.view.target = None
            mx, my = mouse_ax()
            dest_ax = (mx + 60, my + 40)
        start_ax = self.anim["dest"] if self.anim else mouse_ax()
        self.real = real
        self.view.show_pointer = not real
        self.last_warp = mouse_ax() if real else None
        self.anim = {"start": start_ax, "dest": dest_ax, "t0": time.time(), "dur": 0.9, "ring_t0": None}
        self.win.orderFrontRegardless()
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 60, self, "tick:", None, True)

    def tick_(self, timer):
        a = self.anim
        if not a:
            return
        t = (time.time() - a["t0"]) / a["dur"]
        e = _ease(t)
        x = a["start"][0] + (a["dest"][0] - a["start"][0]) * e
        y = a["start"][1] + (a["dest"][1] - a["start"][1]) * e
        if self.real:
            here = mouse_ax()
            if self.last_warp and math.hypot(here[0] - self.last_warp[0], here[1] - self.last_warp[1]) > 4:
                self.real = False            # the person took the mouse: let go
                self.view.show_pointer = True
            else:
                Quartz.CGWarpMouseCursorPosition((x, y))
                Quartz.CGAssociateMouseAndMouseCursorPosition(True)
                self.last_warp = (x, y)
        self.view.pointer = self._to_view(x, y)
        if t >= 1:
            if a["ring_t0"] is None:
                a["ring_t0"] = time.time()
            self.view.ring = min(1.0, (time.time() - a["ring_t0"]) / 0.6)
            if self.view.ring >= 1:
                a["ring_t0"] = time.time() + 0.6  # pulse again after a pause
        self.view.setNeedsDisplay_(True)

    @objc.python_method
    def pulse(self):
        if self.anim:
            self.anim["ring_t0"] = time.time()

    @objc.python_method
    def hide(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.anim = None
        self.view.pointer = self.view.target = None
        self.view.caption = ""
        self.win.orderOut_(None)
