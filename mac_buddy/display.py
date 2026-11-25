"""Live output for the harness and chat.

Compact (default): one line per tool call, e.g.

     6.5s  insert_text e3 "second check"      ✓ 1 change · focus ok
     8.2s  press_key cmd+s                     ✓ saved notes.txt (24 B) · focus ok

Anything that needs attention (ERROR, STALE, FOCUS MOVED, NO CHANGE, NOT SAVED…)
is printed in full under its line. Verbose (-v, /verbose) prints every result in
full, as before. Every event also goes to a JSONL log, which `mac-buddy-log`
replays in either view.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
     "c": "\033[36m", "m": "\033[35m", "x": "\033[0m"}

LOG_DIR = os.path.expanduser("~/Library/Logs/mac-buddy")

# Result lines that must always be shown, in full, in the compact view.
_ALERT = re.compile(r"^(ERROR|FOCUS MOVED|NO CHANGE|NO NEW WINDOW|NOT SAVED|APPS LAUNCHED|NOTE: changed|"
                    r"DISK: .*(does not exist|can't be checked)|.*READ-BACK DIFFERS|.*FAILED)")


def new_log_path(kind: str) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.jsonl")


def _width() -> int:
    return shutil.get_terminal_size((120, 24)).columns


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: max(n - 1, 0)] + "…"


def call_label(name: str, inp: dict) -> str:
    """`insert_text e3 "second check"` — the call with its useful arguments only."""
    parts = [name]
    for k, v in inp.items():
        if k == "window" and len(inp) > 1:
            continue  # the element id says enough; the window is nearly always the same
        if isinstance(v, list):
            v = " ▸ ".join(map(str, v))
        if isinstance(v, str) and k in ("text", "value", "find", "replace", "answer", "evidence"):
            v = json.dumps(_clip(v, 40), ensure_ascii=False)
        parts.append(str(v))
    return " ".join(parts)


def summarize(name: str, text: str) -> tuple[str, list[str]]:
    """(one-line summary, lines that need attention) for a tool result."""
    lines = text.splitlines()
    alerts = [l for l in lines if _ALERT.match(l.strip())]
    if lines and lines[0].startswith("ERROR"):
        return "✗ " + _clip(lines[0][6:].strip(), 70), [l for l in lines[1:6] if l.strip()]
    bits: list[str] = []
    ok = "✓ "
    if name == "observe":
        m = re.match(r"WINDOW (\w+) · app (.+?) \(pid \d+\) · title '(.*?)'", lines[0] if lines else "")
        n = sum(1 for l in lines if re.match(r"\s*\[e\d+\]", l))
        return (f"{m.group(1)} '{_clip(m.group(3), 30)}' · {n} elements" if m else _clip(lines[0], 60)), alerts
    if name == "list_apps":
        return f"{sum(1 for l in lines if l.startswith('- '))} apps", alerts
    if name == "menu":
        return f"{sum(1 for l in lines if l.startswith('- '))} items", alerts
    if name == "read_text":
        m = re.search(r"text length (\d+)", text)
        return (f"{m.group(1)} chars" if m else _clip(lines[0], 60)), alerts
    if name in ("launch_app", "wait_for_save"):
        bits.append(_clip(lines[0], 70))
        ok = "" if name == "wait_for_save" and not lines[0].startswith("SAVED") else ok
    if name == "new_document":
        bits += [_clip(l[2:], 50) for l in lines if l.startswith("+ NEW WINDOW")]
    # actions: changes, new/closed windows, disk, focus
    in_changes = False
    n_changes = 0
    title = None
    for l in lines:
        if l.startswith("CHANGES in"):
            in_changes = True
            continue
        if in_changes and l.startswith("  "):
            n_changes += 1
            m = re.match(r"\s*~ window title: \"(.*?)\" → \"(.*?)\"", l)
            if m:
                title = m.group(2)
            continue
        in_changes = False
    if n_changes:
        bits.append(f"{n_changes} change{'s' * (n_changes != 1)}" + (f", title → '{_clip(title, 30)}'" if title else ""))
    if any(l.startswith("NO CHANGE") for l in lines):
        ok, bits = "⚠ ", bits + ["no visible change"]
    for l in lines:
        if l.startswith("+ NEW WINDOW") and name != "new_document":
            bits.append("new window " + _clip(l.split(":", 1)[1].split("—")[0].strip(), 30))
        elif "CLOSED as a result" in l:
            bits.append("window closed")
        m = re.match(r"DISK: (.+?) was written \(size (\d+) bytes", l)
        if m:
            bits.append(f"saved {os.path.basename(m.group(1).rstrip('/'))} ({m.group(2)} B)")
    if any(l.startswith("focus ok") for l in lines):
        bits.append("focus ok")
    if any(l.startswith("NOTE: the person switched") or "the person switched apps themselves" in l
           for l in lines):
        bits.append("you switched apps")
    if any(l.startswith("FOCUS MOVED:") for l in lines):  # during this action
        ok, bits = "✗ ", bits + ["FOCUS MOVED"]
    elif any(l.startswith("FOCUS MOVED (seen by the session watch") for l in lines):
        # between/after actions: could be the person switching apps
        ok = "✗ " if ok == "✗ " else "⚠ "
        bits.append("front app changed meanwhile")
    if not bits:
        first = lines[0] if lines else ""
        bits.append(_clip(re.sub(r"^ACTION \S+ ?", "", first), 60))
    return ok + " · ".join(bits), alerts


class Printer:
    def __init__(self, quiet: bool = False, log_path: str | None = None, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose
        self.log_path = log_path
        self.log = open(log_path, "a") if log_path else None
        self._pending: dict | None = None

    def event(self, kind: str, **data):
        if self.log:
            self.log.write(json.dumps({"t": time.time(), "kind": kind, **data}, default=str) + "\n")
            self.log.flush()
        if not self.quiet:
            self.show(kind, data)

    # ---------------------------------------------------------------- output
    def show(self, kind: str, data: dict):
        t = data.get("elapsed", 0)
        stamp = f"{C['dim']}{t:6.1f}s{C['x']} "
        if kind == "thought":
            if self.verbose:
                print(stamp + f"{C['m']}✻ {data['text'].strip()}{C['x']}")
            else:
                print(stamp + f"{C['m']}{C['dim']}✻ {_clip(data['text'], _width() - 12)}{C['x']}")
        elif kind == "call":
            if self.verbose:
                args = json.dumps(data["input"], ensure_ascii=False)
                print(stamp + f"{C['b']}{C['c']}→ {data['name']}{C['x']} {_clip(args, 220)}")
            elif data["name"] != "finish":
                self._pending = {"stamp": stamp, "label": call_label(data["name"], data["input"])}
        elif kind == "result":
            if self.verbose:
                self._full_result(data)
            else:
                self._compact_result(data)
        elif kind == "finish":
            col = C["g"] if data["status"] == "done" else C["r"]
            print(stamp + f"{C['b']}{col}■ {data['status'].upper()}{C['x']}"
                  + (f"  {C['b']}{data.get('answer')}{C['x']}" if data.get("answer") else ""))
            ev = data.get("evidence") or ""
            print(f"         {C['dim']}evidence: {ev if self.verbose else _clip(ev, 3 * _width())}{C['x']}")
        elif kind == "reply":
            print(f"{C['b']}{data['text']}{C['x']}")
        elif kind == "info":
            txt = data["text"] if self.verbose else _clip(data["text"], 2 * _width() - 10)
            print(stamp + f"{C['dim']}{txt}{C['x']}")

    def _compact_result(self, data: dict):
        summary, alerts = summarize(data["name"], data["text"])
        p = self._pending or {"stamp": "", "label": data["name"]}
        self._pending = None
        col = C["r"] if summary.startswith("✗") else C["y"] if summary.startswith("⚠") else C["g"]
        label_w = min(44, max(24, _width() // 3))
        label = _clip(p["label"], label_w).ljust(label_w)
        room = max(_width() - 8 - label_w - 2, 20)
        print(p["stamp"] + f"{C['c']}{label}{C['x']}  {col}{_clip(summary, room)}{C['x']}")
        shown = set()
        for a in alerts:
            if a in shown:
                continue
            shown.add(a)
            acol = C["r"] + C["b"] if (a.startswith("FOCUS MOVED:") or a.startswith("ERROR")) else C["y"]
            print(f"         {acol}{a.strip()}{C['x']}")

    def _full_result(self, data: dict):
        lines = data["text"].splitlines()
        limit = 40 if data["name"] != "observe" else 14
        for ln in lines[:limit]:
            col = ""
            if ln.startswith("ERROR"):
                col = C["r"]
            elif "FOCUS MOVED" in ln:
                col = C["r"] + C["b"]
            elif ln.startswith("focus ok"):
                col = C["g"]
            elif ln.lstrip().startswith("+"):
                col = C["g"]
            elif ln.lstrip().startswith("-"):
                col = C["r"]
            elif ln.lstrip().startswith("~") or ln.startswith("DISK"):
                col = C["y"]
            elif ln.startswith("NO CHANGE"):
                col = C["y"] + C["b"]
            print(f"         {col}{ln}{C['x']}")
        if len(lines) > limit:
            print(f"         {C['dim']}… {len(lines) - limit} more lines{C['x']}")


def replay_main():
    """mac-buddy-log [PATH|latest] [-v] [--all]: replay a saved log."""
    import argparse
    import glob
    ap = argparse.ArgumentParser(description="Replay a mac-buddy log (compact by default).")
    ap.add_argument("path", nargs="?", default="latest", help="a .jsonl log, or 'latest'")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every result in full")
    ap.add_argument("--list", action="store_true", help="list saved logs")
    a = ap.parse_args()
    logs = sorted(glob.glob(os.path.join(LOG_DIR, "*.jsonl")))
    if a.list:
        for p in logs[-30:]:
            print(p)
        return
    path = logs[-1] if a.path == "latest" and logs else a.path
    if not path or not os.path.exists(path):
        raise SystemExit(f"no log found ({LOG_DIR})")
    print(f"{C['dim']}{path}{C['x']}")
    pr = Printer(verbose=a.verbose)
    with open(path) as f:
        for line in f:
            ev = json.loads(line)
            kind = ev.pop("kind")
            ev.pop("t", None)
            if kind == "user":
                print(f"\n{C['b']}{C['c']}you ›{C['x']} {ev.get('text')}")
            elif kind == "summary":
                print(f"{C['dim']}{ev.get('elapsed', 0):.1f}s · {ev.get('tool_calls')} tool calls · "
                      f"{ev.get('tokens_in')} in / {ev.get('tokens_out')} out tokens{C['x']}")
            else:
                pr.show(kind, ev)
