"""Accessibility permission: check it, and help the person grant it.

macOS never lets a program grant itself Accessibility. The most a program can
do is (1) ask the system to show its own "would like to control this computer"
dialog and (2) open the right pane of System Settings. The person flips the
switch. The permission belongs to the app that launched us (Terminal, iTerm,
VS Code, ...), not to python, so that is the app we name.

Run `mac-buddy-setup` (or `python -m mac_buddy.permissions`) to go through it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import ApplicationServices as AS

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def trusted() -> bool:
    return bool(AS.AXIsProcessTrusted())


def responsible_app() -> str:
    """Name of the .app this process was started from, e.g. 'Terminal'."""
    pid = os.getpid()
    for _ in range(20):
        # args, not comm: a bundle whose launcher is a script shows up as plain "bash" in comm
        out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            break
        ppid, _, comm = out.partition(" ")
        if ".app/" in comm:
            name = comm.split(".app/")[0].rsplit("/", 1)[-1]
            if name != "Python":  # the interpreter's own bundle, not the launcher
                return name
        if not ppid.strip().isdigit() or int(ppid) <= 1:
            break
        pid = int(ppid)
    return "your terminal app"


def request(wait_s: float = 120.0, interactive: bool = True) -> bool:
    """Make sure we have Accessibility. Returns True once granted.

    Shows the system prompt, opens System Settings at the Accessibility list,
    then waits for the switch to be turned on. Never touches the setting itself.
    """
    if trusted():
        return True
    app = responsible_app()
    print(f"mac-buddy needs Accessibility permission for '{app}' to read and control windows.",
          file=sys.stderr)
    if not interactive:
        print(f"Grant it in System Settings › Privacy & Security › Accessibility, "
              f"or run: mac-buddy-setup", file=sys.stderr)
        return False
    # The system's own dialog (shown once per app until it is in the list).
    AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
    subprocess.run(["open", SETTINGS_URL], check=False)
    print(f"  1. In the window that opened, turn on '{app}' (use + to add it if it isn't listed).\n"
          f"  2. Come back here. Waiting up to {int(wait_s)} s…", file=sys.stderr)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if trusted():
            print("Accessibility granted.", file=sys.stderr)
            return True
        time.sleep(1.0)
    print(f"Still not granted. If you just turned it on, quit and reopen '{app}' "
          f"(macOS applies it on restart), then try again.", file=sys.stderr)
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Check and request the permissions mac-buddy needs.")
    ap.add_argument("--wait", type=float, default=120.0, help="seconds to wait for the switch")
    a = ap.parse_args()
    if trusted():
        print(f"Accessibility: granted (for '{responsible_app()}'). Nothing to do.")
        sys.exit(0)
    sys.exit(0 if request(wait_s=a.wait) else 1)


if __name__ == "__main__":
    main()
