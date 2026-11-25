"""Harness: give the model a task, run it against the AX MCP server, and
stream every tool call and what changed to the terminal (and a JSONL log).

    uv run mac-buddy "In TextEdit, ..."            # one command
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

import anthropic
from .display import C, Printer, new_log_path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = os.environ.get("MAC_BUDDY_MODEL", "claude-opus-5-5")

SYSTEM = """You operate Mac apps for a person through their accessibility tree, using the tools provided. The person is using their computer at the same time: they keep the front app and their cursor. You work on windows in the BACKGROUND. Nothing you do may bring the target app forward, move the cursor, or type into the person's app; the tools are built so that this does not happen — if a result ever says FOCUS MOVED, stop and report it.

How to work:
- Start with observe(app). Element ids (e12) belong to one window (w1) and refer to one specific element. Use only ids from the latest results.
- Every action returns the CHANGES it caused in that window (+ added, - removed, ~ changed), a focus line and, for documents, whether the file on disk changed. Read them every time. An action being "accepted" is NOT evidence it worked; only the changes are.
- If a result starts with ERROR: STALE, WINDOW GONE or ELEMENT GONE, nothing was done. Re-read the situation (observe) before doing anything else. Never guess.
- Never repeat a write (typing, replacing, saving, moving) without first checking whether the previous one already landed — check the CHANGES or observe.
- If an unexpected dialog or sheet appears, handle it according to the task's instructions, then confirm the result.
- If an app the task needs is not running, start it with launch_app (it starts in the background), then observe it. Launch only apps, and open only documents, that the task needs.
- If the task refers to "the open document" (or similar) but the app has no document open and the task names no file, create one with new_document(app) and do the task in it. Don't open recent files or guess which file was meant. If the task says to save, save it as plain text in ~/Documents as "Untitled.txt"; if that name is taken use "Untitled 2.txt", "Untitled 3.txt", … — never replace an existing file. Report in `answer` that you created a new document and its full path.
- Never open files any other way: Finder's Open, cmd+o, cmd+down on a file or a double-click starts the file's app IN FRONT of the person. To enter a folder in Finder, select only that folder first.
- Prefer precise edits (replace_text, insert_text) over retyping whole documents.
- Before finishing, verify the outcome in the UI itself: e.g. after saving, the sheet is gone, the window title is the file name, it is not marked Edited, and the server reported the file was written; after an edit, read back the text.
- Finish by calling finish exactly once. status="done" only when you have verified the outcome; otherwise status="failed" with what is wrong. Do not claim success you have not seen. Put any value you were asked to report in `answer`, exactly as it appears in the app."""

FINISH_TOOL = {
    "name": "finish",
    "description": "End the task. status 'done' only if the outcome was verified in the UI; "
                   "'failed' if it could not be completed or verified.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["done", "failed"]},
            "answer": {"type": "string", "description": "Value(s) the task asked you to report, if any"},
            "evidence": {"type": "string", "description": "What you observed that shows the outcome"},
        },
        "required": ["status", "answer", "evidence"],
        "additionalProperties": False,
    },
}

@dataclass
class RunResult:
    task: str
    status: str = "no_finish"         # done | failed | no_finish | timeout | error
    answer: str = ""
    evidence: str = ""
    elapsed_s: float = 0.0
    tool_calls: int = 0
    turns: int = 0
    focus_moved_reports: int = 0
    stale_refusals: int = 0
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    transcript: list = field(default_factory=list)


def load_dotenv():
    """Read KEY=VALUE lines from the project's .env (if any) into os.environ."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "mac_buddy.server"],
                                 env={**os.environ, "PYTHONUNBUFFERED": "1"})


def model_tools(listed) -> list:
    """The server's tools as offered to the model (harness-only tools left out), plus finish."""
    tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
             for t in listed.tools if not t.name.startswith("harness_")]
    return tools


async def run_turns(session, client, tools: list, messages: list, res: RunResult, pr: "Printer",
                    t0: float, *, model: str, effort: str, fallbacks: bool, max_turns: int,
                    system: str = SYSTEM, chat: bool = False) -> None:
    """Call the model and run its tool calls until it calls finish (or, in chat
    mode, replies in plain text). `messages` is extended in place."""
    nudged = False
    for turn in range(max_turns):
        res.turns = turn + 1
        kwargs = dict(model=model, max_tokens=16000, system=system, tools=tools,
                      messages=messages, thinking={"type": "adaptive", "display": "summarized"},
                      output_config={"effort": effort}, cache_control={"type": "ephemeral"})
        if fallbacks:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        resp = await client.beta.messages.create(**kwargs)
        res.input_tokens += resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0) \
            + (resp.usage.cache_creation_input_tokens or 0)
        res.output_tokens += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            res.status, res.error = "error", "model refused"
            return
        uses = [b for b in resp.content if b.type == "tool_use"]
        for b in resp.content:
            if b.type == "thinking" and b.thinking:
                pr.event("thought", text=b.thinking[:400], elapsed=time.time() - t0)
            elif b.type == "text" and b.text.strip() and (uses or not chat):
                pr.event("thought", text=b.text[:400], elapsed=time.time() - t0)
        if not uses:
            if resp.stop_reason == "max_tokens":
                messages.append({"role": "user", "content": "Continue."})
                continue
            if chat:
                res.status = "reply"
                res.answer = "\n".join(b.text for b in resp.content if b.type == "text").strip()
                pr.event("reply", text=res.answer, elapsed=time.time() - t0)
                return
            if not nudged:
                nudged = True
                messages.append({"role": "user", "content":
                                 "You ended without calling finish. Call finish now with the "
                                 "status you have actually verified."})
                continue
            res.status = "no_finish"
            return
        results = []
        finished = None
        for u in uses:
            pr.event("call", name=u.name, input=u.input, elapsed=time.time() - t0)
            res.tool_calls += 1
            res.transcript.append({"name": u.name, "input": u.input})
            if u.name == "finish":
                finished = u.input
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": "ok"})
                continue
            if finished is not None:
                results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                "content": "not run: task already finished"})
                continue
            try:
                out = await session.call_tool(u.name, u.input)
                text = "\n".join(c.text for c in out.content if getattr(c, "type", "") == "text")
                is_err = bool(out.isError)
            except Exception as e:  # transport-level failure
                text, is_err = f"ERROR: tool call failed: {e}", True
            if "FOCUS MOVED" in text:
                res.focus_moved_reports += 1
            if text.startswith("ERROR: STALE"):
                res.stale_refusals += 1
            res.transcript[-1]["result"] = text
            pr.event("result", name=u.name, text=text, elapsed=time.time() - t0)
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": text,
                            **({"is_error": True} if is_err else {})})
        messages.append({"role": "user", "content": results})
        if finished is not None:
            res.status = finished.get("status", "failed")
            res.answer = finished.get("answer", "")
            res.evidence = finished.get("evidence", "")
            pr.event("finish", **finished, elapsed=time.time() - t0)
            return
    res.status = "no_finish"
    res.error = f"max_turns {max_turns} reached"


