"""MCP server: observe one window through the AX tree and act on it in the
background. Every action returns what changed in that window."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from mcp.server.fastmcp import FastMCP

from . import ax, snapshot as S, synthfocus
from .focus import FocusMonitor, attribute, front_app, person_input_age
from .notes import APP_NOTES

mcp = FastMCP("mac-buddy", log_level="WARNING")


class ToolError(Exception):
    pass


@dataclass
class Win:
    wid: str
    app: ax.App
    el: object
    ids: S.IdMap
    last: S.Snapshot | None = None
    noted: bool = False
    pending_note: str = ""
    # disk state of the document just before the most recent action that changed the window
    disk_before_edit: tuple | None = None
    edited_at: float = 0.0


@dataclass
class State:
    windows: dict[str, Win] = field(default_factory=dict)
    n: int = 0
    noted_apps: set = field(default_factory=set)


ST = State()


# ------------------------------------------------------------ window registry

def _register(app: ax.App, el) -> Win:
    for w in ST.windows.values():
        if w.app.pid == app.pid and ax.same(w.el, el):
            return w
    ST.n += 1
    wid = f"w{ST.n}"
    w = Win(wid, app, el, S.IdMap(""))
    ST.windows[wid] = w
    return w


def _win(wid: str) -> Win:
    w = ST.windows.get(wid)
    if w is None:
        raise ToolError(f"unknown window {wid!r}; call observe(app) first. Known: {sorted(ST.windows)}")
    if not ax.app_is_running(w.app.pid):
        raise ToolError(f"WINDOW GONE: {w.app.name} (pid {w.app.pid}) is no longer running. "
                        "Nothing was done. Call list_apps / observe again.")
    current = ax.windows(w.app)
    if not any(ax.same(w.el, c) for c in current) or not ax.alive(w.el):
        others = ", ".join(repr(str(ax.get(c, "AXTitle") or "")) for c in current) or "none"
        raise ToolError(f"WINDOW GONE: window {wid} ({w.last.window_title if w.last else '?'}) "
                        f"has closed. Nothing was done. {w.app.name} windows now: {others}. "
                        "Call observe again before doing anything else.")
    return w


def _snap(w: Win) -> S.Snapshot:
    try:
        s = S.take(w.el, w.app.el)
    except ax.AXError as e:
        raise ToolError(f"WINDOW GONE while reading it ({e}). Nothing was done; observe again.")
    w.ids.assign(s)
    return s


def _header(w: Win, s: S.Snapshot) -> str:
    fp, fname = front_app()
    doc = s.document or "none"
    return (f"WINDOW {w.wid} · app {w.app.name} (pid {w.app.pid}) · title {s.window_title!r} · "
            f"document file: {doc}\n"
            f"Person's front app: {fname!r}" + (" (this IS the target app)" if fp == w.app.pid else
                                               " — you are working in the background; never bring the target forward"))


def _guard_fresh(w: Win, targets: list[str] | None = None) -> S.Snapshot:
    """Refuse to act on a window that changed since the model last saw it, if
    the change could affect this action: elements appeared or disappeared,
    the title/document changed, or the target (or anything above/below it, or
    the focused element when there is no target) changed. Unrelated value
    changes elsewhere (a clock, a 'GB available' status line) are passed on
    to the model with the result instead of blocking it."""
    if w.last is None:
        raise ToolError(f"you have not observed {w.wid} yet; call observe first")
    s = _snap(w)
    if s.signature() == w.last.signature():
        return s
    old = w.last
    changes = S.diff(old, s)
    structural = any(l.startswith(("+", "-", "~ window")) for l in changes)
    tkeys = [old.by_eid[t].key for t in (targets or []) if t in old.by_eid]
    touched = []
    for n in s.nodes:
        o = old.by_eid.get(n.eid)
        if o is None or not n.rendered or not o.rendered or (o.sig() == n.sig() and o.focused == n.focused):
            continue
        if tkeys:
            if any(n.key == k or n.key.startswith(k + "/") or k.startswith(n.key + "/") for k in tkeys):
                touched.append(n.eid)
        elif n.focused or o.focused:
            touched.append(n.eid)
    w.last = s
    if structural or touched:
        raise ToolError(
            "STALE: the window changed since your last look, so the action was NOT performed.\n"
            "Changes since your last look:\n  " + ("\n  ".join(changes) or "(attributes changed)") +
            "\nYour view is now up to date with these changes. Re-check that your target is still "
            "right, then retry with the current ids (observe if unsure).")
    w.pending_note = ("NOTE: changed since your last look, elsewhere in the window (did not block this action):\n  "
                      + "\n  ".join(changes))
    return s


def _node(w: Win, snap: S.Snapshot, eid: str) -> S.Node:
    if eid not in w.ids.by_key:
        raise ToolError(f"unknown element id {eid!r} for {w.wid}")
    n = snap.by_eid.get(eid)
    if n is None or not n.rendered:
        raise ToolError(f"ELEMENT GONE: {eid} is no longer in {w.wid}. Nothing was done; observe again.")
    if not ax.alive(n.ref):
        raise ToolError(f"ELEMENT GONE: {eid} was destroyed. Nothing was done; observe again.")
    return n


def _settle(w: Win, before: S.Snapshot, max_wait: float = 3.0, quiet: float = 1.2):
    """Poll until the window stops changing. Returns (snapshot|None if closed, changed)."""
    t0 = time.time()
    prev_sig = before.signature()
    last = before
    changed_at = None
    stable = 0
    while time.time() - t0 < max_wait:
        time.sleep(0.12)
        if not ax.alive(w.el) or not any(ax.same(w.el, c) for c in ax.windows(w.app)):
            return None, True
        try:
            s = S.take(w.el, w.app.el)
        except ax.AXError:
            return None, True
        w.ids.assign(s)
        sig = s.signature()
        if sig != prev_sig:
            changed_at = time.time()
            stable = 0
            prev_sig = sig
        else:
            stable += 1
        last = s
        if changed_at is None and time.time() - t0 > quiet:
            break
        if changed_at is not None and stable >= 2:
            break
    w.ids.assign(last)
    return last, changed_at is not None


def _act(w: Win, what: str, fn, synth: bool = True, settle_max: float = 3.0) -> str:
    before = w.last
    app_wins_before = ax.windows(w.app)
    doc_before = before.document
    file_before = S.file_state(doc_before)
    lines = [w.pending_note] if w.pending_note else []
    w.pending_note = ""
    with FocusMonitor() as mon:
        if synth:
            ok, msg = synthfocus.focus_window(w.app.pid, w.el)
            if not ok:
                raise ToolError(f"could not give {w.wid} synthetic focus ({msg}); nothing was done")
        try:
            res = fn()
            lines.append(f"ACTION {what}: {res or 'accepted by the app'}")
        except ax.AXError as e:
            if e.code == ax.ERR_CANNOT_COMPLETE:
                lines.append(f"ACTION {what}: sent, but the app did not answer in time "
                             "(it may be showing a modal dialog). Check the changes below.")
            else:
                raise ToolError(f"ACTION {what} FAILED: {e}. Nothing changed as far as the server knows.")
        after, changed = _settle(w, before, max_wait=settle_max)
    rep = mon.report()
    lines.append(rep.line(target_pid=w.app.pid))
    # windows of the app that appeared / vanished
    app_wins_after = ax.windows(w.app)
    for c in app_wins_after:
        if not any(ax.same(c, b) for b in app_wins_before) and \
                (ax.get(c, "AXSubrole") or ax.get(c, "AXTitle")):
            nw = _register(w.app, c)
            lines.append(f"+ NEW WINDOW {nw.wid}: {str(ax.get(c, 'AXTitle') or '')!r} "
                         f"({ax.get(c, 'AXSubrole')}) — observe({w.app.name!r}, window={nw.wid!r}) to see it")
    if after is None:
        lines.append(f"- WINDOW {w.wid} CLOSED as a result of this action. Its element ids are void.")
        w.last = None
        return "\n".join(lines)
    diff = S.diff(before, after)
    if diff and doc_before and file_before and file_before[0] != "unreadable":
        w.disk_before_edit, w.edited_at = file_before, time.time()
    if diff:
        lines.append(f"CHANGES in {w.wid}:")
        lines.extend("  " + d for d in diff)
    else:
        lines.append(f"NO CHANGE in {w.wid} within {settle_max:.0f}s. The app accepted the request but "
                     "nothing visible happened — do NOT assume it worked.")
    for path in {doc_before, after.document} - {None}:
        fb = file_before if path == doc_before else None
        fa = S.file_state(path)
        if fa and fa[0] == "unreadable":
            lines.append(f"DISK: {path} can't be checked (macOS protects it, e.g. inside another "
                         "app's container) — saved state is UNKNOWN, not unchanged")
        elif fa and fa[0] == "missing":
            lines.append(f"DISK: {path} does not exist")
        elif fa and fa != fb:
            lines.append(f"DISK: {path} was written (size {fa[1]} bytes, {time.time() - fa[0]:.1f}s ago)")
        elif fa:
            lines.append(f"DISK: {path} unchanged on disk")
    w.last = after
    return "\n".join(lines)


class SessionWatch:
    """Watches the front app and app launches for the whole session, between
    actions too. An action can have a delayed effect -- e.g. Finder opening a
    file launches its app, which comes forward seconds later, after that
    action's own focus window has closed."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.events: list[str] = []
        self.t0 = time.time()
        self.front = front_app()
        self.pids = set(ax._live_pids())
        self.started = False
        self.expected: set[str] = set()  # app names started on purpose by launch_app

    def start(self):
        if self.started:
            return
        import threading
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        tick = 0
        while True:
            f = front_app()
            if f != self.front:
                targets = {w.app.pid for w in ST.windows.values()}
                kind, why = attribute(f, person_input_age(), targets, f[0] not in self.pids)
                with self.lock:
                    self.events.append(("front", kind, f"{self.front[1]!r} → {f[1]!r}", why))
                self.front = f
            tick += 1
            if tick % 25 == 0:
                now = set(ax._live_pids())
                for pid in now - self.pids:
                    from AppKit import NSRunningApplication
                    ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                    if ra is not None and ra.activationPolicy() == 0 and \
                            ra.localizedName() not in self.expected:
                        with self.lock:
                            self.events.append(("launch", ra.localizedName()))
                self.pids = now
            time.sleep(0.01)

    def drain(self) -> list[str]:
        with self.lock:
            ev, self.events = self.events, []
        return ev


