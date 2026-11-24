"""Interactive chat in the terminal: type requests, watch the agent work, follow up.

    uv run ghosthand            # no task → chat
    uv run ghosthand-chat

One MCP server and one conversation stay alive for the whole session, so
follow-ups ("now save a copy of it") know what "it" is. Ctrl-C stops the
current request and keeps the chat; Ctrl-D or /exit quits.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import anthropic
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from .display import new_log_path
from .harness import (C, FINISH_TOOL, MODEL, SYSTEM, Printer, RunResult, _server_params,
                      load_dotenv, model_tools, run_turns)

CHAT_SYSTEM = SYSTEM + """

This is an ongoing chat with the person in their terminal. Each message is a new request or a follow-up to earlier ones ("it", "that file" refer to earlier work). Window and element ids from earlier requests may be out of date: observe again before acting on them. For a request that needs work in apps, work as above and end with finish. For a question you can answer from the conversation alone (e.g. "what did you change?"), reply in plain text without tools."""

HELP = f"""{C['b']}Commands{C['x']}
  /new            start a fresh conversation (the agent forgets earlier requests)
  /effort LEVEL   low | medium | high | xhigh | max   (now: {{effort}})
  /verbose        show every result in full (again: back to one line per step)
  /log            where the full log of this chat is saved
  /usage          tokens used in this session
  /help           this help
  /exit           quit (or Ctrl-D).  Ctrl-C stops the current request."""

# The history is append-only: the API ties each thinking block to the exact
# conversation before it, so editing earlier messages (e.g. trimming old tool
# results to save tokens) makes the next request fail. Re-sent history is
# mostly prompt-cache reads; /new starts over when it gets long.


def _add_user_text(messages: list, text: str):
    if messages and messages[-1]["role"] == "user":
        last = messages[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"].append({"type": "text", "text": text})
    else:
        messages.append({"role": "user", "content": text})


async def chat(model: str, effort: str, timeout_s: float, fallbacks: bool, log_path: str | None,
               verbose: bool = False):
    log_path = log_path or new_log_path("chat")
    pr = Printer(False, log_path, verbose=verbose)
    client = anthropic.AsyncAnthropic()
    loop = asyncio.get_running_loop()
    current: dict = {"task": None}

    def on_sigint(signum, frame):
        t = current["task"]
        if t is not None and not t.done():
            loop.call_soon_threadsafe(t.cancel)
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)
    try:
        import readline  # noqa: F401  (line editing and ↑ history for input())
    except ImportError:
        pass

    async with stdio_client(_server_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = model_tools(await session.list_tools()) + [FINISH_TOOL]
            messages: list = []
            pending_note = ""
            totals = {"in": 0, "out": 0, "requests": 0}
            print(f"{C['b']}ghosthand chat{C['x']} · {model} · effort {effort} · "
                  f"works in the background; you keep the front app.\n"
                  f"{C['dim']}Type a request, e.g. \"In TextEdit, add 'hello' to the open document "
                  f"and save it\". /help for commands, Ctrl-D to quit.{C['x']}")
            while True:
                try:
                    line = input(f"\n{C['b']}{C['c']}you ›{C['x']} ").strip()
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    print(f"\n{C['dim']}(Ctrl-D or /exit to quit){C['x']}")
                    continue
                if not line:
                    continue
                if line.startswith("/"):
                    cmd, _, arg = line.partition(" ")
                    if cmd in ("/exit", "/quit"):
                        break
                    elif cmd == "/new":
                        messages, pending_note = [], ""
                        print(f"{C['dim']}Fresh conversation.{C['x']}")
                    elif cmd == "/effort" and arg in ("low", "medium", "high", "xhigh", "max"):
                        effort = arg
                        print(f"{C['dim']}effort = {effort}{C['x']}")
                    elif cmd == "/verbose":
                        pr.verbose = not pr.verbose
                        print(f"{C['dim']}verbose {'on' if pr.verbose else 'off'}{C['x']}")
                    elif cmd == "/log":
                        print(f"{C['dim']}{log_path}  (replay: uv run ghosthand-log){C['x']}")
                    elif cmd == "/usage":
                        print(f"{C['dim']}{totals['requests']} requests · {totals['in']} in / "
                              f"{totals['out']} out tokens{C['x']}")
                    else:
                        print(HELP.format(effort=effort))
                    continue

                checkpoint = len(messages)
                merged = bool(messages) and messages[-1]["role"] == "user"
                _add_user_text(messages, (pending_note + line) if pending_note else line)
                pending_note = ""
                await session.call_tool("harness_begin_task", {})
                pr.event("user", text=line)
                res = RunResult(task=line)
                t0 = time.time()
                task = asyncio.ensure_future(run_turns(
                    session, client, tools, messages, res, pr, t0, model=model, effort=effort,
                    fallbacks=fallbacks, max_turns=60, system=CHAT_SYSTEM, chat=True))
                current["task"] = task
                why = ""
                try:
                    await asyncio.wait_for(task, timeout=timeout_s)
                except asyncio.CancelledError:
                    why = "stopped by you (Ctrl-C)"
                except asyncio.TimeoutError:
                    why = f"timed out after {timeout_s:.0f}s"
                except anthropic.APIError as e:
                    why = f"API error: {e}"
                finally:
                    current["task"] = None
                if why:
                    # The conversation may end mid tool call; drop this request's turns and
                    # tell the model next time, since some actions may already have happened.
                    del messages[checkpoint:]
                    if merged:  # our text went into the previous tool_result message
                        messages[-1]["content"].pop()
                    pending_note = (f"(Your work on my previous request — \"{line}\" — was interrupted: "
                                    f"{why}. Some actions may already have happened; observe before "
                                    "assuming anything.)\n\n")
                    print(f"{C['r']}■ {why}{C['x']}")
                totals["in"] += res.input_tokens
                totals["out"] += res.output_tokens
                totals["requests"] += 1
                pr.event("summary", status=res.status, elapsed=time.time() - t0, tool_calls=res.tool_calls,
                         tokens_in=res.input_tokens, tokens_out=res.output_tokens)
                print(f"{C['dim']}{time.time() - t0:.1f}s · {res.tool_calls} tool calls · "
                      f"{res.input_tokens} in / {res.output_tokens} out tokens{C['x']}")


def main(argv: list[str] | None = None):
    import argparse
    load_dotenv()
    ap = argparse.ArgumentParser(description="Chat with the macOS AX agent in your terminal.")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--timeout", type=float, default=300, help="seconds per request")
    ap.add_argument("--log", default=None, help="JSONL log path (default: a new file in ~/Library/Logs/ghosthand)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every result in full")
    ap.add_argument("--no-fallbacks", action="store_true")
    a = ap.parse_args(argv)
    from . import permissions
    if not permissions.request(interactive=sys.stdin.isatty()):
        sys.exit(2)
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        sys.exit("Set ANTHROPIC_API_KEY (or put it in .env).")
    try:
        asyncio.run(chat(a.model, a.effort, a.timeout, not a.no_fallbacks, a.log, a.verbose))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
