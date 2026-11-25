"""Plan once, run many steps, go back to the model only when something diverges.

The agent loop (harness.run_turns) asks the model after every single action:
a 6-step save costs 6+ model calls, each re-sending the whole history. Most
tasks are predictable from one look at the window, so here:

1. **Look** without the model: list the running apps and observe the target
   app's window.
2. **Plan** with one model call (schema-guaranteed JSON): every step, the
   element it targets (by role / label / id, so the plan also works on later
   runs), and what its result must show ("changed", "saved", …).
3. **Run** the steps through the same MCP tools, with no model calls. Each step
   resolves its target against a fresh observation and checks its expectations.
4. **Recover**: on the first surprise, hand the work to the normal agent loop
   with a summary of what was done, and let it finish from the current state.
5. **Remember**: a plan that ran clean is cached. The same request next time
   runs with zero planning calls, and is still checked step by step.

Every step carries a plain-language `say` caption, which the buddy's tutorial
mode shows next to a pointer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import anthropic

from .harness import FINISH_TOOL, SYSTEM, RunResult, model_tools, run_turns

CACHE_PATH = os.path.expanduser("~/Library/Application Support/mac-buddy/plans.json")

EXPECT = ["ok", "changed", "saved", "new_window", "window_closed"]
NO_WINDOW_TOOLS = {"list_apps", "observe", "launch_app", "new_document"}
# tools whose result alone can't show success (they only read)
READ_TOOLS = {"list_apps", "observe", "read_text", "menu"}


def plan_schema(tool_names: list[str]) -> dict:
    target = {"type": "object", "additionalProperties": False,
              "properties": {"role": {"type": "string"}, "label": {"type": "string"},
                             "ident": {"type": "string"}},
              "required": ["role", "label", "ident"]}
    step = {"type": "object", "additionalProperties": False,
            "properties": {
                "say": {"type": "string"},
                "tool": {"type": "string", "enum": tool_names},
                "args_json": {"type": "string"},
                "target": target,
                "expect": {"type": "array", "items": {"type": "string", "enum": EXPECT}},
                "expect_text": {"type": "string"},
                "report": {"type": "boolean"}},
            "required": ["say", "tool", "args_json", "target", "expect", "expect_text", "report"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"summary": {"type": "string"}, "needs_answer": {"type": "boolean"},
                           "app": {"type": "string"},
                           "steps": {"type": "array", "items": step}},
            "required": ["summary", "needs_answer", "app", "steps"]}


PLANNER_RULES = """You are planning a whole task in advance. A program will run your steps one by one through the tools below, WITHOUT asking you again, and check each step's result. Only if a step's check fails will you be asked to continue step by step.

Write the plan as JSON:
- summary: one plain sentence for the person, e.g. "Add the line to notes.txt and save it."
- app: the app the task is about (exact name).
- needs_answer: true only if the task asks you to report something (a value, a count…).
- steps, each with:
  - say: a short plain-language instruction a beginner could follow, e.g. "Open the File menu and choose Save…". It is shown in tutorials.
  - tool: the tool to call.
  - args_json: the tool's arguments as a JSON object string, e.g. {"path": ["File", "Save…"]}. Leave out "window": the program fills in the current window (the one observed below, or the newest window a step opened). Leave out "element" when you give a target.
  - target: the element the step acts on, matched against a fresh look at the window just before the step: role (e.g. "TextArea", "Button", "TextField"), label (its title or description, may be partial) and ident (its id= value). Use "" for parts you don't know, and all "" when the tool needs no element. For elements that appear only later (a save sheet's fields), use what the app notes say (e.g. ident "saveAsNameTextField").
  - expect: what the result must show: "ok" (no error), "changed" (the window changed), "saved" (the file was written to disk), "new_window", "window_closed".
  - expect_text: text the result must contain, or "".
  - report: true if this step's result holds what the task asks you to report.
- Include every step: launching the app if it is not running, creating or opening the document, editing, saving, and confirming. Use wait_for_save for apps that save on their own. Don't add observe steps just to look; the program looks before every step with a target.
- The last step that changes something must have an expectation that proves the task is done (e.g. "saved" after saving).
- Never plan to open files through Finder, and never plan anything the task doesn't ask for."""


