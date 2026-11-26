# Mac Buddy

A helper for your Mac. Hold **⌘ and double-click** anywhere, and a chat opens next to your pointer. Ask for anything in any app, by typing or talking. Mac Buddy either does it **in the background**, without touching your keyboard, pointer or front app, or **shows you how** with a ghost pointer that glides to each button.

**Not a developer?** Read the [guide](GUIDE.md).

## How it's built

- **`mac_buddy/app/`**: the menu bar app.
  - `main.py`: the triggers (⌘ double-click, ⌃⌥Space, the menu) and onboarding.
  - `panel.py`: the chat panel. It never takes your keyboard while it works.
  - `overlay.py`: the tutorial's ghost pointer, highlight and captions.
  - `voice.py`: local Whisper speech-to-text.
  - `perms.py`: one-click permission popups that open the exact Settings pane.
  - `keychain.py`: the API key, stored in the Keychain.
  - `worker.py`: runs requests on a background thread.
- **`mac_buddy/planner.py`**: plans a task **once**, runs every step without the model, and checks each result. It goes back to the model only when something doesn't go to plan, and it remembers plans that worked.
- **`mac_buddy/server.py`**: an MCP server (stdio). It observes one window through the accessibility tree and acts on it: click, set or replace text, keys, menus, scroll, select rows. After every action it returns what changed in that window, a focus report, and whether the document's file on disk changed.
- **`mac_buddy/harness.py`** and **`chat.py`**: the terminal versions, for a single task or a chat, with live output and a JSONL log.
- **`mac_buddy/bench/`**: 8 benchmark tasks with synthetic data, independent checkers, a simulated person who types in another app, scripted reference solutions and safe-failure checks.

Design note: [DESIGN.md](DESIGN.md). Results: [RESULTS.md](RESULTS.md).

## The app

```bash
uv sync
uv run python scripts/build_app.py --install   # builds Mac Buddy.app and copies it to ~/Applications
open ~/Applications/"Mac Buddy.app"
```

It asks for your API key (stored in the Keychain) and for Accessibility permission, each in a single popup. For development, `uv run mac-buddy-app` runs it straight from the source.

