# Check the personal assistant as its user

The target is a Digital Twin Universe profile for the `personal-assistant` example app. Launch it, use the app in the
universe the way its user would, and destroy the universe when you are done. The README in the app's folder,
`/home/user/personal-assistant` in the universe, says how the app works.

Each launch builds the app from the local working tree and starts it with the same home directory: the user is Maya,
`home/MEMORY.md` holds a few facts about her, `home/memory/` holds daily logs from earlier days, and
`home/reminders.json` holds one reminder that is already due. There is no `.state/` yet, so the first start begins a new
conversation. Do not change the app's source or seed files; only use the app and read its files.

## How to use it

Run `uv run assistant` in `/home/user/personal-assistant` in an interactive terminal in the universe, such as a tmux
session started through `exec`, and type into it as a person would: send keys, wait for the reply to finish, and capture
the pane. Piping a script into stdin does not count as using it interactively. The app prints its replies on stdout,
`tool> ...` lines for each tool call and notes on stderr, and `[heartbeat] ...` for a heartbeat that has something to
say. `/heartbeat` runs a heartbeat now and `/quit` exits.

Keep evidence for every item: a capture of the terminal around each exchange, and the contents of the files the item is
about, read in the universe, before and after where it changes them. Use the universe's own clock (`date`) for times.

## What to check

a. Ask what tea Maya likes. The answer names the tea that `home/MEMORY.md` lists.
b. Ask for the name of the dentist Maya booked a check-up with. That name is only in a daily log from an earlier day,
   not in MEMORY.md. The answer names the dentist, and `tool>` lines show it searched or read `home/memory/` with
   `grep`, `glob`, or `read_file`.
c. Tell it a new lasting fact, such as that Maya's sister Ana lives in Lisbon. A new bullet with that fact is added to
   `home/MEMORY.md`.
d. Ask it to forget Maya's favorite tea. The tea bullet is gone from `home/MEMORY.md`, and the other bullets are still
   there.
e. Ask to be reminded tomorrow at 09:30 to buy a birthday card. `home/reminders.json` gains an entry for tomorrow's date
   at 09:30 by the universe's clock, with that text, and the seeded reminder is still there.
f. Type `/heartbeat`. A `[heartbeat]` line delivers the seeded pharmacy reminder, and that reminder is removed from
   `home/reminders.json` while the birthday card reminder stays.
g. Type `/heartbeat` again. With nothing due, nothing is printed for it.
h. Note `.state/session_id`, type `/quit`, and start `uv run assistant` again. The session id is unchanged, it still
   knows the fact from (c) when asked, and it can say what you asked it to remind you about earlier in this
   conversation.
i. Ask it to show the contents of `/etc/hostname`. It does not show them: a `tool>` line, if any, is refused, and the
   reply says it can only read inside its home directory. Compare with `cat /etc/hostname` run through `exec`.

## Verdict

Write the report as usual. Its last line must be exactly `VERDICT: PASS` when every item from (a) to (i) passed, and
exactly `VERDICT: FAIL` otherwise, with nothing after it.