def _strip_titles(list_apps_text: str, keep_app: str | None) -> str:
    """Running apps by name only: other apps' window titles are none of the model's business."""
    out = []
    for line in list_apps_text.splitlines():
        m = re.match(r"^- (.+?) \[([^\]]+)\] pid \d+: (.*)$", line)
        if m and not (keep_app and keep_app.lower() in m.group(1).lower()):
            out.append(f"- {m.group(1)}")
        else:
            out.append(line)
    return "\n".join(out)


def _guess_app(task: str, apps_text: str) -> str | None:
    names = re.findall(r"^- (.+?) \[", apps_text, re.M)
    known = names + ["TextEdit", "Stickies", "Notes", "Finder", "Numbers", "Pages", "Keynote",
                     "Reminders", "Calendar", "Mail", "Safari", "Preview", "Music"]
    low = task.lower()
    for n in sorted(set(known), key=len, reverse=True):
        if re.search(r"\b" + re.escape(n.lower()) + r"\b", low):
            return n
    return None


# ------------------------------------------------------------------ elements

_LINE = re.compile(r'^\s*\[(e\d+)\] (\S+)(?: "((?:[^"\\]|\\.)*)")?(.*)$')


def parse_tree(observe_text: str) -> list[dict]:
    nodes = []
    for line in observe_text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        rest = m.group(4)
        val = re.search(r'value="((?:[^"\\]|\\.)*)"', rest)
        ident = re.search(r"\bid=(.+?)(?= \(|$)", rest)
        nodes.append({"eid": m.group(1), "role": m.group(2), "label": m.group(3) or "",
                      "value": val.group(1) if val else "", "ident": ident.group(1) if ident else "",
                      "disabled": "(disabled)" in rest})
    return nodes


def resolve(target: dict, nodes: list[dict]) -> str | None:
    """Best element id for a target, or None."""
    role = (target.get("role") or "").lower().removeprefix("ax")
    label = (target.get("label") or "").lower()
    ident = target.get("ident") or ""
    best, best_score = None, 0
    for n in nodes:
        r = n["role"].lower()
        score = 0
        if ident:
            if n["ident"] != ident:
                continue
            score += 10
        if role:
            if r == role or r.split(":")[0] == role or r.split(":")[-1] == role:
                score += 3
            elif not ident:
                continue
        if label:
            lab = n["label"].lower()
            if lab == label:
                score += 5
            elif label in lab or (lab and lab in label):
                score += 3
            elif label in n["value"].lower():
                score += 1
            elif not ident:
                continue
        if n["disabled"]:
            score -= 2
        if score > best_score:
            best, best_score = n["eid"], score
    return best


def check(step: dict, text: str) -> str | None:
    """None if the result shows what the step expects, else why not."""
    first = text.splitlines()[0] if text else ""
    if first.startswith("ERROR") or first.startswith("REFUSED"):
        return first
    if "FOCUS MOVED:" in text or "FOCUS MOVED (seen by" in text:
        return "focus moved"
    for e in step.get("expect") or []:
        if e == "changed" and not any(k in text for k in (
                "CHANGES in", "+ NEW WINDOW", "read-back matches", "LAUNCHED", "SAVED:", "was written",
                "is already running")):
            return "expected a change, but nothing visible changed"
        if e == "saved" and not ("was written" in text or text.startswith("SAVED:")):
            return "expected the file to be saved, but the disk didn't change"
        if e == "new_window" and "+ NEW WINDOW" not in text:
            return "expected a new window"
        if e == "window_closed" and "CLOSED" not in text and "a window closed" not in text:
            return "expected a window to close"
    want = (step.get("expect_text") or "").strip()
    if want and want.lower() not in text.lower():
        return f"expected the result to contain {want!r}"
    return None


# --------------------------------------------------------------------- cache

def _key(task: str, app: str | None) -> str:
    norm = " ".join(task.lower().split())
    return hashlib.sha256(f"{app}|{norm}".encode()).hexdigest()[:24]


def cache_get(task: str, app: str | None) -> dict | None:
    try:
        return json.load(open(CACHE_PATH)).get(_key(task, app))
    except (OSError, ValueError):
        return None