**Voice** uses [whisper.cpp](https://github.com/ggerganov/whisper.cpp) (through `pywhispercpp`) on the Mac. The `base` model (~148 MB) is downloaded on first use into `~/Library/Application Support/mac-buddy/models`. Set `MAC_BUDDY_WHISPER=tiny` for a smaller model, or `small` for a more accurate one.

## Setup

Requirements: macOS 14 or later (developed on macOS 27), [uv](https://docs.astral.sh/uv/), and an Anthropic API key.

```bash
uv sync
```

The process that runs the server needs **Accessibility** permission. Go to System Settings → Privacy & Security → Accessibility and enable the app you launch it from (Terminal, iTerm, Claude, …).

To be walked through it, run `uv run mac-buddy-setup`. It shows the system's permission dialog, opens the Accessibility pane of System Settings, names the app to turn on, and waits until it is on. macOS doesn't let a program grant this to itself, so you turn on the switch. `mac-buddy` and `mac-buddy-bench` run the same check before they call the model.

Put the key in `.env` in this folder (it is git-ignored):

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

## Command line

`mac-buddy "task"` plans first. It needs one model call (two if it has to open the window before it can see it), then runs the steps, and a repeated request reuses the saved plan with **no** model calls. Use `--no-plan` to ask the model after every step instead, as the benchmark does, and `--no-cache` to neither reuse nor save plans.

Open any TextEdit document. Keep typing in another app if you like. Then run:

```bash
uv run mac-buddy "In TextEdit, add the line 'checked by agent' at the end of the open document and save it."
```

The output is one line per step: the call, then what it did.

```
   8.5s insert_text e3 "log test"                     ✓ 5 changes · focus ok
   9.1s press_key cmd+s                               ✓ 4 changes · saved notes.txt (20 B) · focus ok
  14.8s ■ DONE  Added 'log test' to the end of ~/AXBench/demo/notes.txt and saved it.
```

- **✓** means the action changed something.
- **⚠** means it changed nothing visible, or the front app changed between steps (which may be you switching apps).
- **✗** means an error, or focus moved during the action.

Anything that needs attention (ERROR, STALE, FOCUS MOVED, NO CHANGE, NOT SAVED…) is printed in full under its line. Use `-v` to see every tree and diff.

Every run is also saved in full to `~/Library/Logs/mac-buddy/` as a JSONL file; the last line of the output names it. To replay a saved run:

```bash
uv run mac-buddy-log           # the latest run, compact
uv run mac-buddy-log -v        # …with every result in full
uv run mac-buddy-log --list    # recent logs
```

Options: `--model`, `--effort low|medium|high|xhigh|max` (default medium), `--timeout`, `--log path.jsonl`, `-v`.

### Chat mode

Run `mac-buddy` with no task to chat with it in the terminal:

```bash
uv run mac-buddy
```

Type one request at a time. The agent's work streams live, as above. The server and the conversation stay alive between requests, so a follow-up like "undo that" or "now save a copy of it" knows what you mean. You can keep using other apps between requests and while it works.

- `/new` starts a fresh conversation.
- `/effort low|medium|high|xhigh|max` changes the effort level.
- `/verbose` turns full output on or off.
- `/log` shows where this chat's log is saved.
- `/usage` shows the tokens used so far.
- **Ctrl-C** stops the current request but keeps the chat. The agent is told it was interrupted, so it checks the apps before continuing.
- **Ctrl-D** or `/exit` quits.

`uv run mac-buddy-chat` does the same thing.

The history is append-only, because the API ties each thinking block to the exact conversation before it. Long chats therefore grow, but most of the re-sent history is cheap prompt-cache reads. Use `/new` when you move on to something unrelated.

To use the server from any MCP client:

```json
{ "mcpServers": { "mac-buddy": { "command": "uv", "args": ["--directory", "/path/to/mac-buddy", "run", "mac-buddy-server"] } } }
```

## Reproduce the benchmark

The benchmark takes over the Mac. Each run brings Stickies (the "person") to the front, and task 8 types into it with real key events. Don't touch the keyboard or mouse while it runs. The simulated person stops typing the moment Stickies stops being the front app, so it can never type into anything else.

```bash
uv run mac-buddy-bench --runs 5 --timeout 300          # all 8 tasks → results/<timestamp>/
uv run mac-buddy-bench --tasks t5,t8 --runs 3          # a subset
uv run mac-buddy-bench --report results/<timestamp>    # rebuild report.md from runs.jsonl
```

Every run starts from the same state. The task's folder under `~/AXBench/` is regenerated from a fixed seed, TextEdit is quit and relaunched in the background with the task's document, and the task's Finder window is reopened in the background. A separate focus monitor in the runner samples the front app and the cursor every 5 ms for the whole run. Pass/fail comes only from the checkers, which read the files and app state. The agent's `finish` message is recorded so it can be compared, and nothing else.

Outputs: `results/<ts>/runs.jsonl` (one record per run), `report.md`, and `logs/<task>-<n>.jsonl` (the full event stream of each run).

Checks that use no model:

```bash
uv run python -m mac_buddy.bench.safety          # stale / element gone / window gone / no-op detection
uv run python -m mac_buddy.bench.oracle t3       # scripted save-panel flow through the server's tools
uv run python -m mac_buddy.bench.oracle t8       # scripted background edit+save while the person types
```

## Tasks

| id | level | apps | task | the checker verifies |
|---|---|---|---|---|
| t1 | easy | TextEdit | report the qty and bin of one SKU among 150 lines (with a near-miss decoy) | answer matches the data; document unchanged |
| t2 | easy | Finder | rename `report-draft.txt` → `report-final.txt` | the whole folder: same hashes, only that name changed |
| t3 | easy | TextEdit | type 3 lines and save as plain text in a given folder | file exists, not RTF, exact content |
| t4 | medium | Finder | move the 6 `*-hold.csv` files out of an 85-item list into `on-hold/` | every file's location and hash |
| t5 | medium | TextEdit | change clause 7's "Net 30" (but not clause 12's) and save as a copy | original byte-identical; copy = original with exactly that edit |
| t6 | medium | TextEdit | save as `summary.txt`; a "replace?" alert interrupts; save as `summary-2.txt` instead | existing file untouched; new file correct |
| t7 | hard | Finder → TextEdit | list the `-Acme.pdf` invoices (not `Acme-Labs`) from a Finder window into a saved document | lines equal the folder listing, re-read at check time |
| t8 | hard | TextEdit while a person types in Stickies | change one order's status and save | file correct; every typed character in the note; none leaked into the doc; Stickies front for the whole run; agent saw `DISK: … written` |
