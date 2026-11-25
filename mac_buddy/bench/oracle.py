"""Scripted (no model) solutions that drive the same MCP tool functions the
agent uses. They prove a task is solvable through the server and validate the
checkers, and t8 exercises background work while the person types.

    uv run python -m mac_buddy.bench.oracle t8
"""
from __future__ import annotations

import re
import sys
import time

from .. import server as S
from ..focus import FocusMonitor
from ..harness import RunResult
from . import env
from .person import Person
from .tasks import BY_ID, T3_TEXT, T8_PERSON_TEXT


def _id(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"pattern not found: {pattern}\n{text[:3000]}")
    return m.group(1)


def _log(res: RunResult, name: str, out: str):
    res.transcript.append({"name": name, "result": out})
    print(f"→ {name}\n" + "\n".join("    " + l for l in out.splitlines()[:14]))
    return out


def save_as_plain_text(res, wid: str, folder: str, name: str, make_plain=True):
    if make_plain:
        _log(res, "select_menu Make Plain Text", S.select_menu(wid, ["Format", "Make Plain Text"]))
    o = _log(res, "select_menu Save…", S.select_menu(wid, ["File", "Save…"]))
    _log(res, "press_key cmd+shift+g", S.press_key(wid, "cmd+shift+g"))
    o = S.observe("TextEdit", wid)
    pf = _id(o, r'\[(e\d+)\] TextField value="[^"]*" id=PathTextField')
    _log(res, "set_value path", S.set_value(wid, pf, folder))
    o = S.observe("TextEdit", wid)
    if "PathTextField" in o:
        _log(res, "press_key return", S.press_key(wid, "return"))
        o = S.observe("TextEdit", wid)
    nf = _id(o, r'\[(e\d+)\] TextField value="[^"]*" id=saveAsNameTextField')
    _log(res, "set_value name", S.set_value(wid, nf, name))
    o = S.observe("TextEdit", wid)
    m = re.search(r'\[(e\d+)\] Button "Save"', o)
    if m:
        _log(res, "click Save", S.click(wid, m.group(1)))


def t3(res):
    o = S.observe("TextEdit", "Untitled")
    wid = _id(o, r"WINDOW (w\d+)")
    ta = _id(o, r"\[(e\d+)\] TextArea")
    _log(res, "insert_text", S.insert_text(wid, ta, T3_TEXT, "start"))
    ctx = res.ctx
    save_as_plain_text(res, wid, str(ctx["dir"]), "meeting-notes.txt")


def t8(res):
    o = S.observe("TextEdit", "orders.txt")
    wid = _id(o, r"WINDOW (w\d+)")
    ta = _id(o, r"\[(e\d+)\] TextArea")
    _log(res, "replace_text", S.replace_text(wid, ta, "order 1013: status: pending", "order 1013: status: shipped"))
    _log(res, "select_menu Save", S.select_menu(wid, ["File", "Save"]))


SOLVERS = {"t3": t3, "t8": t8}


def main():
    tid = sys.argv[1] if len(sys.argv) > 1 else "t8"
    task = BY_ID[tid]
    ctx = task.setup()
    person = Person(T8_PERSON_TEXT, cps=8)
    assert person.start_app(), "could not front the person app"
    time.sleep(0.5)
    res = RunResult(task=tid)
    res.ctx = ctx
    person.start_typing()
    t0 = time.time()
    with FocusMonitor() as mon:
        SOLVERS[tid](res)
        time.sleep(1.0)
    person.stop()
    res.elapsed_s = time.time() - t0
    rep = mon.report()
    run = {"focus_moved": rep.front_changed, "person": {"typed": person.typed, "note": person.read_note()}}
    ok, details = task.check(ctx, res, run)
    print(f"\n{tid} scripted: {'PASS' if ok else 'FAIL'} in {res.elapsed_s:.1f}s; {rep.line()}\n{details}")
    env.sh("open", "-a", "Stickies")


if __name__ == "__main__":
    main()
