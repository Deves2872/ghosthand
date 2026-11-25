"""Putting apps into a known starting state without stealing focus."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import ax, synthfocus

ROOT = Path.home() / "AXBench"


def sh(*args, check=False, timeout=20):
    return subprocess.run(list(args), capture_output=True, text=True, check=check, timeout=timeout)


def osa(script: str):
    return sh("osascript", "-e", script)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_state(folder: Path) -> dict[str, str]:
    """relative path -> sha256 for every file under folder."""
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.name != ".DS_Store":
            out[str(p.relative_to(folder))] = sha(p)
    return out


def fresh_dir(name: str) -> Path:
    d = ROOT / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def wait_for(pred, timeout=8.0, interval=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    return None


# ------------------------------------------------------------------ TextEdit

def quit_textedit():
    if ax.find_app("TextEdit"):
        try:
            sh("osascript", "-e", 'tell application "TextEdit" to quit saving no', timeout=6)
        except subprocess.TimeoutExpired:
            pass   # a modal sheet blocks quitting; fall through to kill
        if not wait_for(lambda: ax.find_app("TextEdit") is None, timeout=5):
            sh("pkill", "-x", "TextEdit")
            if not wait_for(lambda: ax.find_app("TextEdit") is None, timeout=4):
                sh("pkill", "-9", "-x", "TextEdit")
                wait_for(lambda: ax.find_app("TextEdit") is None, timeout=4)
        time.sleep(0.3)


# Relaunch without restoring windows from a previous (possibly killed) session.
_NO_RESTORE = ["--args", "-ApplePersistenceIgnoreState", "YES"]


def _dismiss_open_panel(app):
    """TextEdit launched without a document shows an Open panel; cancel it."""
    for w in ax.windows(app):
        if str(ax.get(w, "AXTitle") or "") == "Open":
            synthfocus.focus_window(app.pid, w)
            for el in _desc(w):
                if ax.get(el, "AXRole") == "AXButton" and ax.get(el, "AXTitle") == "Cancel":
                    try:
                        ax.perform(el, "AXPress")
                    except ax.AXError:
                        pass
                    break
    wait_for(lambda: not any(str(ax.get(w, "AXTitle") or "") == "Open" for w in ax.windows(app)), 4)


def _desc(el, depth=0):
    if depth > 12:
        return
    for c in ax.get(el, "AXChildren") or []:
        yield c
        yield from _desc(c, depth + 1)


def textedit_open(path: Path):
    quit_textedit()
    sh("open", "-g", "-a", "TextEdit", str(path), *_NO_RESTORE)
    app = wait_for(lambda: ax.find_app("TextEdit"))
    wait_for(lambda: any(path.name in str(ax.get(w, "AXTitle") or "") for w in ax.windows(app)), 8)
    time.sleep(0.5)


def textedit_new(text: str = ""):
    quit_textedit()
    sh("open", "-g", "-a", "TextEdit", *_NO_RESTORE)
    app = wait_for(lambda: ax.find_app("TextEdit"))
    wait_for(lambda: ax.windows(app), 8)
    time.sleep(0.5)
    _dismiss_open_panel(app)
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    osa(f'tell application "TextEdit" to make new document with properties {{text:"{esc}"}}')
    wait_for(lambda: any(str(ax.get(w, "AXTitle") or "").startswith("Untitled") for w in ax.windows(app)), 6)
    time.sleep(0.4)


# -------------------------------------------------------------------- Finder

def close_finder_windows(titles: set[str]):
    app = ax.find_app("Finder")
    if not app:
        return
    for w in ax.windows(app):
        if str(ax.get(w, "AXTitle") or "") in titles:
            for b in ax.get(w, "AXChildren") or []:
                if ax.get(b, "AXSubrole") == "AXCloseButton":
                    try:
                        ax.perform(b, "AXPress")
                    except ax.AXError:
                        pass
    time.sleep(0.4)


def finder_open(folder: Path):
    close_finder_windows({folder.name})
    sh("open", "-g", str(folder))
    app = ax.find_app("Finder")
    wait_for(lambda: any(str(ax.get(w, "AXTitle") or "") == folder.name for w in ax.windows(app)), 8)
    time.sleep(0.6)
