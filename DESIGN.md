# Design note

## What the model sees

**`observe(app, window)`** returns a header and a compact tree of that window.

- The header gives the window id, the app, the title, the document's file path, and the person's front app.
- The first time an app is observed, a short per-app note is added (e.g. TextEdit: "rich text by default; Format ▸ Make Plain Text for .txt; how the save sheet works; what *saved* looks like").
- Each tree line is one element: `[e36] TextField value="Untitled.txt" id=saveAsNameTextField (focused)`.
- Unlabelled layout groups are flattened. Rows are labelled by their cell text, so an 85-file Finder list is 85 lines.

Element ids belong to one window and to one AX element. Identity comes from `CFEqual` on the AXUIElement, not from the element's position in the tree. The same element keeps its id across observations, and an id is never handed to a different element.

**Every action** returns four things:

```
ACTION click e92 Button "Save": performed AXPress
focus ok: front app stayed 'Stickies'; cursor unmoved [135 samples over 0.83s]
CHANGES in w1:
  ~ window title: "Untitled.txt" → "meeting-notes.txt"
  - [e35] Sheet "save"  (+42 descendants)
  ~ [e29] MenuButton "document actions" title: 'Edited' → None
DISK: /Users/…/AXBench/t3/meeting-notes.txt was written (size 82 bytes, 0.5s ago)
```

1. What was sent.
2. A focus report. The front app and the cursor are sampled every 5 ms *during* the action, not only before and after it.
3. The diff of the window's tree after it settles. The server polls until two snapshots in a row match, or a quiet timeout passes. Large subtrees and long runs of rows are collapsed. Text edits are shown as one replaced span with its offset and context.
4. For document windows, whether the file on disk changed (mtime and size).

New windows of the app are reported with fresh window ids. A window that closes is reported as closed.

## How it knows an action worked

"Accepted" is never treated as "done":

- The diff is the primary signal. If nothing changed, the result says `NO CHANGE … do NOT assume it worked`.
- Text writes (`set_value`, `replace_text`, `insert_text`, `select_rows`) read the value back and compare it with the expected full text.
- Some apps (Stickies) have no Save command and write the file by themselves about 2 s after an edit, which is after an action's result is returned. `wait_for_save(window)` polls the document until it is newer than the state before the last edit, and answers `SAVED` or `NOT SAVED`. Files inside another app's protected container can't be listed or read, but their known member files (e.g. `TXT.rtf` in an `.rtfd`) can still be checked for time and size. When even that fails, the result says the saved state is `UNKNOWN`, never "unchanged".
- Saves are checked against the disk, because a save can look successful in the UI and write nothing. The first thing this project found: in a background app, `AXPress` on File ▸ Save returns success and does nothing, because AppKit validates menus against the *key window* and a background app has none.
- The system prompt makes the model verify the outcome in the UI before `finish(status="done")`. The benchmark ignores that claim anyway and checks files and app state itself.

## Background operation

Nothing activates an app, raises a window, moves the cursor or posts to the global event stream.

