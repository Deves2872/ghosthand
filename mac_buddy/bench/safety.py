"""Scripted checks of the safe-failure rules, against real TextEdit windows.

    uv run python -m mac_buddy.bench.safety

1. Something changes between the model's look and its action -> STALE, no action.
2. The target element disappears -> ELEMENT GONE, no action.
3. The window closes -> WINDOW GONE, no action.
4. An accepted action with no effect is reported as NO CHANGE, not success.
"""
from __future__ import annotations

import re

from .. import server as S
from . import env


def check(name, cond, out):
    print(f"{'PASS' if cond else 'FAIL'}  {name}\n      {out.splitlines()[0][:160]}")
    return cond


def main():
    ok = True
    d = env.fresh_dir("safety")
    p = d / "doc.txt"
    p.write_text("alpha\nbeta\n")
    env.textedit_open(p)
    o = S.observe("TextEdit", "doc.txt")
    wid = re.search(r"WINDOW (w\d+)", o).group(1)
    ta = re.search(r"\[(e\d+)\] TextArea", o).group(1)

    # 1. external change after the look (another process edits the text)
    env.osa('tell application "TextEdit" to set text of document 1 to "changed behind your back\\n"')
    out = S.insert_text(wid, ta, "MINE", "end")
    ok &= check("change between look and action is refused", out.startswith("ERROR: STALE"), out)
    ok &= check("…and nothing was typed", "MINE" not in env.osa(
        'tell application "TextEdit" to get text of document 1').stdout, out)

    # 2. element goes away: open the Save sheet, note its Cancel button, then
    #    dismiss the sheet outside the agent. The first attempt is refused as
    #    STALE (with the diff); reusing the old id after that is ELEMENT GONE.
    env.textedit_new("rich")
    o = S.observe("TextEdit", "Untitled")
    w2 = re.search(r"WINDOW (w\d+)", o).group(1)
    S.select_menu(w2, ["File", "Save…"])
    o = S.observe("TextEdit", w2)
    cancel = re.search(r'\[(e\d+)\] Button "Cancel"', o).group(1)
    from .. import ax as AX
    win = S.ST.windows[w2].el
    for el in env._desc(win):
        if AX.get(el, "AXRole") == "AXButton" and AX.get(el, "AXTitle") == "Cancel":
            AX.perform(el, "AXPress")
            break
    env.wait_for(lambda: not any(AX.get(c, "AXRole") == "AXSheet" for c in AX.get(win, "AXChildren") or []), 4)
    out = S.click(w2, cancel)
    ok &= check("first use after the sheet vanished is refused as STALE", out.startswith("ERROR: STALE"), out)
    out = S.click(w2, cancel)
    ok &= check("reusing the vanished element's id is refused", out.startswith("ERROR: ELEMENT GONE"), out)
    bold = re.search(r'\[(e\d+)\] CheckBox:Segment "bold"', S.observe("TextEdit", w2)).group(1)

    # 3. window closes behind the agent
    S.observe("TextEdit", w2)
    env.osa('tell application "TextEdit" to close every document saving no')
    out = S.click(w2, bold)
    ok &= check("closed window is refused", out.startswith("ERROR: WINDOW GONE"), out)

    # 4. accepted but ineffective
    env.textedit_open(p)
    o = S.observe("TextEdit", "doc.txt")
    w3 = re.search(r"WINDOW (w\d+)", o).group(1)
    # Go-to-folder only means something inside a save panel; here it does nothing.
    # (Note: TextEdit rewrites the file on Save even when unmodified, which the
    # server reports as "DISK: … was written".)
    out = S.press_key(w3, "cmd+shift+g")
    nc = [l for l in out.splitlines() if l.startswith("NO CHANGE") or l.startswith("DISK")]
    ok &= check("accepted-but-ineffective key: NO CHANGE and file unchanged on disk",
                "NO CHANGE" in out and "unchanged on disk" in out, " | ".join(nc) or out)
    env.quit_textedit()
    print("\nall safety checks passed" if ok else "\nSOME SAFETY CHECKS FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