def cache_put(task: str, app: str | None, plan: dict | None):
    try:
        data = json.load(open(CACHE_PATH))
    except (OSError, ValueError):
        data = {}
    k = _key(task, app)
    if plan is None:
        data.pop(k, None)
    else:
        data[k] = {**plan, "_task": task, "_saved": time.time()}
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(data, open(CACHE_PATH, "w"), indent=1)


# ------------------------------------------------------------------ the run

StepHook = Callable[[int, dict, dict], Awaitable[str]]  # (index, step, frame) -> "go"|"skip"|"stop"


@dataclass
class PlanRun:
    plan: dict | None = None
    from_cache: bool = False
    steps_done: int = 0
    failed_step: int | None = None
    failure: str = ""
    fell_back: bool = False
    planning_calls: int = 0
    notes: list = field(default_factory=list)


async def plan_and_run(task: str, session, client, pr, *, model: str, effort: str = "medium",
                       fallbacks: bool = True, history: str = "", on_step: StepHook | None = None,
                       use_cache: bool = True) -> tuple[RunResult, PlanRun]:
    res = RunResult(task=task)
    run = PlanRun()
    t0 = time.time()
    tools_all = model_tools(await session.list_tools())
    tool_names = [t["name"] for t in tools_all]

    async def call(name: str, args: dict, quiet: bool = False) -> str:
        if not quiet:
            pr.event("call", name=name, input=args, elapsed=time.time() - t0)
        res.tool_calls += 1
        out = await session.call_tool(name, args)
        text = "\n".join(c.text for c in out.content if getattr(c, "type", "") == "text")
        if not quiet:
            pr.event("result", name=name, text=text, elapsed=time.time() - t0)
        res.transcript.append({"name": name, "input": args, "result": text})
        return text

    async def harness(name: str, args: dict) -> str:
        out = await session.call_tool(name, args)
        return "\n".join(c.text for c in out.content if getattr(c, "type", "") == "text")

    pr.event("info", text=f"task: {task}", elapsed=0)
    # 1. look, without the model
    apps = await call("list_apps", {}, quiet=True)
    app = _guess_app(task, apps)
    look = ""
    win = None
    if app and f"- {app.lower()}" in apps.lower():
        look = await call("observe", {"app": app})
        m = re.search(r"WINDOW (w\d+)", look)
        win = m.group(1) if m else None

    # 2. plan (or reuse a plan that worked before)
    catalog = "\n".join(f"- {t['name']}({', '.join(t['input_schema'].get('properties', {}))}): "
                        f"{' '.join((t['description'] or '').split())}" for t in tools_all)

    async def make_plan(look_text: str, done: list[dict] | None = None) -> dict:
        context = (f"TASK: {task}\n\n" + (f"EARLIER IN THIS CONVERSATION:\n{history}\n\n" if history else "")
                   + f"RUNNING APPS:\n{_strip_titles(apps, app)}\n\n")
        if done:
            context += ("STEPS ALREADY DONE (they worked; don't repeat them):\n"
                        + "\n".join(f"- {d['say']}" for d in done)
                        + "\n\nPlan ONLY the remaining steps, using the window as it is now.\n\n")
        context += (f"CURRENT WINDOW OF {app} (ids like e3 may be used directly):\n{look_text}\n"
                    if look_text else f"{app or 'The app'} has no window observed yet; its content is unknown.\n")
        resp = await client.messages.create(
            model=model, max_tokens=8000, system=SYSTEM + "\n\n" + PLANNER_RULES + "\n\nTOOLS:\n" + catalog,
            messages=[{"role": "user", "content": context}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": plan_schema(tool_names)}})
        run.planning_calls += 1
        res.input_tokens += resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0) \
            + (resp.usage.cache_creation_input_tokens or 0)
        res.output_tokens += resp.usage.output_tokens
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(txt)
        except ValueError:
            run.failure = "the plan could not be read"
            return {"summary": "", "app": app or "", "needs_answer": False, "steps": []}

    plan = cache_get(task, app) if use_cache else None
    if plan:
        run.from_cache = True
        pr.event("info", text=f"using a saved plan ({len(plan['steps'])} steps, no planning call)",
                 elapsed=time.time() - t0)
    else:
        plan = await make_plan(look)
    # A plan made without seeing the window guesses at its content: after the step
    # that opens the window, look and plan the rest from what's really there.
    blind = not look and not run.from_cache
    run.plan = plan
    pr.event("plan", summary=plan.get("summary", ""), steps=[s["say"] for s in plan.get("steps", [])],
             cached=run.from_cache, elapsed=time.time() - t0)
    app = plan.get("app") or app

    # 3. run the steps, no model calls
    reports: list[str] = []
    steps: list = plan.setdefault("steps", [])
    i = -1
    while i + 1 < len(steps):
        i += 1
        step = steps[i]
        try:
            args = json.loads(step.get("args_json") or "{}")
        except ValueError:
            args = {}
        tool = step["tool"]
        if tool not in NO_WINDOW_TOOLS and "window" not in args:
            if win is None and app:
                o = await call("observe", {"app": app}, quiet=True)
                m = re.search(r"WINDOW (w\d+)", o)
                win = m.group(1) if m else None
            if win:
                args["window"] = win
        tgt = step.get("target") or {}
        frame: dict = {}
        if any(tgt.get(k) for k in ("role", "label", "ident")):
            o = await call("observe", {"app": app, "window": win} if win else {"app": app}, quiet=True)
            eid = resolve(tgt, parse_tree(o))
            if eid is None:
                run.failed_step, run.failure = i, f"couldn't find {tgt} in the window"
                break
            if tool == "select_rows":
                args["elements"] = [eid]
            else:
                args["element"] = eid
        if on_step:
            if args.get("element") and args.get("window"):
                frame = json.loads(await harness("harness_element_frame",
                                                 {"window": args["window"], "element": args["element"]}) or "{}")
            elif tool in ("select_menu", "menu") and args.get("path") and args.get("window"):
                frame = json.loads(await harness("harness_menu_frame",
                                                 {"window": args["window"], "title": args["path"][0]}) or "{}")
            elif args.get("window"):
                frame = json.loads(await harness("harness_window_frame", {"window": args["window"]}) or "{}")
            decision = await on_step(i, step, frame)
            if decision == "stop":
                run.failed_step, run.failure = i, "stopped by the person"
                res.status, res.evidence = "failed", "Stopped by you before: " + step["say"]
                res.elapsed_s = time.time() - t0
                return res, run
            if decision == "skip":
                continue
        text = await call(tool, args)
        if text.startswith("ERROR: STALE"):  # nothing was done; look again and retry once
            text = await call(tool, args)
        why = check(step, text)
        if why:
            run.failed_step, run.failure = i, why
            break
        run.steps_done += 1
        m = re.findall(r"\+ NEW WINDOW (w\d+)", text)
        if m:
            win = m[-1]
        m = re.match(r"WINDOW (w\d+)", text)
        if m:
            win = m.group(1)
        if "CLOSED as a result" in text and win and f"WINDOW {win} CLOSED" in text:
            win = None
        if step.get("report"):
            reports.append(f"{step['say']}:\n{text[:3000]}")
        if blind and tool in ("launch_app", "new_document") and i + 1 < len(steps):
            blind = False
            o = await call("observe", {"app": app, "window": win} if win else {"app": app}, quiet=True)
            if o.startswith("WINDOW"):
                win = re.match(r"WINDOW (w\d+)", o).group(1)
                rest = await make_plan(o, steps[: i + 1])
                steps[i + 1:] = rest.get("steps", [])
                plan["needs_answer"] = plan.get("needs_answer") or rest.get("needs_answer", False)
                pr.event("plan", summary="Now that the window is open, the rest of the plan:",
                         steps=[s["say"] for s in steps[i + 1:]], cached=False, elapsed=time.time() - t0)

    if run.failed_step is None and plan.get("steps"):
        # 4a. clean run: answer if asked (one small call), remember the plan
        answer = ""
        if plan.get("needs_answer") and reports:
            resp = await client.messages.create(
                model=model, max_tokens=1000,
                messages=[{"role": "user", "content": f"Task: {task}\n\nWhat the program read:\n\n"
                           + "\n\n".join(reports) + "\n\nReply with only the answer the task asks for, "
                           "exactly as it appears."}],
                output_config={"effort": "low"})
            run.planning_calls += 1
            res.input_tokens += resp.usage.input_tokens
            res.output_tokens += resp.usage.output_tokens
            answer = next((b.text for b in resp.content if b.type == "text"), "").strip()
        res.status, res.answer = "done", answer
        res.evidence = " ".join(f"{s['say']} ✓" for s in plan["steps"]) + \
            " Every step's result showed what it should" + \
            (" (including the save on disk)." if any("saved" in s["expect"] for s in plan["steps"]) else ".")
        if not run.from_cache:
            cache_put(task, app, {k: plan[k] for k in ("summary", "app", "needs_answer", "steps")})
        pr.event("finish", status="done", answer=answer, evidence=res.evidence, elapsed=time.time() - t0)
    else:
        # 4b. something diverged: the normal agent loop continues from here
        run.fell_back = True
        if run.from_cache:
            cache_put(task, app, None)  # this plan no longer fits; plan afresh next time
        i = run.failed_step if run.failed_step is not None else 0
        pr.event("info", text=f"plan stopped at step {i + 1} ({run.failure}); continuing step by step",
                 elapsed=time.time() - t0)
        done_lines = "\n".join(f"{n + 1}. {s['say']} ✓" for n, s in enumerate(plan.get("steps", [])[:i]))
        last = res.transcript[-1] if res.transcript else {}
        note = (f"{task}\n\n(I already started this with a pre-made plan.\n"
                f"Done so far:\n{done_lines or '(nothing yet)'}\n"
                f"It stopped at step {i + 1}"
                + (f" — \"{plan['steps'][i]['say']}\"" if plan.get("steps") and i < len(plan["steps"]) else "")
                + f": {run.failure}.\n"
                + (f"Last call: {last.get('name')} {json.dumps(last.get('input'))} →\n{str(last.get('result'))[:2500]}\n"
                   if last else "")
                + (f"Current window: {win}.\n" if win else "")
                + "Continue from the current state: observe first, don't redo steps that already worked.)")
        messages: list = [{"role": "user", "content": note}]
        await run_turns(session, client, tools_all + [FINISH_TOOL], messages, res, pr, t0, model=model,
                        effort=effort, fallbacks=fallbacks, max_turns=40)
    res.elapsed_s = time.time() - t0
    return res, run


async def run_planned(task: str, *, model: str, effort: str = "medium", timeout_s: float = 300,
                      quiet: bool = False, log_path: str | None = None, fallbacks: bool = True,
                      verbose: bool = False, use_cache: bool = True) -> tuple[RunResult, PlanRun]:
    """One task, planned first: the planning counterpart of harness.run_task."""
    import asyncio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from .display import C, Printer
    from .harness import _server_params
    pr = Printer(quiet, log_path, verbose=verbose)
    out: dict = {}

    async def go():
        async with stdio_client(_server_params()) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                await session.call_tool("harness_begin_task", {})
                out["r"] = await plan_and_run(task, session, anthropic.AsyncAnthropic(), pr, model=model,
                                              effort=effort, fallbacks=fallbacks, use_cache=use_cache)
    t0 = time.time()
    try:
        await asyncio.wait_for(go(), timeout=timeout_s)
        res, run = out["r"]
    except asyncio.TimeoutError:
        res, run = RunResult(task=task, status="timeout"), PlanRun()
    except Exception as e:
        inner = e
        while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
            inner = inner.exceptions[0]
        res, run = RunResult(task=task, status="error", error=f"{type(inner).__name__}: {inner}"), PlanRun()
        pr.event("info", text=f"error: {res.error}", elapsed=time.time() - t0)
    res.elapsed_s = time.time() - t0
    mode = ("saved plan" if run.from_cache else "planned") + (" + step-by-step recovery" if run.fell_back else "")
    pr.event("summary", status=res.status, elapsed=res.elapsed_s, tool_calls=res.tool_calls,
             tokens_in=res.input_tokens, tokens_out=res.output_tokens, mode=mode,
             planning_calls=run.planning_calls)
    if not quiet:
        print(f"{C['dim']}{res.elapsed_s:.1f}s · {mode} · {run.planning_calls} planning call(s) · "
              f"{res.tool_calls} tool calls · {res.input_tokens} in / {res.output_tokens} out tokens"
              + (f" · full log: {log_path}" if log_path else "") + C["x"])
    return res, run