WATCH = SessionWatch()


def _tool(fn):
    """Turn ToolErrors into plain text results the model can read, and report
    focus changes / app launches that happened since the previous call."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        WATCH.start()
        try:
            out = fn(*a, **kw)
        except ToolError as e:
            out = f"ERROR: {e}"
        except ax.AXError as e:
            out = f"ERROR: {e}"
        ev = WATCH.drain()
        person = [e for e in ev if e[0] == "front" and e[1] == "person"]
        suspect = [e for e in ev if e[0] == "front" and e[1] != "person"]
        launches = [e[1] for e in ev if e[0] == "launch"]
        if suspect:
            out += ("\nFOCUS MOVED (seen by the session watch, since your previous call): "
                    + "; ".join(f"front app {m} ({why})" for _, _, m, why in suspect)
                    + ". If your actions caused this (e.g. something was opened), stop and report it.")
        if person:
            out += ("\nNOTE: the person switched apps themselves since your previous call: "
                    + "; ".join(f"{m} ({why})" for _, _, m, why in person)
                    + ". Not caused by you; keep working in the background.")
        if launches:
            out += "\nAPPS LAUNCHED since your previous call: " + "; ".join(launches)
        return out
    return wrapper


_PANEL_IDS = {"saveAsNameTextField", "PathTextField"}


def _key_pid(w: Win) -> tuple[int, str]:
    """Where keystrokes for this window must go. Open/save panels (and their
    Go-to-folder sheet) are drawn by a separate per-app XPC service process,
    so keys posted to the app itself never reach them."""
    try:
        snap = S.take(w.el, w.app.el)
    except ax.AXError:
        return w.app.pid, w.app.name
    in_panel = any(n.ident in _PANEL_IDS for n in snap.nodes) or \
        any(n.role == "AXSheet" and (n.title or n.desc or "").lower() in ("save", "open") for n in snap.nodes)
    if not in_panel:
        return w.app.pid, w.app.name
    import subprocess
    from AppKit import NSRunningApplication
    pids = subprocess.run(["pgrep", "-f", "openAndSavePanelService"], capture_output=True, text=True).stdout.split()
    for p in pids:
        ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(p))
        if ra and f"({w.app.name})" in str(ra.localizedName()):
            return int(p), f"{w.app.name}'s save/open panel service"
    return w.app.pid, w.app.name


# -------------------------------------------------------------------- tools

@mcp.tool()
@_tool
def list_apps() -> str:
    """List running GUI apps and their windows, and which app the person has in front."""
    if not ax.trusted():
        return "ERROR: this process lacks Accessibility permission (System Settings › Privacy & Security › Accessibility)."
    fp, fname = front_app()
    out = [f"Person's front app: {fname} (pid {fp}). Work in the background; do not activate apps."]
    for a in ax.running_apps():
        titles = [str(ax.get(w, "AXTitle") or "") for w in ax.windows(a)]
        out.append(f"- {a.name} [{a.bundle_id}] pid {a.pid}: " +
                   (", ".join(repr(t) for t in titles) if titles else "no windows"))
    return "\n".join(out)


@mcp.tool()
@_tool
def observe(app: str, window: str | None = None) -> str:
    """Observe one window of an app (by app name or bundle id). `window` may be a
    window id from an earlier result (e.g. "w2") or part of a window title; default
    is the app's main window. Returns the window's element tree with ids like e12.
    Ids stay the same for the same element across observations."""
    if window and window in ST.windows:
        w = _win(window)
    else:
        a = ax.find_app(app)
        if a is None:
            raise ToolError(f"{app!r} is not running. If the task needs it, call launch_app({app!r}) "
                            "to start it in the background. Running: " +
                            ", ".join(x.name for x in ax.running_apps()))
        wins = ax.windows(a)
        if not wins:
            raise ToolError(f"{a.name} has no open windows. If the task needs a new document, "
                            f"call new_document({a.name!r}).")
        el = None
        if window:
            for c in wins:
                if window.lower() in str(ax.get(c, "AXTitle") or "").lower():
                    el = c
                    break
            if el is None:
                raise ToolError(f"no {a.name} window matching {window!r}; windows: " +
                                ", ".join(repr(str(ax.get(c, 'AXTitle') or '')) for c in wins))
        else:
            main = ax.get(a.el, "AXMainWindow")
            el = next((c for c in wins if ax.same(c, main)), wins[0])
        w = _register(a, el)
    s = _snap(w)
    w.last = s
    parts = [_header(w, s)]
    if w.app.bundle_id not in ST.noted_apps and w.app.bundle_id in APP_NOTES:
        ST.noted_apps.add(w.app.bundle_id)
        parts.append(f"NOTE for {w.app.name}: {APP_NOTES[w.app.bundle_id]}")
    others = [c for c in ax.windows(w.app) if not ax.same(c, w.el)]
    if others:
        parts.append("Other windows of this app: " + ", ".join(
            f"{_register(w.app, c).wid}={str(ax.get(c, 'AXTitle') or '')!r}" for c in others))
    parts.append("TREE:\n" + S.render(s))
    return "\n".join(parts)


@mcp.tool()
@_tool
def click(window: str, element: str, action: str | None = None) -> str:
    """Press/activate an element without moving the mouse (AX action). `action`
    optionally names a specific AX action listed for it (e.g. AXShowMenu)."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    acts = ax.action_names(n.ref)
    desc = f'click {element} {S._short_role(n)} "{n.label()}"'

    def do():
        if action:
            if action not in acts:
                raise ToolError(f"{element} has no action {action}; it has {acts}")
            ax.perform(n.ref, action)
            return f"performed {action}"
        if n.role == "AXRow" and w.app.bundle_id == "com.apple.finder" and "| Folder" not in (n.title or ""):
            acts_ = [a for a in acts if a != "AXOpen"]
        else:
            acts_ = acts
        for a in ("AXPress", "AXConfirm", "AXPick", "AXOpen"):
            if a not in acts_:
                continue
            if a in acts:
                ax.perform(n.ref, a)
                return f"performed {a}"
        if ax.settable(n.ref, "AXSelected"):
            ax.set_attr(n.ref, "AXSelected", True)
            return "set AXSelected"
        if ax.settable(n.ref, "AXFocused"):
            ax.set_attr(n.ref, "AXFocused", True)
            return "focused it (no press action)"
        raise ToolError(f"{element} cannot be pressed without a real mouse click (actions: {acts}); "
                        "nothing was done. Look for a menu item or keyboard route instead.")
    return _act(w, desc, do)


