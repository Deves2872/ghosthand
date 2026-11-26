"""Meeting notes from live captions, for meetings the person is in.

The note-taker never joins a meeting, never hides itself and never records
audio. It reads the captions the meeting app already shows on the person's
screen (Google Meet in Chrome, Zoom, Microsoft Teams), through the
accessibility tree, and turns them into notes when the person stops it. The
app asks the person to confirm they've told the other participants before it
starts, and shows a badge while it runs.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

from . import ax
from . import snapshot as S

NOTES_DIR = os.path.expanduser("~/Documents/Mac Buddy Notes")

# where meetings happen, and how their caption switch is labelled
SOURCES = [
    ("Google Chrome", re.compile(r"\bMeet\b|meet\.google\.com", re.I), ["Turn on captions", "Captions"]),
    ("Safari", re.compile(r"\bMeet\b|meet\.google\.com", re.I), ["Turn on captions", "Captions"]),
    ("zoom.us", re.compile(r"Zoom Meeting|Zoom Webinar|^Zoom$", re.I), ["Show Captions", "Captions", "CC"]),
    ("Microsoft Teams", re.compile(r"Meeting|Call|\|", re.I), ["Turn on live captions", "Live captions"]),
]
_CHROME_WORDS = re.compile(r"^(mic|camera|present|raise hand|more options|leave call|chat|people|activities|"
                           r"turn (on|off)|captions|you|reactions|meeting details|host controls|\d+:\d+( [AP]M)?)",
                           re.I)


def _is_name(t: str) -> bool:
    """"Priya", "Sam Lee", "Dana (Host)": 1–4 Capitalised words, no sentence punctuation."""
    words = t.split()
    return 0 < len(words) <= 4 and len(t) <= 32 and not re.search(r"[.?!,]", t) and \
        all(w[:1].isupper() or w[:1] == "(" for w in words)


@dataclass
class Line:
    t: float
    text: str


@dataclass
class Meeting:
    app: str
    title: str
    started: float = field(default_factory=time.time)
    lines: list[Line] = field(default_factory=list)


def find_meeting() -> tuple[ax.App, object, str] | None:
    """The meeting window on screen: (app, window, title), or None."""
    for name, pat, _ in SOURCES:
        a = ax.find_app(name)
        if a is None:
            continue
        for w in ax.windows(a):
            title = str(ax.get(w, "AXTitle") or "")
            if pat.search(title):
                return a, w, title
    return None


def turn_on_captions(app: ax.App, win) -> bool:
    """Press the app's own captions button if captions aren't on. Returns True if pressed."""
    labels = next((l for n, _, l in SOURCES if n == app.name), [])
    try:
        ax.set_attr(app.el, "AXEnhancedUserInterface", True)  # Chrome: expose the page
    except ax.AXError:
        pass
    time.sleep(1.0)
    for n in S.take(win, app.el).nodes:
        lab = (n.title or n.desc or "")
        if n.role == "AXButton" and any(lab.lower().startswith(l.lower()) for l in labels) \
                and "off" not in lab.lower():
            from . import synthfocus
            synthfocus.focus_window(app.pid, win)
            try:
                ax.perform(n.ref, "AXPress")
                return True
            except ax.AXError:
                return False
    return False


class NoteTaker:
    """Reads caption text every ~1.2 s on a background thread.

    Captions grow word by word and scroll away, and the page's layout shifts as
    they do, so lines are followed by their text, not their position: a line that
    grows replaces its shorter version, a short name right before a line is its
    speaker, and text that never changes from the first look to the last
    (buttons, the meeting title) is the app's own, not speech."""

    def __init__(self, app: ax.App, win, title: str):
        self.app, self.win = app, win
        self.meeting = Meeting(app=app.name, title=title)
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.first: set[str] = set()
        self.last: set[str] = set()
        self.live: dict = {}  # kept for the badge's line count
        self.error = ""

    def _texts(self) -> list[str]:
        snap = S.take(self.win, self.app.el)
        out = []
        for n in snap.nodes:
            if n.role != "AXStaticText":
                continue
            txt = " ".join(str(n.value or n.title or "").split())
            if len(txt) < 2 or _CHROME_WORDS.match(txt):
                continue
            out.append(txt)
        lines, i = [], 0
        while i < len(out):  # "Priya:" / "Priya" followed by what she said
            t = out[i]
            if i + 1 < len(out) and _is_name(t.rstrip(": ")):
                lines.append(f"{t.rstrip(': ')}: {out[i + 1]}")
                i += 2
            else:
                lines.append(t)
                i += 1
        return lines

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="note-taker")
        self.thread.start()

    def _run(self):
        first = True
        while True:
            try:
                now = self._texts()
            except ax.AXError:
                if not ax.alive(self.win):
                    self.error = "The meeting window closed."
                    break
                now = None
            if now is not None:
                if first:
                    self.first, first = set(now), False
                self.last = set(now)
                for txt in now:
                    self._keep(txt)
            if self.stop_evt.wait(1.2):
                break
        static = self.first & self.last  # on screen, unchanged, from start to end: the app itself
        self.meeting.lines = [l for l in self.meeting.lines if l.text not in static]

    @staticmethod
    def _said(t: str) -> str:
        """What was said, without a 'Name: ' prefix."""
        head, sep, rest = t.partition(": ")
        return rest if sep and _is_name(head) else t

    def _keep(self, txt: str):
        if txt.endswith(":"):  # a speaker's name on its own
            return
        lines = self.meeting.lines
        said = self._said(txt)
        for j in range(len(lines) - 1, max(-1, len(lines) - 10), -1):  # the last few lines
            old = lines[j].text
            old_said = self._said(old)
            if said == old_said or old_said.startswith(said):
                if txt != said and old == old_said:   # now we know who said it
                    lines[j] = Line(lines[j].t, txt)
                return                                # seen (or an earlier, shorter view of it)
            if said.startswith(old_said):
                lines[j] = Line(lines[j].t, txt if txt != said or old == old_said else old.replace(old_said, said))
                return                                # the same caption, grown
        lines.append(Line(time.time(), txt))

    def stop(self) -> Meeting:
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=5)
        return self.meeting


