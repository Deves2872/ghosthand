"""Continuous focus/cursor sampling.

Checking focus only before and after an action misses a steal that is undone
within the action. FocusMonitor samples on a background thread every few ms
for the whole duration of an action (or a whole benchmark run).

"Front app" is the WindowServer's front process (GetFrontProcess), because
NSWorkspace.frontmostApplication is stale in a process without a main run
loop, the top window in CGWindowList is not always the front app's, and the
target app's own AXFrontmost attribute lies once we synthetically focus it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import Quartz

import ctypes

from AppKit import NSRunningApplication

from .synthfocus import PSN, _AS

_AS.GetFrontProcess.argtypes = [ctypes.POINTER(PSN)]
_AS.GetFrontProcess.restype = ctypes.c_int32
_AS.GetProcessPID.argtypes = [ctypes.POINTER(PSN), ctypes.POINTER(ctypes.c_int32)]
_AS.GetProcessPID.restype = ctypes.c_int32
_names: dict[int, str] = {}


def front_app() -> tuple[int | None, str]:
    """The WindowServer's front process: the app that receives the person's
    keystrokes. Live in any thread, ~10µs per call."""
    psn = PSN()
    if _AS.GetFrontProcess(ctypes.byref(psn)) != 0:
        return None, ""
    pid = ctypes.c_int32()
    if _AS.GetProcessPID(ctypes.byref(psn), ctypes.byref(pid)) != 0:
        return None, ""
    p = pid.value
    if p not in _names:
        ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(p)
        _names[p] = str(ra.localizedName()) if ra else f"pid {p}"
    return p, _names[p]


front_window_owner = front_app


def mouse() -> tuple[float, float]:
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (round(p.x, 1), round(p.y, 1))


# ---------------------------------------------------------- who moved focus?
#
# The HID system keeps, per event type, the time since the last *hardware*
# event. The server's keys are posted straight to one process, so they never
# touch the key-down, click or mouse-move counters (measured; the agent's arrow
# keys do reset the catch-all "any input" counter, so that one isn't used).
# A switch right after real input is the person's doing.

PERSON_WINDOW_S = 1.5
_HUMAN_TYPES = (Quartz.kCGEventKeyDown, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown, Quartz.kCGEventMouseMoved, Quartz.kCGEventScrollWheel,
                Quartz.kCGEventLeftMouseDragged)


def person_input_age() -> float:
    """Seconds since the last real keyboard/mouse input."""
    return min(Quartz.CGEventSourceSecondsSinceLastEventType(Quartz.kCGEventSourceStateHIDSystemState, t)
               for t in _HUMAN_TYPES)


def attribute(new: tuple, input_age: float, target_pids: set, newly_launched: bool) -> tuple[str, str]:
    """('person' | 'agent?' | 'unknown', reason) for the app `new` coming to the front.

    The target app or a freshly launched app coming forward is always treated as
    possibly the agent's doing, even with input nearby: a person typing nonstop
    must not hide a real steal."""
    pid, name = new
    hint = f"; there was real keyboard/mouse input {input_age:.1f}s before, so it may have been the person" \
        if input_age <= PERSON_WINDOW_S else ""
    if pid in target_pids:
        return "agent?", f"{name!r} is the app being worked on{hint}"
    if newly_launched:
        return "agent?", f"{name!r} was not running before{hint}"
    if input_age <= PERSON_WINDOW_S:
        return "person", f"real keyboard/mouse input {input_age:.1f}s before"
    return "unknown", "no keyboard or mouse input just before"


@dataclass
class FocusReport:
    start_front: tuple
    end_front: tuple
    fronts_seen: list = field(default_factory=list)   # distinct (pid, name) in order
    mouse_start: tuple = (0, 0)
    mouse_end: tuple = (0, 0)
    mouse_moved: bool = False
    samples: int = 0
    duration_s: float = 0.0
    changes: list = field(default_factory=list)       # (new_front, input_age, newly_launched)

    @property
    def front_changed(self) -> bool:
        return len(self.fronts_seen) > 1

    def attributions(self, target_pid: int | None = None) -> list[tuple[str, str]]:
        targets = {target_pid} if target_pid is not None else set()
        return [attribute(new, age, targets, launched) for new, age, launched in self.changes]

    def person_only(self, target_pid: int | None = None) -> bool:
        """Every front change during this action was the person's own switch."""
        at = self.attributions(target_pid)
        return bool(at) and all(k == "person" for k, _ in at)

    def line(self, target_pid: int | None = None) -> str:
        names = " → ".join(n for _, n in self.fronts_seen) or "?"
        if self.front_changed and self.person_only(target_pid):
            msg = (f"focus ok: the person switched apps themselves during this action ({names}; "
                   f"{self.attributions(target_pid)[-1][1]}) — not caused by it")
        elif self.front_changed:
            why = "; ".join(r for _, r in self.attributions(target_pid))
            msg = f"FOCUS MOVED: front app went {names}" + (f" ({why})" if why else "")
        else:
            msg = f"focus ok: front app stayed {self.start_front[1]!r}"
        if target_pid is not None and any(p == target_pid for p, _ in self.fronts_seen) \
                and self.start_front[0] != target_pid:
            msg += " (the TARGET app came to the front — this must not happen)"
        if self.mouse_moved:
            msg += f"; cursor moved {self.mouse_start}→{self.mouse_end} (this server never moves it; likely the person)"
        else:
            msg += "; cursor unmoved"
        return msg + f" [{self.samples} samples over {self.duration_s:.2f}s]"


class FocusMonitor:
    def __init__(self, interval: float = 0.005):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.fronts: list = []
        self.changes: list = []
        self.known_pids: set | None = None
        self.samples = 0
        self.m0 = self.m1 = (0, 0)
        self.mouse_moved = False
        self.t0 = 0.0
        self.t1 = 0.0

    def _run(self):
        while not self._stop.is_set():
            f = front_window_owner()
            if not self.fronts or self.fronts[-1] != f:
                self.fronts.append(f)
                self.changes.append((f, person_input_age(),
                                     self.known_pids is not None and f[0] not in self.known_pids))
            m = mouse()
            if m != self.m0:
                self.mouse_moved = True
                self.m1 = m
            self.samples += 1
            self._stop.wait(self.interval)

    def __enter__(self):
        self.t0 = time.time()
        self.fronts = [front_window_owner()]
        self.changes = []
        from .ax import _live_pids
        self.known_pids = set(_live_pids())
        self.m0 = self.m1 = mouse()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def stop(self) -> FocusReport:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join()
            end = front_window_owner()
            if self.fronts[-1] != end:
                self.fronts.append(end)
                self.changes.append((end, person_input_age(), end[0] not in (self.known_pids or {end[0]})))
            self.t1 = time.time()
        return self.report()

    def report(self) -> FocusReport:
        return FocusReport(start_front=self.fronts[0], end_front=self.fronts[-1],
                           fronts_seen=list(self.fronts), mouse_start=self.m0,
                           mouse_end=self.m1, mouse_moved=self.mouse_moved,
                           samples=self.samples, duration_s=(self.t1 or time.time()) - self.t0,
                           changes=list(self.changes))