@mcp.tool()
@_tool
def set_value(window: str, element: str, value: str) -> str:
    """Replace the whole value of a text field / text area (or set a slider etc.)."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    if not ax.settable(n.ref, "AXValue"):
        raise ToolError(f"{element}'s value is not settable; nothing was done. Try type_text.")

    def do():
        ax.set_attr(n.ref, "AXValue", value)
        got = ax.get(n.ref, "AXValue")
        if str(got) != value:
            return f"set, but READ-BACK DIFFERS: now {S._q(str(got), 200)}"
        return "set; read-back matches"
    return _act(w, f"set_value {element}", do)


@mcp.tool()
@_tool
def replace_text(window: str, element: str, find: str, replace: str, occurrence: int = 1) -> str:
    """Replace one occurrence (1-based) of `find` inside a text element with
    `replace`, leaving the rest of the text untouched. Fails if not found."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    text = str(ax.get(n.ref, "AXValue") or "")
    idx = -1
    for _ in range(occurrence):
        idx = text.find(find, idx + 1)
        if idx < 0:
            raise ToolError(f"{find!r} occurrence {occurrence} not found in {element}; nothing was done")
    count = text.count(find)

    def do():
        ax.set_attr(n.ref, "AXSelectedTextRange", ax.make_range(idx, len(find)))
        ax.set_attr(n.ref, "AXSelectedText", replace)
        got = str(ax.get(n.ref, "AXValue") or "")
        expect = text[:idx] + replace + text[idx + len(find):]
        if got != expect:
            return "replaced, but READ-BACK DIFFERS from the expected text — inspect the changes"
        return f"replaced occurrence {occurrence} of {count}; read-back matches"
    return _act(w, f"replace_text {element}", do)


