"""Window snapshots, compact rendering for the model, and snapshot diffs."""
from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from dataclasses import dataclass, field

import Quartz

from . import ax

ATTRS = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue",
         "AXIdentifier", "AXEnabled", "AXFocused", "AXSelected", "AXChildren",
         "AXPlaceholderValue", "AXHelp"]

# Subtrees that are pure noise for the model.
SKIP_ROLES = {"AXRuler", "AXRulerMarker", "AXGrowArea", "AXMenuBar"}
# Containers we flatten (don't render, but walk) when they carry no label.
FLATTEN_ROLES = {"AXGroup", "AXSplitGroup", "AXLayoutArea", "AXLayoutItem",
                 "AXUnknown", "AXCell", "AXColumn", "AXScrollArea"}
TEXT_ROLES = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}
MAX_NODES = 2500
VOLATILE_VALUE_ROLES = {"AXProgressIndicator", "AXBusyIndicator", "AXValueIndicator"}
MAX_DEPTH = 30
TREE_TEXT_LIMIT = 1500
MAX_ROWS_WALKED = 300


@dataclass
class Node:
    key: str                 # structural path, stable while the UI doesn't change shape
    ref: object              # AXUIElementRef
    depth: int
    role: str
    subrole: str | None
    title: str | None
    desc: str | None
    value: str | None
    ident: str | None
    enabled: bool | None
    focused: bool | None
    selected: bool | None
    placeholder: str | None
    rendered: bool = True
    eid: str = ""

    def sig(self) -> tuple:
        # Animating indicators change on their own; they must not make the
        # window look "changed since your last look".
        v = None if self.role in VOLATILE_VALUE_ROLES else self.value
        return (self.role, self.subrole, self.title, self.desc, v,
                self.enabled, self.selected)

    def label(self) -> str:
        for v in (self.title, self.desc, self.placeholder):
            if v:
                return v
        return ""


@dataclass
class Snapshot:
    taken_at: float
    window_title: str
    document: str | None
    nodes: list[Node]
    by_key: dict[str, Node] = field(default_factory=dict)
    by_eid: dict[str, Node] = field(default_factory=dict)
    truncated: bool = False

    def signature(self) -> str:
        h = hashlib.sha1()
        h.update((self.window_title or "").encode())
        h.update((self.document or "").encode())
        for n in self.nodes:
            if n.rendered:
                h.update(repr((n.eid or n.key, n.sig())).encode())
        return h.hexdigest()


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


def _value(v) -> str | None:
    if v is None:
        return None
    # AXValue refs (points, ranges) are not useful as text
    try:
        import ApplicationServices as AS
        import Quartz
        tid = Quartz.CFGetTypeID(v)
        if tid in (AS.AXValueGetTypeID(), AS.AXUIElementGetTypeID()):
            return None
    except Exception:
        pass
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.3g}"
    if hasattr(v, "__len__") and not isinstance(v, str) and not hasattr(v, "lower"):
        return None  # arrays (e.g. AXValue of some lists) -> skip
    return str(v)


def document_path(win) -> str | None:
    doc = ax.get(win, "AXDocument")
    if not doc:
        return None
    s = str(doc)
    if s.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(s).path)
    return s


def take(win, app_el=None) -> Snapshot:
    """Walk one window's AX tree. Raises ax.AXError if the window is gone."""
    head = ax.get_many(win, ["AXRole", "AXTitle"])
    if head.get("AXRole") is None and not ax.alive(win):
        raise ax.AXError(ax.ERR_INVALID, "window")
    nodes: list[Node] = []
    truncated = False

    def walk(el, depth: int, key: str):
        nonlocal truncated
        if len(nodes) >= MAX_NODES or depth > MAX_DEPTH:
            truncated = True
            return
        try:
            a = ax.get_many(el, ATTRS)
        except ax.AXError:
            return
        role = _s(a["AXRole"]) or "AXUnknown"
        if role in SKIP_ROLES:
            return
        n = Node(key=key, ref=el, depth=depth, role=role, subrole=_s(a["AXSubrole"]),
                 title=_s(a["AXTitle"]), desc=_s(a["AXDescription"]),
                 value=_value(a["AXValue"]), ident=_s(a["AXIdentifier"]),
                 enabled=None if a["AXEnabled"] is None else bool(a["AXEnabled"]),
                 focused=None if a["AXFocused"] is None else bool(a["AXFocused"]),
                 selected=None if a["AXSelected"] is None else bool(a["AXSelected"]),
                 placeholder=_s(a["AXPlaceholderValue"]))
        if role in FLATTEN_ROLES and not n.label() and role != "AXScrollArea":
            n.rendered = False
        if role == "AXScrollArea":
            n.rendered = True
        if role == "AXStaticText" and not (n.value or n.title):
            n.rendered = False
        if role == "AXImage" and not n.label():
            n.rendered = False
        nodes.append(n)
        if role == "AXScrollBar":
            return
        kids = list(a["AXChildren"] or [])
        if role in ("AXTable", "AXOutline", "AXList") and len(kids) > MAX_ROWS_WALKED:
            n.value = f"{len(kids)} items; only the first {MAX_ROWS_WALKED} are listed (scroll / narrow the view)"
            kids = kids[:MAX_ROWS_WALKED]
        counts: dict[str, int] = {}
        for c in kids:
            r = ax.get(c, "AXRole") or "?"
            i = counts.get(r, 0)
            counts[r] = i + 1
            walk(c, depth + 1, f"{key}/{r}[{i}]")

    walk(win, 0, "win")
    _label_rows(nodes)
    if app_el is not None:
        # Some editors (e.g. Finder's inline rename field) are parented to the
        # application, not the window, so a window walk never sees them.
        f = ax.get(app_el, "AXFocusedUIElement")
        if f is not None and ax.get(f, "AXRole") in TEXT_ROLES and \
                not any(ax.same(f, n.ref) for n in nodes if n.role in TEXT_ROLES):
            before = len(nodes)
            walk(f, 1, "win/focused-editor")
            for n in nodes[before:]:
                n.rendered = True
                n.desc = (n.desc or "") + " [focused editor outside the window tree]"
    snap = Snapshot(time.time(), str(head.get("AXTitle") or ""), document_path(win),
                    nodes, truncated=truncated)
    snap.by_key = {n.key: n for n in nodes}
    return snap