- **Synthetic focus.** Before acting, the server sends two SkyLight event records to the *target process only* (`SLPSPostEventRecordTo`): "you are active" and "this window is key". The app then validates menus and routes keys as if it were in front. The WindowServer's front process is untouched, and that is what decides where the person's real keystrokes go. No "deactivate" record is ever sent to the person's app. This is a private API. If it fails, the action is refused rather than attempted.
- **Input.** Presses, menu items, values, selections and scrolling go through AX actions and attributes. Keys go through `CGEventPostToPid` with a private event source, so the person's held modifiers and ours never mix. Open and save panels are drawn by a separate per-app XPC service (`openAndSavePanelService`), so when a panel is up, keys go to that process. That is how cmd+shift+g reaches "Go to folder".
- **Launching.** If the app a task needs isn't running, `launch_app(app, document?)` starts it with `open -g`, which never brings it forward. The tool waits for its window, keeps watching focus for 1.5 s afterwards (some apps activate themselves late), and reports both. Launches made this way aren't flagged as surprises by the session watch. Opening files through Finder is still refused, because that puts the file's app in front.
- **Who moved focus.** The front app can change because the person switched apps or because of something the agent did. The server tells these apart using the HID system's per-type "seconds since the last hardware event" counters (key down, clicks, mouse moves, scrolling). The agent's keys are posted straight to one process and never touch those counters. This was measured: its arrow keys do reset the catch-all "any input" counter, so that one isn't used. A switch to an app that was already running, within 1.5 s of real input, is reported to the model as the person's switch (`NOTE: the person switched apps themselves…`). The target app, or a newly launched app, coming forward is always `FOCUS MOVED`, even with input nearby, so a person typing nonstop can't hide a real steal. The benchmark doesn't use this attribution: it counts any change of front app as a focus move.
- **Focus truth.** `GetFrontProcess` is live in any thread and takes about 10 µs. `NSWorkspace.frontmostApplication` is stale without a run loop. The target's own `AXFrontmost` says `true` once it has been synthetically focused, so it can't be trusted either.

## Safe failure

Before any action, the server re-reads the window and compares a signature of everything the model can see against the model's last view:

- **Window closed or app quit** → `WINDOW GONE`, nothing done.
- **Anything changed since the last look** → `STALE`, nothing done. The changes are returned and become the model's new view.
- **Target id not in the current tree, or its element destroyed** → `ELEMENT GONE`.
- Animating progress indicators are excluded from the signature so they can't cause false staleness.

`bench/safety.py` exercises each case on real TextEdit windows.

Added after the first benchmark run (see RESULTS.md, t4#3):

- **Stale checks are target-aware.** A change refuses the action only if it adds or removes elements, changes the window's title or document, or touches the target element (or its ancestors or descendants, or the focused element). Other value changes, like Finder's "GB available" line, come back with the result as a `NOTE`. Under the strict rule they caused 17 needless refusals in run A and slowed t4.
- **Session-wide focus watch.** Some actions have delayed effects. Opening a file in Finder launches an app that takes focus seconds later, after that action's own focus window has closed. A background thread now watches the front app and app launches for the whole session and reports them on the next tool call.
- **Finder "open" guard.** cmd+down, cmd+o, File ▸ Open and AXOpen are refused when the selection includes files, because they would launch those files' app in front of the person.

## One approach ruled out: activate, act, restore

The obvious way to drive a background app is to bring it forward for a moment, send keys or clicks, then re-activate the person's app. I ruled it out on purpose:

- It moves focus by definition, even if only for 100 ms. That is exactly what a before/after focus check misses.
- Any keystroke the person makes in that window lands in the wrong app, and nothing can undo that.

Synthetic focus plus per-process events gets the app's cooperation without the WindowServer ever changing its front process.

Other approaches tried and dropped along the way:

- **Ids based on tree position.** When the save panel expanded, `e18` quietly went from the name field to the search field. Now `CFEqual` identity is used.
- **Posting keys to the app while a save sheet is open.** The keys are dropped, because the panel lives in another process.
- **Presetting the save panel's folder in TextEdit's preferences.** Those preferences live in a protected app container.

## Known limits

- **Private API.** SkyLight records are private and could change in a macOS update.
- **Save panel file browser.** Its column contents are not exposed over AX. Folders are reached through Go to Folder or the sidebar.
- **Finder moves use the clipboard.** Copy, then Move Item Here overwrites the person's clipboard. Saving and restoring the pasteboard around the move would fix this. Not done.
- **Cursor movement.** The server never generates mouse events, so a cursor move is always the person. Benchmark runs are unattended.
- **Focus attribution is a heuristic.** If the person happens to touch the keyboard or mouse in the same 1.5 s that an agent action brings forward an app that is *already running* and isn't the target (e.g. a file opening in an app that's already open), the switch would be blamed on the person. The Finder open guard blocks the known way this happens.
- **The "person" app.** Stickies stands in for Notes, so that runs never create notes in the user's real, possibly synced, Notes account.