@mcp.tool()
@_tool
def insert_text(window: str, element: str, text: str, position: str = "end") -> str:
    """Insert text into a text element at position "end", "start", "selection"
    (replacing the current selection) or a character offset like "120"."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    cur = str(ax.get(n.ref, "AXValue") or "")
    if position == "end":
        loc, length = len(cur), 0
    elif position == "start":
        loc, length = 0, 0
    elif position == "selection":
        loc, length = ax.cfrange(ax.get(n.ref, "AXSelectedTextRange")) or (len(cur), 0)
    else:
        try:
            loc, length = int(position), 0
        except ValueError:
            raise ToolError("position must be end, start, selection or an integer offset")
    if loc > len(cur):
        raise ToolError(f"offset {loc} beyond text length {len(cur)}")

    def do():
        ax.set_attr(n.ref, "AXSelectedTextRange", ax.make_range(loc, length))
        ax.set_attr(n.ref, "AXSelectedText", text)
        got = str(ax.get(n.ref, "AXValue") or "")
        if got != cur[:loc] + text + cur[loc + length:]:
            return "inserted, but READ-BACK DIFFERS from expected — inspect the changes"
        return "inserted; read-back matches"
    return _act(w, f"insert_text {element} at {position}", do)


@mcp.tool()
@_tool
def read_text(window: str, element: str, start: int = 0, length: int = 4000) -> str:
    """Read part of a long text value (the tree truncates long text)."""
    w = _win(window)
    snap = _snap(w)
    n = _node(w, snap, element)
    text = str(ax.get(n.ref, "AXValue") or "")
    chunk = text[start:start + length]
    return f"{element} text length {len(text)}; chars {start}..{start + len(chunk)}:\n{chunk}"


@mcp.tool()
def harness_begin_task() -> str:
    """(Harness only, not offered to the model.) Start a new request: forget focus
    changes and launches from before it, e.g. the person switching apps while chatting."""
    WATCH.start()
    WATCH.drain()
    return "ok"


def _frame(el) -> dict | None:
    pos, size = ax.get(el, "AXPosition"), ax.get(el, "AXSize")
    if pos is None or size is None:
        return None
    try:
        import ApplicationServices as AS
        ok, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        ok2, z = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        if not (ok and ok2):
            return None
        return {"x": p.x, "y": p.y, "w": z.width, "h": z.height}
    except Exception:
        return None


@mcp.tool()
def harness_element_frame(window: str, element: str) -> str:
    """(Harness only.) Screen frame of an element, top-left origin, as JSON; {} if unknown."""
    import json
    try:
        w = _win(window)
        snap = _snap(w)
        n = _node(w, snap, element)
        return json.dumps(_frame(n.ref) or {})
    except (ToolError, ax.AXError):
        return "{}"


@mcp.tool()
def harness_menu_frame(window: str, title: str) -> str:
    """(Harness only.) Screen frame of a menu-bar title (e.g. "File") of the window's app."""
    import json
    try:
        w = _win(window)
        for top in _menu_children(_menu_root(w)):
            if _norm(str(ax.get(top, "AXTitle") or "")) == _norm(title):
                return json.dumps(_frame(top) or {})
    except (ToolError, ax.AXError):
        pass
    return "{}"


