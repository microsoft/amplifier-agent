# Personal assistant example

`personal-assistant` is an always-on assistant that lives in one terminal process. You chat with it, and a heartbeat
wakes it every few minutes to deliver reminders and look at a checklist. Its memory is plain Markdown in a home
directory that you can read and edit. Chat lines and heartbeats feed one queue, and one worker runs one turn at a time.

## What it shows

Every use of `amplifier_agent` is in [`personal_assistant/agent/`](personal_assistant/agent/), and only its public
exports are used. Each file starts with a docstring that lists what it shows.

- [`session.py`](personal_assistant/agent/session.py): `AgentOptions`, `create_agent`, instructions rebuilt for every
  turn, a durable session resumed by id, an ephemeral session, `start_turn` and its events, `set_model`, `AgentError`
- [`tools.py`](personal_assistant/agent/tools.py): caller tools, tool errors the model corrects, and built-in tools
  named by string
- [`approvals.py`](personal_assistant/agent/approvals.py): an approval handler that decides on `request.arguments`, with
  denials returned to the model
- [`providers.py`](personal_assistant/agent/providers.py): a credential check with `list_providers`

The rest is application code that does not import the library. [`__main__.py`](personal_assistant/__main__.py) holds
the queue and the worker, [`heartbeat.py`](personal_assistant/heartbeat.py) decides without a model call whether a
heartbeat has anything to do, and [`memory.py`](personal_assistant/memory.py) reads and writes the home directory.
[`templates/`](templates/) seeds a new home directory.

It also shows how to set up the [Reality Check](https://github.com/microsoft/amplifier-smart-tool-reality-check) Smart
Tool to validate an app with Amplifier Agent: [`tests/reality-check/`](tests/reality-check/) pins it, seeds data in a
Digital Twin Universe, and checks the app end to end as its user would. [Test](#test) covers how to run it.

The home directory, `./home` unless you pass `--home`:

```
home/
  SOUL.md           personality and tone
  RULES.md          standing instructions
  HEARTBEAT.md      a checklist; each "- [ ] " line is an open item the heartbeat looks at
  MEMORY.md         lasting facts, one bullet each, sent with every turn
  memory/           one conversation log per day, named YYYY-MM-DD.md
  reminders.json    reminders that have not been delivered yet
```

The first run copies the templates and creates the rest. Files that exist are never overwritten, so edit any of them.
The conversation itself is stored in `.state/` beside the home directory.

## Run it

You need [uv](https://docs.astral.sh/uv/), Git, and `OPENAI_API_KEY`. From this folder:

```bash
uv run assistant
uv run assistant --home ~/assistant-home --heartbeat-minutes 5
```

The default model is `gpt-6-luna`; choose another with `--model`. In the chat, `/heartbeat` runs a heartbeat now and
`/quit` exits, as do Ctrl+D and Ctrl+C. Reminders are delivered by the first heartbeat after they fall due. Run
`uv run assistant --help` for every option.

## Run against the latest commit

`pyproject.toml` pins `amplifier-agent` to a revision of this repository. To use the library in your checkout
instead, run from the repository root:

```bash
uv run --directory examples/python/personal-assistant --with-editable ../../../packages/python assistant
```

## Test

```bash
uv run pytest
uv run tests/reality-check/check.py
```

`pytest` runs the unit tests of the approval policy.

[`tests/reality-check/`](tests/reality-check/) checks the app end to end, the way its user would, with the
[Reality Check](https://github.com/microsoft/amplifier-smart-tool-reality-check) Smart Tool used as a Python library.
The checker runs on Amplifier Agent with `openai/gpt-6.1-sol` at medium reasoning effort. It launches a
[Digital Twin Universe](https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe) from
[`profile/`](tests/reality-check/profile/), which builds the app from this folder as it is on disk and starts it with
the home directory in [`seed/home/`](tests/reality-check/seed/home/). It then chats with the assistant in the
universe's terminal and checks what [`request.md`](tests/reality-check/request.md) lists: recall, searching daily logs,
remembering and forgetting, reminders, heartbeats, a restart, and reading outside the home directory.

The check needs Docker and `OPENAI_API_KEY`, which the universe passes to the app. It takes several minutes and writes
its report and evidence under `.state/reality-checks/`. It prints the report's path and exits 0 only when the report
ends with `VERDICT: PASS`. The script pins Reality Check to commit `2408911ba70a19ef016623a0bf92a679ee5bf9c5` in its
inline metadata, and runs apart from this project because that version needs a different `amplifier-agent`.