def transcript(m: Meeting) -> str:
    return "\n".join(f"[{time.strftime('%H:%M:%S', time.localtime(l.t))}] {l.text}" for l in m.lines)


SUMMARY_PROMPT = """These are the live captions from a meeting the person attended ("{title}", {when}, {mins} minutes). Captions can be garbled; don't invent anything that isn't there.

Write the person's meeting notes in Markdown with these sections:
## Summary (3–6 bullet points)
## Decisions
## Action items (one per line: "- [ ] who: what (when, if said)")
## Open questions
Leave a section as "- none" if the captions have nothing for it.

CAPTIONS:
{captions}"""


async def write_notes(m: Meeting, client, model: str) -> tuple[str, str]:
    """Summarize with one model call and save notes + transcript. Returns (path, notes)."""
    mins = max(1, round((time.time() - m.started) / 60))
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.started))
    caps = transcript(m)
    if not m.lines:
        notes = "_No captions were seen. Turn on the meeting's captions (CC) and try again._"
    else:
        resp = await client.messages.create(
            model=model, max_tokens=4000, output_config={"effort": "low"},
            messages=[{"role": "user", "content": SUMMARY_PROMPT.format(title=m.title, when=when, mins=mins,
                                                                        captions=caps[-60000:])}])
        notes = next((b.text for b in resp.content if b.type == "text"), "").strip()
    os.makedirs(NOTES_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", m.title)[:50].strip() or "Meeting"
    path = os.path.join(NOTES_DIR, f"{time.strftime('%Y-%m-%d %H%M', time.localtime(m.started))} {safe}.md")
    with open(path, "w") as f:
        f.write(f"# {m.title}\n\n*{when} · {mins} min · notes by Mac Buddy from {m.app}'s live captions*\n\n"
                f"{notes}\n\n---\n\n<details><summary>Captions</summary>\n\n```\n{caps}\n```\n</details>\n")
    return path, notes