def _label_rows(nodes: list[Node]):
    """Give rows a label from their cell text and hide the static text /
    images inside them, so a 60-row list renders as 60 lines."""
    for i, n in enumerate(nodes):
        if n.role != "AXRow" or n.label():
            continue
        texts = []
        prefix = n.key + "/"
        for m in nodes[i + 1:]:
            if not m.key.startswith(prefix):
                break
            if m.role == "AXStaticText" and (m.value or m.title):
                texts.append(m.value or m.title)
                m.rendered = False
            elif m.role == "AXTextField" and m.value and not m.enabled is False and not m.focused:
                texts.append(m.value)
                m.rendered = False
            elif m.role == "AXImage" or (m.role == "AXList" and m.enabled is False):
                m.rendered = False
        if texts:
            n.title = " | ".join(t for t in texts[:4])


class IdMap:
    """Per-window element ids keyed by AX element identity (CFEqual), not by
    tree position. The same element keeps its id across snapshots; a
    different element never inherits an old id, even if it sits at the same
    place in the tree (e.g. fields shifting when a panel expands)."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.next = 1
        self.buckets: dict[int, list] = {}   # CFHash -> [(ref, eid)]
        self.by_key: dict[str, str] = {}     # eid -> eid (kept for API compat)

    def _lookup(self, ref):
        for r, eid in self.buckets.get(Quartz.CFHash(ref), []):
            if Quartz.CFEqual(r, ref):
                return eid
        return None

    def assign(self, snap: "Snapshot"):
        for n in snap.nodes:
            eid = self._lookup(n.ref)
            if eid is None:
                eid = f"{self.prefix}e{self.next}"
                self.next += 1
                self.buckets.setdefault(Quartz.CFHash(n.ref), []).append((n.ref, eid))
                self.by_key[eid] = eid
            n.eid = eid
        snap.by_eid = {n.eid: n for n in snap.nodes}


def _short_role(n: Node) -> str:
    r = n.role[2:] if n.role.startswith("AX") else n.role
    sr = n.subrole[2:] if n.subrole and n.subrole.startswith("AX") else n.subrole
    if sr and sr not in ("StandardWindow",):
        return f"{r}:{sr}"
    return r


def _q(s: str, limit: int) -> str:
    s = s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    if len(s) > limit:
        return f'"{s[:limit]}…" (+{len(s) - limit} chars, use read_text)'
    return f'"{s}"'


def render_node(n: Node, text_limit: int = TREE_TEXT_LIMIT) -> str:
    parts = [f"[{n.eid}]", _short_role(n)]
    lab = n.label()
    if lab:
        parts.append(_q(lab, 120))
    if n.value is not None and n.value != lab:
        lim = text_limit if n.role in TEXT_ROLES else 200
        if n.role in ("AXCheckBox", "AXRadioButton", "AXMenuItem"):
            parts.append(f"value={n.value}")
        elif n.role == "AXScrollBar":
            parts.append(f"pos={n.value}")
        else:
            parts.append(f"value={_q(n.value, lim)}")
    if n.ident and n.role in ("AXButton", "AXTextField", "AXTextArea") and not lab:
        parts.append(f"id={n.ident}")
    flags = []
    if n.enabled is False:
        flags.append("disabled")
    if n.focused:
        flags.append("focused")
    if n.selected:
        flags.append("selected")
    if flags:
        parts.append("(" + ",".join(flags) + ")")
    return " ".join(parts)


def render(snap: Snapshot) -> str:
    lines = []
    # Indentation follows rendered ancestors only.
    stack: list[int] = []
    for n in snap.nodes:
        while stack and stack[-1] >= n.depth:
            stack.pop()
        if not n.rendered:
            continue
        lines.append("  " * len(stack) + render_node(n))
        stack.append(n.depth)
    if snap.truncated:
        lines.append(f"… tree truncated at {MAX_NODES} nodes")
    return "\n".join(lines)


def diff(old: Snapshot, new: Snapshot, max_lines: int = 60) -> list[str]:
    out: list[str] = []
    if old.window_title != new.window_title:
        out.append(f'~ window title: "{old.window_title}" → "{new.window_title}"')
    if old.document != new.document:
        out.append(f"~ window document: {old.document} → {new.document}")
    ok, nk = set(old.by_eid), set(new.by_eid)
    removed = [n for n in old.nodes if n.rendered and
               (n.eid not in nk or not new.by_eid[n.eid].rendered)]
    added = [n for n in new.nodes if n.rendered and
             (n.eid not in ok or not old.by_eid[n.eid].rendered)]
    changed = []
    for n in new.nodes:
        o = old.by_eid.get(n.eid)
        if o is None or not n.rendered or not o.rendered:
            continue
        if o.sig() != n.sig() or o.focused != n.focused:
            changed.append((o, n))

    def summarize_group(nodes: list[Node], sign: str):
        # Collapse big subtrees (e.g. a whole sheet) to their roots, and long
        # runs of sibling rows to a count.
        keys = {n.key for n in nodes}
        roots = [n for n in nodes if not any(n.key.startswith(k + "/") for k in keys if k != n.key)]
        runs: list[list[Node]] = []
        for r in roots:
            parent = r.key.rsplit("/", 1)[0]
            if runs and runs[-1][0].role == r.role and runs[-1][0].key.rsplit("/", 1)[0] == parent:
                runs[-1].append(r)
            else:
                runs.append([r])
        budget = 30
        for run in runs:
            show = run if len(run) <= 6 else run[:3]
            for r in show:
                sub = [n for n in nodes if n.key == r.key or n.key.startswith(r.key + "/")]
                if sign == "-":
                    out.append(f"- {render_node(r, 200)}" + (f"  (+{len(sub) - 1} descendants)" if len(sub) > 1 else ""))
                else:
                    for n in sub[:budget]:
                        out.append(f"+ {render_node(n, 400)}")
                    if len(sub) > budget:
                        out.append(f"+ … {len(sub) - budget} more new elements under [{r.eid}] (observe to see all)")
            if len(run) > len(show):
                verb = "removed" if sign == "-" else "added"
                out.append(f"{sign} … and {len(run) - len(show)} more {_short_role(run[0])} elements {verb} alongside them")

    if removed:
        summarize_group(removed, "-")
    if added:
        summarize_group(added, "+")
    for o, n in changed:
        bits = []
        for name, a, b in (("title", o.title, n.title), ("desc", o.desc, n.desc),
                           ("enabled", o.enabled, n.enabled), ("selected", o.selected, n.selected),
                           ("focused", o.focused, n.focused)):
            if a != b:
                bits.append(f"{name}: {a!r} → {b!r}")
        if o.value != n.value:
            if n.role in TEXT_ROLES and (len(o.value or "") > 120 or len(n.value or "") > 120):
                bits.append("value: " + text_change(o.value or "", n.value or ""))
            else:
                bits.append(f"value: {_q(o.value or '', 120)} → {_q(n.value or '', 120)}")
        out.append(f"~ [{n.eid}] {_short_role(n)} {_q(n.label(), 60) if n.label() else ''} " + "; ".join(bits))
    if len(out) > max_lines:
        extra = len(out) - max_lines
        out = out[:max_lines] + [f"… {extra} more changes (observe for the full tree)"]
    return out


def text_change(a: str, b: str) -> str:
    """Describe an edit as one contiguous replaced span, with context."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    old_mid, new_mid = a[i:len(a) - j], b[i:len(b) - j]
    ctx = a[max(0, i - 30):i]
    return (f"len {len(a)} → {len(b)}; at offset {i} (after {_q(ctx, 40)}) "
            f"replaced {_q(old_mid, 200)} with {_q(new_mid, 200)}")


def file_state(path: str | None):
    if not path:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return ("missing",)
    except OSError:
        return ("unreadable",)
    if not os.path.isdir(path):
        return (st.st_mtime, st.st_size)
    # A package (.rtfd, .pages, …): rewriting a file inside it doesn't change the
    # folder's own mtime, so look at its contents.
    mt, size = st.st_mtime, 0
    try:
        for root, _dirs, files in os.walk(path, onerror=_raise):
            for f in files:
                fs = os.stat(os.path.join(root, f))
                mt, size = max(mt, fs.st_mtime), size + fs.st_size
    except OSError:
        # A protected container (e.g. Stickies') blocks listing and reading, but a
        # file's mtime and size can still be read by its exact name.
        known = [m for m in PACKAGE_MEMBERS if os.path.exists(os.path.join(path, m))]
        if not known:
            return ("unreadable",)
        mt, size = st.st_mtime, 0
        for m in known:
            fs = os.stat(os.path.join(path, m))
            mt, size = max(mt, fs.st_mtime), size + fs.st_size
    return (mt, size)


PACKAGE_MEMBERS = ("TXT.rtf", "TXT.rtfd", "index.xml", "Index.zip", "Data", "Contents.json")


def _raise(e):
    raise e
