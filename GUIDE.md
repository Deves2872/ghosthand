# Mac Buddy: a helper for your Mac

Mac Buddy is a little helper that lives in your menu bar (look for the 👋 hand at the top of your screen). Ask it to do something in any app, in your own words, and it does it for you. Or ask it to **show you how**, and it points at each button while you follow along.

## Opening Mac Buddy

Any of these opens a small chat window right next to your pointer:

- Hold **⌘ Command** and **double-click** anywhere.
- Press **⌃ Control + ⌥ Option + Space**.
- Click the 👋 hand in the menu bar and choose **Ask Mac Buddy…**

Press **Esc** to close it.

## Asking for something

Type what you want and press **Return**, or hold the 🎤 microphone button and say it. Some examples:

- "In TextEdit, add the line 'call mom' to my notes and save it."
- "Make a new sticky note that says 'dentist on Friday'."
- "How many files are in my Downloads folder?"
- "Play Stranger Things on Netflix."
- "Take notes of this meeting."

Above the text box you can choose:

- **Do it for me.** Mac Buddy does the whole task in the background. You can keep working, because it doesn't take over your keyboard or your pointer.
- **Show me how.** Mac Buddy brings the app to the front, and a purple pointer glides to each button with a note saying what to do. For each step, click:
  - **Do it for me**, and Mac Buddy does that step while you watch;
  - **I did it**, once you've done the step yourself. Mac Buddy checks that it really happened before moving on;
  - **Stop**, to end the lesson.

  Want the buddy to move your *real* pointer instead? In the 👋 menu, choose **Use my real pointer in "Show me how"**. If you grab the mouse, it lets go right away.

When it finishes, Mac Buddy says **✓ Done** only after it has checked the result: for example, that the file was really saved. If something didn't work, it tells you what went wrong instead of pretending.

## Watching something

Ask in your own words, for example:

- "Play the Lofi Girl live stream on YouTube."
- "Resume The Rookie on Prime Video."
- "Play episode 3 of Stranger Things season 2 on Netflix."

Mac Buddy opens the site in a new Chrome tab in the background, finds the right title (skipping ads), presses Play or Resume, and then brings Chrome to the front so you can watch. Mac Buddy:

- **uses your existing sign-ins** and never types a password;
- **never buys, rents or subscribes**: if something isn't in your plan, it tells you;
- **asks which profile** if Netflix asks "Who's watching?" and you didn't say;
- **tells you if there's nothing to resume** (for example, you've finished the series), instead of guessing.

"Pause it" and "Is it still playing?" work too.

## Meeting notes

In a meeting you're part of (Google Meet in Chrome, Zoom or Microsoft Teams), say or type "Take notes of this meeting", or choose **Take meeting notes…** in the 👋 menu.

1. **Tell the others first.** Mac Buddy asks you to confirm you've told everyone that you're taking notes. It's the polite thing to do, and in many places the law requires it. Mac Buddy never joins meetings for you or hides that notes are being taken.
2. **It reads the captions.** It switches on the meeting's live captions if they're off, then reads them as they appear. It doesn't record any audio.
3. **A badge shows it's running.** "📝 Taking notes", with a timer, stays in the corner of your screen.
4. **Click Stop when you're done.** You get a summary, the decisions, the action items with who's doing them, and open questions. They're saved in **Documents ▸ Mac Buddy Notes**, along with the captions.

## Running a Terminal command

Ask things like "Open Terminal and check how much free disk space I have." Mac Buddy shows you the **exact command** and why it wants to run it, then waits for you to click **Run** or **Cancel**. It asks every time.

Commands that delete files, need your password (sudo), install software or change system settings are marked **⚠ Careful** in red. The command runs in a Terminal window you can see, and Mac Buddy reads the result back to you.

The first time Mac Buddy uses Chrome or Terminal, your Mac asks whether Mac Buddy may control it. Click **OK**.

## Talking instead of typing

Hold the 🎤 button, speak, and let go. Or click it once to start and once more to stop. Your words appear in the text box; check them and press **Return**.

Your voice is turned into text **on your Mac**, using the open-source Whisper speech model. Nothing is sent anywhere. The first time, Mac Buddy downloads the speech model (about 150 MB), which takes a few seconds.

## Permissions

The first time Mac Buddy needs something, it asks with a popup and a single button:

- **"Let Mac Buddy see and use your apps"** (Accessibility). Click **Open Settings**, and the right page of System Settings opens. Switch on **Mac Buddy**, and the buddy carries on by itself.
- **"Let Mac Buddy hear you"** (Microphone). Click **Allow Microphone**, then **Allow** in the box your Mac shows.

If you ever turn a permission off, Mac Buddy asks again the next time it needs it, right where you are.

## Getting started (one time)

1. Get an Anthropic API key at [console.anthropic.com](https://console.anthropic.com/settings/keys). This is what lets Mac Buddy understand you.
2. Open **Mac Buddy** from your Applications folder. (If someone set it up for you, it's already there.)
3. Paste the key when asked. It's stored safely in your Mac's Keychain.
4. Answer the permission popup.

To change the key later, click the 👋 hand and choose **API key…**.

## It gets faster

The first time you ask for something, Mac Buddy plans all the steps at once. If everything goes as planned, it remembers the plan, so the next time you ask for the same thing it starts straight away. To clear its memory, choose **Forget saved plans** in the 👋 menu.

## If something goes wrong

- **Nothing happens when I ⌘ double-click.** Check that Mac Buddy is running (the 👋 hand is in the menu bar), and that **Open with ⌘ + double-click** is ticked in its menu.
- **"I need permission to use your apps".** Choose **Check permissions…** in the 👋 menu and follow the popup.
- **"Your Anthropic account is out of credit".** Add credit at console.anthropic.com.
- To see exactly what Mac Buddy did, choose **Open activity log** in the 👋 menu.

---

*For developers: setup, the command-line tools and the design are in [README.md](README.md) and [DESIGN.md](DESIGN.md).*