@mcp.tool()
def harness_window_frame(window: str) -> str:
    """(Harness only.) Screen frame of a window."""
    import json
    try:
        return json.dumps(_frame(_win(window).el) or {})
    except (ToolError, ax.AXError):
        return "{}"


@mcp.tool()
@_tool
def launch_app(app: str, document: str | None = None) -> str:
    """Start an app that isn't running, in the BACKGROUND (the person's front app
    and cursor stay as they are), optionally opening one document the task names.
    Returns the app's windows once it is ready. Only launch what the task needs."""
    import subprocess
    if document:
        document = os.path.expanduser(document)
        if not os.path.exists(document):
            raise ToolError(f"document {document!r} does not exist. Nothing was launched.")
    running = ax.find_app(app)
    if running is not None and not document:
        wins = [str(ax.get(w, "AXTitle") or "") for w in ax.windows(running)]
        return f"{running.name} is already running; windows: {wins}. Use observe({running.name!r})."
    t0 = time.time()
    with FocusMonitor() as mon:
        # -g: don't bring it forward. The app's name is reserved first so the
        # session watch doesn't report this launch as a surprise.
        WATCH.expected.add(app if running is None else running.name)
        cmd = ["open", "-g", "-a", app] + ([document] if document else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise ToolError(f"could not launch {app!r}: {(r.stderr or r.stdout).strip()}. Nothing was launched.")
        a = None
        while time.time() - t0 < 15 and a is None:
            a = ax.find_app(app)
            time.sleep(0.1)
        if a is None:
            raise ToolError(f"{app!r} did not start within 15 s.")
        WATCH.expected.add(a.name)
        wins = []
        while time.time() - t0 < 15:
            wins = ax.windows(a)
            if wins and (not document or any(os.path.basename(document) in str(ax.get(w, "AXTitle") or "")
                                             for w in wins)):
                break
            time.sleep(0.15)
        time.sleep(1.5)  # some apps activate themselves a moment after their window appears
    lines = [f"LAUNCHED {a.name} (pid {a.pid}) in the background in {time.time() - t0:.1f}s"
             + (f", opening {document}" if document else ""), mon.report().line(a.pid)]
    titles = [str(ax.get(w, "AXTitle") or "") for w in ax.windows(a)]
    lines.append(f"windows: {titles}" if titles else "it has no windows yet")
    if any(t == "Open" for t in titles):
        lines.append("An Open panel is showing (the app starts this way with no document). "
                     "If the task needs a new document, call new_document — it replaces the panel.")
    lines.append(f"Next: observe({a.name!r}).")
    return "\n".join(lines)


@mcp.tool()
@_tool
def new_document(app: str) -> str:
    """Create a new, empty document in a running app via its File ▸ New menu item,
    in the background. Works even when the app has no windows. Use it when the task
    needs a document and none is open (and the task names no file)."""
    a = ax.find_app(app)
    if a is None:
        raise ToolError(f"{app!r} is not running; call launch_app({app!r}) first.")
    item = None
    for top in ax.get(ax.get(a.el, "AXMenuBar"), "AXChildren") or []:
        if ax.get(top, "AXTitle") != "File":
            continue
        for m in ax.get(top, "AXChildren") or []:
            for it in ax.get(m, "AXChildren") or []:
                t = str(ax.get(it, "AXTitle") or "")
                # the plain cmd+N item: "New", "New Document", "New Note", …
                if t.startswith("New") and str(ax.get(it, "AXMenuItemCmdChar") or "").upper() == "N" \
                        and (ax.get(it, "AXMenuItemCmdModifiers") or 0) == 0:
                    item = it
                    break
    if item is None or "window" in str(ax.get(item, "AXTitle") or "").lower() or \
            a.bundle_id == "com.apple.finder":
        # Finder's cmd+N is "New Finder Window", which brings Finder to the front.
        raise ToolError(f"{a.name} has no File ▸ New document item (only document apps are "
                        "supported). Nothing was done.")
    if ax.get(item, "AXEnabled") is False:
        raise ToolError(f"{a.name}'s File ▸ {ax.get(item, 'AXTitle')} is disabled. Nothing was done.")
    before = list(ax.windows(a))
    with FocusMonitor() as mon:
        wins = before
        if before:  # menus are validated against the key window
            synthfocus.focus_window(a.pid, before[0])
            time.sleep(0.05)
        ax.perform(item, "AXPress")
        t0 = time.time()
        while time.time() - t0 < 5:
            wins = ax.windows(a)
            if any(not any(ax.same(w, b) for b in before) for w in wins):
                break
            time.sleep(0.1)
        time.sleep(0.3)
    lines = [f"ACTION new_document in {a.name}: chose File ▸ {ax.get(item, 'AXTitle')}",
             mon.report().line(a.pid)]
    new = [w for w in ax.windows(a) if not any(ax.same(w, b) for b in before)]
    if not new:
        lines.append("NO NEW WINDOW appeared within 5s — do NOT assume a document was created.")
    for w in new:
        reg = _register(a, w)
        lines.append(f"+ NEW WINDOW {reg.wid}: {str(ax.get(w, 'AXTitle') or '')!r} — "
                     f"observe({a.name!r}, window={reg.wid!r}) to see it")
    for b in before:
        if not any(ax.same(b, w) for w in ax.windows(a)):
            lines.append(f"- a window closed: {str(ax.get(b, 'AXTitle') or '')!r}")
    return "\n".join(lines)


@mcp.tool()
@_tool
def wait_for_save(window: str, seconds: float = 10) -> str:
    """Wait until the window's document file is written to disk after your edits.
    Use it for apps that save on their own (no Save command), or to confirm a save."""
    w = _win(window)
    snap = _snap(w)
    path = snap.document
    if not path:
        return f"{window} has no document file, so a save can't be checked on disk."
    base = w.disk_before_edit
    if base is None:
        return f"No edit recorded in {window} since it was first observed; nothing to wait for."
    t0 = time.time()
    while True:
        cur = S.file_state(path)
        if cur and cur[0] == "unreadable":
            return (f"DISK: {path} can't be checked (macOS protects it) — saved state is UNKNOWN.")
        if cur and cur[0] != "missing" and cur != base:
            w.disk_before_edit = None
            return (f"SAVED: {path} was written {time.time() - cur[0]:.1f}s ago "
                    f"(size {cur[1]} bytes), {cur[0] - w.edited_at:+.1f}s after your last edit.")
        if time.time() - t0 >= min(seconds, 30):
            return (f"NOT SAVED: {path} has not changed on disk in {time.time() - t0:.0f}s "
                    "since your last edit. Do NOT report it as saved.")
        time.sleep(0.25)


_OPEN_KEYS = {"cmd+down", "cmd+o", "command+down", "command+o"}


def _guard_finder_open(w: Win, snap: S.Snapshot, keys: str):
    """In Finder, 'open' on a selected file launches the file's app IN FRONT
    of the person. Only allow it when every selected row is a folder."""
    if w.app.bundle_id != "com.apple.finder" or keys.replace(" ", "").lower() not in _OPEN_KEYS:
        return
    sel = [n for n in snap.nodes if n.role == "AXRow" and n.selected and n.rendered]
    files = [n for n in sel if "| Folder" not in (n.title or "")]
    if files:
        raise ToolError(
            f"REFUSED: {keys} would OPEN the selected item(s) {[ (n.title or '').split(' | ')[0] for n in files][:6]} "
            "in their default app, which launches in front of the person. Nothing was done. Select only "
            "the folder you want to enter (select_rows with that one row) and try again.")


@mcp.tool()
@_tool
def press_key(window: str, keys: str, element: str | None = None) -> str:
    """Send a key or shortcut (e.g. "return", "escape", "cmd+s", "cmd+shift+g",
    "down") to the window's app only, via synthetic focus. If `element` is given it
    gets keyboard focus inside the app first. The person's keyboard is unaffected."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element) if element else None
    ax.parse_combo(keys)
    _guard_finder_open(w, snap, keys)

    def do():
        if n is not None and ax.settable(n.ref, "AXFocused"):
            ax.set_attr(n.ref, "AXFocused", True)
        time.sleep(0.05)
        pid, who = _key_pid(w)
        ax.post_key_to_pid(pid, keys)
        return f"key posted to {who} only"
    return _act(w, f"press_key {keys}" + (f" on {element}" if element else ""), do)


@mcp.tool()
@_tool
def type_text(window: str, text: str, element: str | None = None) -> str:
    """Type characters as key events into the app (for fields whose value cannot be
    set directly). Prefer set_value / replace_text / insert_text when possible."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element) if element else None

    def do():
        if n is not None and ax.settable(n.ref, "AXFocused"):
            ax.set_attr(n.ref, "AXFocused", True)
        time.sleep(0.05)
        pid, who = _key_pid(w)
        ax.post_text_to_pid(pid, text)
        return f"typed {len(text)} chars into {who}"
    return _act(w, f"type_text {S._q(text, 60)}", do)


def _menu_root(w: Win):
    mb = ax.get(w.app.el, "AXMenuBar")
    if mb is None:
        raise ToolError(f"{w.app.name} has no menu bar")
    return mb


def _norm(t: str) -> str:
    return t.strip().rstrip("…").rstrip(".").strip().lower()


def _menu_children(el) -> list:
    kids = ax.get(el, "AXChildren") or []
    if ax.get(el, "AXRole") in ("AXMenuBarItem", "AXMenuItem") and kids:
        return list(ax.get(kids[0], "AXChildren") or [])
    return list(kids)


def _walk_menu(w: Win, path: list[str]):
    el = _menu_root(w)
    for part in path:
        kids = _menu_children(el)
        match = next((k for k in kids if str(ax.get(k, "AXTitle") or "") == part), None) or \
            next((k for k in kids if _norm(str(ax.get(k, "AXTitle") or "")) == _norm(part)), None)
        if match is None:
            titles = [str(ax.get(k, "AXTitle") or "") for k in kids if ax.get(k, "AXTitle")]
            raise ToolError(f"menu item {part!r} not found under {path[:path.index(part)] or 'menu bar'}; "
                            f"available: {titles}")
        el = match
    return el


def _mods(m) -> str:
    m = int(m or 0)
    parts = [n for bit, n in ((4, "ctrl"), (2, "opt"), (1, "shift")) if m & bit]
    if not m & 8:
        parts.append("cmd")
    return "+".join(parts)


@mcp.tool()
@_tool
def menu(window: str, path: list[str] | None = None) -> str:
    """List the menu items of the window's app at `path` (e.g. [] for the menu bar,
    ["File"] for the File menu), with enabled state as they apply to that window."""
    w = _win(window)
    synthfocus.focus_window(w.app.pid, w.el)
    time.sleep(0.05)
    el = _walk_menu(w, path or [])
    out = []
    for k in _menu_children(el):
        t = str(ax.get(k, "AXTitle") or "")
        if not t:
            continue
        en = ax.get(k, "AXEnabled")
        ch = ax.get(k, "AXMenuItemCmdChar")
        mods = ax.get(k, "AXMenuItemCmdModifiers")
        sc = f"  [{_mods(mods)}+{str(ch).lower()}]" if ch and str(ch).isprintable() and len(str(ch)) == 1 else ""
        sub = " ▸" if ax.get(k, "AXChildren") and ax.get(k, "AXRole") == "AXMenuItem" else ""
        out.append(f"- {t}{sub}{sc}" + ("" if en is not False else " (disabled)"))
    return f"menu {path or []} of {w.app.name} for {w.wid}:\n" + "\n".join(out)


@mcp.tool()
@_tool
def select_menu(window: str, path: list[str]) -> str:
    """Choose a menu item for this window, e.g. ["File", "Save…"] or
    ["Format", "Make Plain Text"]. Refuses disabled items."""
    w = _win(window)
    _guard_fresh(w)
    synthfocus.focus_window(w.app.pid, w.el)
    time.sleep(0.05)
    item = _walk_menu(w, path)
    if w.app.bundle_id == "com.apple.finder" and path and _norm(path[-1]).startswith("open") \
            and "new" not in _norm(path[-1]):
        _guard_finder_open(w, w.last, "cmd+o")
    if ax.get(item, "AXEnabled") is False:
        raise ToolError(f"menu item {' ▸ '.join(path)} is DISABLED for {w.wid}; nothing was done")
    return _act(w, f"select_menu {' ▸ '.join(path)}", lambda: ax.perform(item, "AXPress"))


@mcp.tool()
@_tool
def scroll(window: str, element: str, direction: str = "down", pages: int = 1) -> str:
    """Scroll a scroll area (or the scroll area containing `element`) by pages:
    direction up/down/left/right. Use scroll_to to bring one element into view."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    el = n.ref
    for _ in range(12):
        if ax.get(el, "AXRole") == "AXScrollArea":
            break
        el = ax.get(el, "AXParent")
        if el is None:
            raise ToolError(f"{element} is not inside a scroll area")
    act = {"up": "AXScrollUpByPage", "down": "AXScrollDownByPage",
           "left": "AXScrollLeftByPage", "right": "AXScrollRightByPage"}.get(direction)
    if act is None:
        raise ToolError("direction must be up/down/left/right")

    def do():
        if act in ax.action_names(el):
            for _ in range(max(1, pages)):
                ax.perform(el, act)
            return f"{act} x{pages}"
        bar = ax.get(el, "AXVerticalScrollBar" if direction in ("up", "down") else "AXHorizontalScrollBar")
        if bar is None:
            raise ToolError("nothing to scroll")
        v = float(ax.get(bar, "AXValue") or 0)
        v = min(1.0, max(0.0, v + (0.25 * pages if direction in ("down", "right") else -0.25 * pages)))
        ax.set_attr(bar, "AXValue", v)
        return f"scroll bar → {v:.2f}"
    return _act(w, f"scroll {direction} {pages}", do, synth=False)


@mcp.tool()
@_tool
def scroll_to(window: str, element: str) -> str:
    """Scroll so that an element is visible."""
    w = _win(window)
    snap = _guard_fresh(w, [element])
    n = _node(w, snap, element)
    return _act(w, f"scroll_to {element}", lambda: ax.perform(n.ref, "AXScrollToVisible"), synth=False)


@mcp.tool()
@_tool
def select_rows(window: str, elements: list[str]) -> str:
    """Select exactly these rows (ids of row elements in one table/outline/list),
    replacing the current selection. Use for multi-selection."""
    w = _win(window)
    snap = _guard_fresh(w, list(elements))
    nodes = [_node(w, snap, e) for e in elements]
    if not nodes:
        raise ToolError("no rows given")
    parent = ax.get(nodes[0].ref, "AXParent")
    for n in nodes[1:]:
        if not ax.same(ax.get(n.ref, "AXParent"), parent):
            raise ToolError("rows are not all in the same table/list")

    def do():
        if ax.settable(parent, "AXSelectedRows"):
            ax.set_attr(parent, "AXSelectedRows", [n.ref for n in nodes])
        elif all(ax.settable(n.ref, "AXSelected") for n in nodes):
            for r in ax.get(parent, "AXSelectedRows") or []:
                ax.set_attr(r, "AXSelected", False)
            for n in nodes:
                ax.set_attr(n.ref, "AXSelected", True)
        else:
            raise ToolError("this list does not allow setting its selection via AX")
        sel = ax.get(parent, "AXSelectedRows") or []
        ok = len(sel) == len(nodes) and all(any(ax.same(s, n.ref) for s in sel) for n in nodes)
        return f"selected {len(sel)} rows" + ("; read-back matches" if ok else "; READ-BACK DIFFERS")
    return _act(w, f"select_rows {elements}", do)


def main():
    if not ax.trusted():
        import sys
        print("WARNING: no Accessibility permission for this process", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
