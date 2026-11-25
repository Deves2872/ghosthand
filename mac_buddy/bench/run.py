"""Benchmark runner.

    uv run mac-buddy-bench --runs 5 --timeout 300            # all tasks
    uv run mac-buddy-bench --tasks t3,t8 --runs 3
    uv run mac-buddy-bench --report results/<dir>            # rebuild report

Each run: reset the task's data and apps, bring the "person" app (Stickies)
to the front, start an independent focus monitor, run the agent, then check
the outcome on disk / in the apps. The agent's own claim is recorded but never
used to decide a pass.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

from .. import ax
from ..focus import FocusMonitor, front_app
from ..harness import MODEL, load_dotenv, run_task
from . import env
from .person import Person, _hid_combo
from .tasks import BY_ID, T8_PERSON_TEXT, TASKS


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, math.ceil(p / 100 * len(s)) - 1)   # nearest-rank
    return s[k]


async def one_run(task, i: int, args, outdir: Path) -> dict:
    print(f"\n=== {task.id} run {i + 1}/{args.runs}: {task.title} ===", flush=True)
    ctx = task.setup()
    person = Person(T8_PERSON_TEXT, cps=args.cps)
    started = person.start_app()
    if not started:
        print("could not bring the person app to the front; skipping run")
        return {"task": task.id, "run": i, "status": "setup_failed"}
    time.sleep(0.5)
    run: dict = {"task": task.id, "run": i, "level": task.level}
    if task.person_types:
        person.start_typing()
    mon = FocusMonitor(interval=0.005)
    mon.__enter__()
    log = outdir / "logs" / f"{task.id}-{i + 1}.jsonl"
    res = await run_task(task.prompt(ctx), model=args.model, effort=args.effort,
                         timeout_s=args.timeout, quiet=args.quiet, log_path=str(log))
    rep = mon.stop()
    if task.person_types:
        time.sleep(0.3)
        person.stop()
    person_pid = person.pid
    run["focus_moved"] = rep.front_changed or rep.start_front[0] != person_pid
    run["fronts_seen"] = [n for _, n in rep.fronts_seen]
    run["mouse_moved"] = rep.mouse_moved
    run["person_lost_focus_to"] = person.lost_to
    time.sleep(0.4)
    if task.person_types:
        run["person"] = {"typed": person.typed, "note": person.read_note()}
    try:
        passed, details = task.check(ctx, res, run)
    except Exception as e:  # a checker crash is a failure, never a pass
        passed, details = False, {"checker_error": repr(e)}
    run.update(status=res.status, passed=bool(passed), claimed_done=res.status == "done",
               false_done=res.status == "done" and not passed, timeout=res.status == "timeout",
               elapsed_s=round(res.elapsed_s, 2), tool_calls=res.tool_calls, turns=res.turns,
               stale_refusals=res.stale_refusals, server_focus_reports=res.focus_moved_reports,
               answer=res.answer, evidence=res.evidence, error=res.error, details=details,
               tokens_in=res.input_tokens, tokens_out=res.output_tokens)
    run.pop("person", None) if not task.person_types else None
    # tidy the person's note
    if front_app()[0] != person_pid:
        env.sh("open", "-a", "Stickies")
        time.sleep(0.6)
    _cleanup_note(person)
    verdict = "PASS" if passed else "FAIL"
    print(f"--- {task.id}#{i + 1}: {verdict} · agent said {res.status} · {res.elapsed_s:.1f}s · "
          f"focus {'MOVED ' + '→'.join(run['fronts_seen']) if run['focus_moved'] else 'kept'} · {details}",
          flush=True)
    return run


def _cleanup_note(person: Person):
    app = ax.find_app("Stickies")
    if not app:
        return
    for w in ax.windows(app):
        t = str(ax.get(w, "AXTitle") or "")
        if t in ("Untitled", "") or (person.typed and person.typed[:12] in t):
            try:
                ax.perform(w, "AXRaise")
            except ax.AXError:
                pass
            time.sleep(0.3)
            if front_app()[1] == "Stickies":
                _hid_combo("cmd+w")
                time.sleep(0.6)
                for w2 in ax.windows(app):
                    for el in env._desc(w2):
                        if ax.get(el, "AXRole") == "AXButton" and ax.get(el, "AXTitle") == "Delete Note":
                            try:
                                ax.perform(el, "AXPress")
                            except ax.AXError:
                                pass
                time.sleep(0.4)


def report(outdir: Path) -> str:
    runs = [json.loads(l) for l in (outdir / "runs.jsonl").read_text().splitlines() if l.strip()]
    def _not_run(r):
        return r.get("status") == "setup_failed" or (r.get("status") == "error" and r.get("tool_calls", 0) == 0
                                                     and "credit balance" in (r.get("error") or ""))
    skipped = [r for r in runs if _not_run(r)]
    runs = [r for r in runs if not _not_run(r)]
    meta = json.loads((outdir / "meta.json").read_text()) if (outdir / "meta.json").exists() else {}
    lines = [f"# Benchmark results — {outdir.name}", "",
             f"Model `{meta.get('model')}`, effort `{meta.get('effort')}`, timeout {meta.get('timeout')}s per run, "
             f"{meta.get('runs')} runs per task from the same starting state. Outcomes checked on disk / in "
             "the apps, independently of the agent's message.", "",
             "| task | level | runs | pass | timeout | said done but wasn't | focus moved | p50 s | p95 s |",
             "|---|---|---|---|---|---|---|---|---|"]
    allt = []
    for t in TASKS:
        rs = [r for r in runs if r["task"] == t.id]
        if not rs:
            continue
        ts = [r["elapsed_s"] for r in rs]
        allt += ts
        lines.append(f"| {t.id} {t.title} | {t.level} | {len(rs)} | {sum(r['passed'] for r in rs)} | "
                     f"{sum(r['timeout'] for r in rs)} | {sum(r['false_done'] for r in rs)} | "
                     f"{sum(r['focus_moved'] for r in rs)} | {pct(ts, 50):.1f} | {pct(ts, 95):.1f} |")
    n = len(runs)
    lines.append(f"| **all** | | {n} | {sum(r['passed'] for r in runs)} | {sum(r['timeout'] for r in runs)} | "
                 f"{sum(r['false_done'] for r in runs)} | {sum(r['focus_moved'] for r in runs)} | "
                 f"{pct(allt, 50):.1f} | {pct(allt, 95):.1f} |")
    lines += ["", f"p50/p95 are nearest-rank over the runs in each row (n={n} overall)."
              + (f" {len(skipped)} run(s) never reached the agent (person app not frontable, or API billing "
                 f"error before the first call) and are excluded: {', '.join(r['task'] + '#' + str(r['run'] + 1) for r in skipped)}."
                 if skipped else ""),
              f"Agent reported 'failed' (honest failure): {sum(r['status'] == 'failed' for r in runs)}; "
              f"no finish / harness error: {sum(r['status'] in ('no_finish', 'error') for r in runs)}; "
              f"stale-state refusals by the server: {sum(r['stale_refusals'] for r in runs)}.", "",
              "## Failures", ""]
    for r in runs:
        if not r["passed"]:
            lines.append(f"- **{r['task']}#{r['run'] + 1}** agent said `{r['status']}` "
                         f"({r['elapsed_s']}s, {r['tool_calls']} calls){' — ' + r['error'] if r.get('error') else ''}: "
                         f"`{json.dumps(r['details'], ensure_ascii=False)[:400]}`"
                         + (f" — evidence given: “{(r.get('evidence') or '')[:200]}”" if r["status"] == "done" else ""))
    if all(r["passed"] for r in runs):
        lines.append("none")
    tok_in = sum(r.get("tokens_in", 0) for r in runs)
    tok_out = sum(r.get("tokens_out", 0) for r in runs)
    lines += ["", f"Tokens: {tok_in} input (incl. cached) / {tok_out} output over {n} runs."]
    md = "\n".join(lines) + "\n"
    (outdir / "report.md").write_text(md)
    return md


async def amain(args):
    from .. import permissions
    if not permissions.request(interactive=sys.stdin.isatty()):
        sys.exit(2)
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        sys.exit("Set ANTHROPIC_API_KEY (or put it in .env).")
    ids = args.tasks.split(",") if args.tasks else [t.id for t in TASKS]
    outdir = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    (outdir / "logs").mkdir(parents=True, exist_ok=True)
    (outdir / "meta.json").write_text(json.dumps({"model": args.model, "effort": args.effort,
                                                  "timeout": args.timeout, "runs": args.runs,
                                                  "tasks": ids}))
    print(f"results → {outdir}")
    with open(outdir / "runs.jsonl", "a") as f:
        for tid in ids:
            for i in range(args.runs):
                r = await one_run(BY_ID[tid], i, args, outdir)
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                f.flush()
                if "credit balance" in (r.get("error") or ""):
                    print("API credit exhausted — stopping the benchmark.")
                    env.quit_textedit()
                    print("\n" + report(outdir))
                    return
    env.quit_textedit()
    print("\n" + report(outdir))


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--cps", type=float, default=7.0, help="person typing speed (chars/s)")
    ap.add_argument("--out", default="results")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--report", default=None, help="only rebuild report.md for a results dir")
    args = ap.parse_args()
    if args.report:
        print(report(Path(args.report)))
        return
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
