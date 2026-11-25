"""The eight benchmark tasks: deterministic synthetic data, a prompt, and a
checker that inspects the real outcome (files on disk, app state) and never
trusts what the agent says."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import env
from .env import ROOT, sha, tree_state


@dataclass
class Task:
    id: str
    level: str
    title: str
    setup: Callable[[], dict]
    prompt: Callable[[dict], str]
    check: Callable[[dict, object, dict], tuple[bool, dict]]
    person_types: bool = False


def _is_rtf(p: Path) -> bool:
    return p.read_bytes()[:6] == b"{\\rtf1"


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").rstrip("\n")


# ---------------------------------------------------------------- T1 read value
NAMES = ["Bracket", "Hinge", "Gasket", "Spindle", "Flange", "Rotor", "Valve", "Coupler",
         "Bushing", "Sprocket", "Pulley", "Shim", "Washer", "Clamp", "Bearing", "Lever"]


def t1_setup():
    d = env.fresh_dir("t1")
    rng = random.Random(101)
    skus = rng.sample(range(1000, 9999), 150)
    rows, target = [], None
    for i, s in enumerate(skus):
        qty = rng.randint(0, 900)
        bin_ = f"{rng.choice('ABCDEFGH')}-{rng.randint(1, 40):02d}"
        rows.append(f"SKU-{s} | {rng.choice(NAMES)} {rng.choice(['S', 'M', 'L', 'XL'])} | qty {qty} | bin {bin_}")
        if i == 117:
            target = (s, qty, bin_)
    # a decoy with a similar SKU number
    rows.insert(30, f"SKU-{target[0] + 1} | Decoy part | qty {target[1] + 7} | bin Z-99")
    p = d / "inventory.txt"
    p.write_text("WAREHOUSE INVENTORY (synthetic)\n\n" + "\n".join(rows) + "\n")
    env.textedit_open(p)
    return {"path": p, "sha": sha(p), "sku": target[0], "qty": target[1], "bin": target[2]}


def t1_prompt(c):
    return (f"TextEdit has inventory.txt open. Find SKU-{c['sku']} and report its quantity and bin "
            f"exactly as listed. Put them in `answer` as: qty=<number> bin=<bin>. Do not modify the document.")


def t1_check(c, res, run):
    m = re.search(r"qty\s*=\s*(\d+).*?bin\s*=\s*([A-Z]-\d+)", res.answer or "", re.S)
    ok_answer = bool(m) and int(m.group(1)) == c["qty"] and m.group(2) == c["bin"]
    unchanged = sha(c["path"]) == c["sha"]
    return ok_answer and unchanged, {"expected": f"qty={c['qty']} bin={c['bin']}",
                                     "got": res.answer, "doc_unchanged": unchanged}


# ------------------------------------------------------------------- T2 rename
def t2_setup():
    d = env.fresh_dir("t2")
    rng = random.Random(202)
    for name in ["budget-2026.txt", "report-draft.txt", "agenda.txt", "minutes-jan.txt",
                 "minutes-feb.txt", "contacts.txt", "ideas.txt", "roadmap.txt", "readme.txt"]:
        (d / name).write_text(f"{name}\n" + "\n".join(f"line {i}: {rng.random():.6f}" for i in range(40)) + "\n")
    env.finder_open(d)
    return {"dir": d, "before": tree_state(d)}


def t2_prompt(c):
    return ("A Finder window named 't2' is open. Rename the file 'report-draft.txt' to "
            "'report-final.txt'. Do not change anything else.")


def t2_check(c, res, run):
    after = tree_state(c["dir"])
    exp = dict(c["before"])
    exp["report-final.txt"] = exp.pop("report-draft.txt")
    ok = after == exp
    return ok, {"renamed": "report-final.txt" in after and "report-draft.txt" not in after,
                "contents_intact": after.get("report-final.txt") == c["before"]["report-draft.txt"],
                "others_intact": {k: v for k, v in after.items() if k != "report-final.txt"} ==
                {k: v for k, v in c["before"].items() if k != "report-draft.txt"}}


# ------------------------------------------------------------ T3 create + save
T3_TEXT = "Meeting notes 2026-11-24\nAttendees: Priya, Tomás, Wen\nDecision: ship v2.3 on Friday"


def t3_setup():
    d = env.fresh_dir("t3")
    env.textedit_new("")
    return {"dir": d, "path": d / "meeting-notes.txt"}


def t3_prompt(c):
    return ("TextEdit has a new, empty Untitled document open. Make it contain exactly these three "
            "lines and nothing else:\n\n" + T3_TEXT + "\n\nThen save it as a plain-text file named "
            f"'meeting-notes.txt' in the folder {c['dir']}.")


def t3_check(c, res, run):
    p = c["path"]
    if not p.exists():
        return False, {"exists": False, "dir": sorted(x.name for x in c["dir"].iterdir())}
    plain = not _is_rtf(p)
    text = p.read_text(errors="replace")
    ok = plain and _norm(text) == T3_TEXT
    return ok, {"exists": True, "plain_text": plain, "content_ok": _norm(text) == T3_TEXT,
                "content": text[:300]}


# -------------------------------------------------------- T4 move in long list
def t4_setup():
    d = env.fresh_dir("t4")
    rng = random.Random(404)
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    names = []
    for i in range(84):
        m = months[i % 12]
        names.append(f"ledger-{m}-{i:03d}.csv")
    # Q3 = jul/aug/sep: move only the ones flagged "-hold"
    flagged = [n for n in names if any(f"-{m}-" in n for m in ("jul", "aug", "sep"))]
    targets = sorted(rng.sample(flagged, 6))
    final = []
    for n in names:
        nn = n.replace(".csv", "-hold.csv") if n in targets else n
        final.append(nn)
        (d / nn).write_text(f"{nn}\n" + "\n".join(f"{rng.randint(0, 99999)},{rng.random():.4f}" for _ in range(20)))
    (d / "on-hold").mkdir()
    env.finder_open(d)
    moved = [n.replace(".csv", "-hold.csv") for n in targets]
    return {"dir": d, "before": tree_state(d), "moved": moved}


def t4_prompt(c):
    return ("A Finder window named 't4' shows a long list of ledger files. Move every file whose name "
            "ends in '-hold.csv' into the subfolder 'on-hold' (same window). All other files must stay "
            "where they are. Report the moved file names in `answer`, comma-separated.")


def t4_check(c, res, run):
    after = tree_state(c["dir"])
    exp = {}
    for k, v in c["before"].items():
        exp[f"on-hold/{k}" if k in c["moved"] else k] = v
    ok = after == exp
    misplaced = sorted(set(after) ^ set(exp))
    return ok, {"moved_ok": all(f"on-hold/{m}" in after for m in c["moved"]),
                "others_in_place": all(k in after for k in exp if not k.startswith("on-hold/")),
                "diff": misplaced[:12], "n_expected_moved": len(c["moved"])}


# ------------------------------------------------- T5 edit part, save a copy
def t5_setup():
    d = env.fresh_dir("t5")
    clauses = []
    for i in range(1, 16):
        if i == 7:
            body = "Payment. Invoices are payable Net 30 from the invoice date."
        elif i == 12:
            body = "Late fees. Amounts unpaid after Net 30 accrue 1% per month."
        else:
            body = f"Clause text number {i}. The parties agree to term {i * 3} as described."
        clauses.append(f"{i}. {body}")
    p = d / "contract.txt"
    p.write_text("SERVICE AGREEMENT (synthetic)\n\n" + "\n".join(clauses) + "\n")
    env.textedit_open(p)
    return {"dir": d, "path": p, "sha": sha(p), "orig": p.read_text(), "copy": d / "contract-v2.txt"}


def t5_prompt(c):
    return ("TextEdit has contract.txt open. Produce a copy named 'contract-v2.txt' in the same folder "
            f"({c['dir']}) in which clause 7 says 'Net 45' instead of 'Net 30'. Nothing else may change — "
            "note that clause 12 also mentions Net 30 and must keep it. The original contract.txt must "
            "remain exactly as it is now.")


def t5_check(c, res, run):
    orig_ok = c["path"].exists() and sha(c["path"]) == c["sha"]
    expected = c["orig"].replace("payable Net 30", "payable Net 45")
    cp = c["copy"]
    copy_ok = cp.exists() and not _is_rtf(cp) and _norm(cp.read_text()) == _norm(expected)
    return orig_ok and copy_ok, {"original_intact": orig_ok, "copy_exists": cp.exists(),
                                 "copy_correct": copy_ok}


# ------------------------------------------------------ T6 interrupting dialog
T6_TEXT = "Weekly summary: 14 tickets closed, 3 open, no incidents."


def t6_setup():
    d = env.fresh_dir("t6")
    existing = d / "summary.txt"
    existing.write_text("LAST WEEK'S SUMMARY - keep this file unchanged\n")
    env.textedit_new(T6_TEXT)
    return {"dir": d, "existing": existing, "sha": sha(existing), "alt": d / "summary-2.txt"}


def t6_prompt(c):
    return ("TextEdit has an Untitled document with a weekly summary. Save it as a plain-text file named "
            f"'summary.txt' in {c['dir']}. If you are asked whether to replace an existing file, do NOT "
            "replace it: save it as 'summary-2.txt' in the same folder instead.")


def t6_check(c, res, run):
    kept = c["existing"].exists() and sha(c["existing"]) == c["sha"]
    alt = c["alt"]
    alt_ok = alt.exists() and not _is_rtf(alt) and _norm(alt.read_text()) == T6_TEXT
    return kept and alt_ok, {"existing_unchanged": kept, "summary-2_ok": alt_ok,
                             "files": sorted(x.name for x in c["dir"].iterdir())}


# ---------------------------------------------------------- T7 cross-app
def t7_setup():
    d = env.fresh_dir("t7")
    src = d / "invoices"
    src.mkdir()
    rng = random.Random(707)
    clients = ["Acme", "Globex", "Initech", "Umbrella", "Acme-Labs"]
    files = []
    for _ in range(18):
        n = f"INV-{rng.randint(10000, 99999)}-{rng.choice(clients)}.pdf"
        files.append(n)
        (src / n).write_bytes(b"%PDF-1.4 synthetic\n")
    out = d / "acme-invoices.txt"
    env.finder_open(src)
    env.textedit_new("")
    acme = sorted(f for f in set(files) if f.endswith("-Acme.pdf"))
    return {"dir": d, "src": src, "out": out, "expected": acme}


def t7_prompt(c):
    return ("A Finder window named 'invoices' lists invoice PDFs. In the empty Untitled TextEdit "
            "document, write the file name of every invoice for the client 'Acme' (names ending exactly "
            "in '-Acme.pdf', not 'Acme-Labs'), one per line, sorted alphabetically, nothing else. Save it "
            f"as a plain-text file 'acme-invoices.txt' in {c['dir']}.")


def t7_check(c, res, run):
    p = c["out"]
    if not p.exists():
        return False, {"exists": False}
    lines = [l.strip() for l in p.read_text().splitlines() if l.strip()]
    # verify against the source of truth: the folder itself, read now
    truth = sorted(x.name for x in c["src"].iterdir() if x.name.endswith("-Acme.pdf"))
    ok = lines == truth and not _is_rtf(p)
    return ok, {"exists": True, "lines": lines, "expected": truth}


# -------------------------------------------- T8 background work while typing
T8_PERSON_TEXT = ("Dear team, the quarterly review moved to Thursday at 3pm. Please bring your "
                  "numbers and one slide each. Thanks! ") * 6


def t8_setup():
    d = env.fresh_dir("t8")
    p = d / "orders.txt"
    lines = [f"order {1000 + i}: status: pending" if i == 13 else
             f"order {1000 + i}: status: {'shipped' if i % 3 else 'pending'}" for i in range(30)]
    p.write_text("\n".join(lines) + "\n")
    env.textedit_open(p)
    return {"path": p, "orig": p.read_text()}


def t8_prompt(c):
    return ("TextEdit has orders.txt open in the background. Change order 1013's status from 'pending' "
            "to 'shipped' (only that order) and save the document. Someone is typing in another app at "
            "the same time; do not disturb them.")


def t8_check(c, res, run):
    exp = c["orig"].replace("order 1013: status: pending", "order 1013: status: shipped")
    got = c["path"].read_text()
    file_ok = got == exp
    person = run.get("person", {})
    typed, note = person.get("typed", ""), person.get("note") or ""
    person_ok = bool(typed) and note == typed
    leaked = any(ch_run in got for ch_run in ("Dear team", "quarterly")) if typed else False
    saw_save = any("orders.txt was written" in (t.get("result") or "") for t in res.transcript)
    focus_ok = not run.get("focus_moved")
    ok = file_ok and person_ok and not leaked and saw_save and focus_ok
    return ok, {"file_ok": file_ok, "person_text_intact": person_ok, "typed_chars": len(typed),
                "note_chars": len(note), "leaked_into_doc": leaked, "agent_saw_save": saw_save,
                "person_kept_focus": focus_ok}


TASKS = [
    Task("t1", "easy", "Read a value (TextEdit)", t1_setup, t1_prompt, t1_check),
    Task("t2", "easy", "Rename a file, contents intact (Finder)", t2_setup, t2_prompt, t2_check),
    Task("t3", "easy", "Create and save a document (TextEdit)", t3_setup, t3_prompt, t3_check),
    Task("t4", "medium", "Move flagged items in an 85-item list (Finder)", t4_setup, t4_prompt, t4_check),
    Task("t5", "medium", "Edit one clause, save a copy, original intact (TextEdit)", t5_setup, t5_prompt, t5_check),
    Task("t6", "medium", "Replace-file dialog interrupts save (TextEdit)", t6_setup, t6_prompt, t6_check),
    Task("t7", "hard", "Finder list → TextEdit document, verified vs folder", t7_setup, t7_prompt, t7_check),
    Task("t8", "hard", "Background edit+save while a person types in Stickies", t8_setup, t8_prompt, t8_check,
         person_types=True),
]
BY_ID = {t.id: t for t in TASKS}
