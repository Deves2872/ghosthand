"""run_command: run a shell command in a real Terminal window, only after the
person approves that exact command.

The model can propose a command, but it can't run one: the tool runs here, on
the buddy's side, and every call goes through `confirm`. In the app that is a
Run / Cancel box in the chat panel; in a terminal it is a y/N prompt; anything
else (no one to ask) is refused. Risky commands are flagged so the person
reads them twice.
"""
from __future__ import annotations

import asyncio
import re
import sys
from typing import Awaitable, Callable

TOOL = {
    "name": "run_command",
    "description": ("Run a shell command in a new Terminal window, so the person can see it. The person must "
                    "approve the exact command first, every time. Returns whether it finished and the window's "
                    "text (the output). Use simple, standard commands; never chain in deletion or sudo unless the "
                    "task asks for it explicitly. `why` is one plain sentence for the person."),
    "input_schema": {"type": "object", "additionalProperties": False,
                     "properties": {"command": {"type": "string"}, "why": {"type": "string"}},
                     "required": ["command", "why"]},
}

_RISKS = [
    (r"(^|[;&|\s])sudo\b", "runs as administrator (sudo)"),
    (r"(^|[;&|\s])rm\s", "deletes files (rm)"),
    (r"(^|[;&|\s])(mv|cp)\s.*\s(/|~)", "moves or copies files over others"),
    (r"(^|[^>])>(?!>)\s*\S", "overwrites a file (>)"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh", "runs a script downloaded from the internet"),
    (r"(^|[;&|\s])(dd|mkfs|diskutil\s+erase|fdisk)\b", "can erase a disk"),
    (r"(^|[;&|\s])(chmod|chown)\s+-R", "changes permissions of many files"),
    (r"(^|[;&|\s])(kill|killall|pkill)\b", "stops running programs"),
    (r"(^|[;&|\s])(shutdown|reboot|halt)\b", "restarts or shuts down the Mac"),
    (r"git\s+(push\s+.*--force|reset\s+--hard|clean\s+-[a-z]*f)", "can throw away git work"),
    (r"(^|[;&|\s])(launchctl|defaults\s+write|csrutil|spctl|tccutil)\b", "changes system settings"),
    (r"(^|[;&|\s])(brew|pip|npm|gem)\s+(install|uninstall)", "installs or removes software"),
]


def risks(command: str) -> list[str]:
    return [why for pat, why in _RISKS if re.search(pat, command)]


Confirm = Callable[[str, str, list], Awaitable[bool]]


async def confirm_in_terminal(command: str, why: str, risky: list) -> bool:
    if not sys.stdin.isatty():
        return False
    print(f"\n  The agent wants to run this in Terminal ({why}):\n\n      {command}\n")
    if risky:
        print("  ⚠ " + "; ".join(risky))
    ans = await asyncio.to_thread(input, "  Run it? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


confirm: Confirm = confirm_in_terminal  # the app replaces this with its Run / Cancel box


def _applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def run_command(command: str, why: str = "", timeout_s: float = 120) -> str:
    command = command.strip()
    if not command:
        return "ERROR: empty command."
    risky = risks(command)
    if not await confirm(command, why, risky):
        return "NOT RUN: the person didn't approve this command. Don't try another one unless they ask."
    script = f'''tell application "Terminal"
    set t to do script {_applescript_string(command)}
    delay 0.4
    set waited to 0
    repeat while (busy of t) and waited < {int(timeout_s)}
        delay 0.25
        set waited to waited + 0.25
    end repeat
    set out to history of t
    if busy of t then
        return "STILL RUNNING" & linefeed & out
    end if
    return "FINISHED" & linefeed & out
end tell'''
    proc = await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        e = err.decode(errors="replace").strip()
        if "-1743" in e or "not allowed" in e.lower():
            return ("ERROR: AUTOMATION PERMISSION needed: macOS must allow controlling Terminal. Nothing was run.")
        return f"ERROR: couldn't run it in Terminal: {e[:300]}"
    text = out.decode(errors="replace")
    status, _, history = text.partition("\n")
    tail = history.strip()[-3500:]
    head = ("COMMAND FINISHED" if status.strip() == "FINISHED" else
            f"COMMAND STILL RUNNING after {int(timeout_s)}s (left running in its Terminal window)")
    return f"{head}: {command}\nTerminal window text (end):\n{tail}"