async def run_task(task: str, *, model: str = MODEL, effort: str = "medium",
                   timeout_s: float = 300, max_turns: int = 60, quiet: bool = False,
                   log_path: str | None = None, fallbacks: bool = True,
                   verbose: bool = False) -> RunResult:
    res = RunResult(task=task)
    pr = Printer(quiet, log_path, verbose=verbose)
    t0 = time.time()
    pr.event("info", text=f"task: {task}", elapsed=0)

    async def loop():
        async with stdio_client(_server_params()) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = await session.list_tools()
                tools = model_tools(listed) + [FINISH_TOOL]
                client = anthropic.AsyncAnthropic()
                messages: list = [{"role": "user", "content": task}]
                await run_turns(session, client, tools, messages, res, pr, t0, model=model,
                                effort=effort, fallbacks=fallbacks, max_turns=max_turns)

    try:
        await asyncio.wait_for(loop(), timeout=timeout_s)
    except asyncio.TimeoutError:
        res.status = "timeout"
        pr.event("info", text=f"TIMEOUT after {timeout_s}s", elapsed=time.time() - t0)
    except anthropic.APIError as e:
        res.status, res.error = "error", f"{type(e).__name__}: {e}"
        pr.event("info", text=f"API error: {res.error}", elapsed=time.time() - t0)
    except Exception as e:
        inner = e
        while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
            inner = inner.exceptions[0]
        res.status, res.error = "error", f"{type(inner).__name__}: {inner}"
        pr.event("info", text=f"harness error: {res.error}", elapsed=time.time() - t0)
    res.elapsed_s = time.time() - t0
    pr.event("summary", status=res.status, elapsed=res.elapsed_s, tool_calls=res.tool_calls,
             tokens_in=res.input_tokens, tokens_out=res.output_tokens)
    if not quiet:
        print(f"{C['dim']}{res.elapsed_s:.1f}s · {res.tool_calls} tool calls · "
              f"{res.input_tokens} in / {res.output_tokens} out tokens{C['x']}"
              + (f"{C['dim']} · full log: {log_path}{C['x']}" if log_path else ""))
    return res


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Run one task with the macOS AX agent and watch it live.")
    ap.add_argument("task", nargs="?", help="the task; leave it out to chat interactively")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--log", default=None, help="JSONL log path (default: a new file in ~/Library/Logs/mac-buddy)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every result in full")
    ap.add_argument("--no-fallbacks", action="store_true")
    ap.add_argument("--no-plan", action="store_true",
                    help="ask the model after every step instead of planning the task first")
    ap.add_argument("--no-cache", action="store_true", help="don't reuse or save plans")
    a = ap.parse_args()
    if a.task is None:
        from . import chat
        chat.main([f"--model={a.model}", f"--effort={a.effort}", f"--timeout={a.timeout}"]
                  + ([f"--log={a.log}"] if a.log else []) + (["--no-fallbacks"] if a.no_fallbacks else [])
                  + (["-v"] if a.verbose else []))
        return
    # Check before any model call: without it every tool fails and tokens are wasted.
    from . import permissions
    if not permissions.request(interactive=sys.stdin.isatty()):
        sys.exit(2)
    if not a.no_plan:
        from .planner import run_planned
        r, _ = asyncio.run(run_planned(a.task, model=a.model, effort=a.effort, timeout_s=a.timeout,
                                       log_path=a.log or new_log_path("task"), fallbacks=not a.no_fallbacks,
                                       verbose=a.verbose, use_cache=not a.no_cache))
        sys.exit(0 if r.status == "done" else 1)
    r = asyncio.run(run_task(a.task, model=a.model, effort=a.effort, timeout_s=a.timeout,
                             log_path=a.log or new_log_path("task"), fallbacks=not a.no_fallbacks,
                             verbose=a.verbose))
    sys.exit(0 if r.status == "done" else 1)


if __name__ == "__main__":
    main()
