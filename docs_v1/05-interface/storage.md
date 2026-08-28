# Storage

Session state lives on disk so a conversation survives the process that started
it. This page covers where it goes and what survives.

```python
@dataclass(frozen=True)
class StorageConfig:
    root: Path | None = None
    persist: bool = True
```

## Layout

```
<root>/<workspace>/sessions/<session_id>/
  session.json                    the session record
  messages.jsonl                  conversation state, one message per line
  context-intelligence/
    events.jsonl                  the event log
    metadata.json                 the event log's own record
```

`<root>` is `StorageConfig.root`, defaulting to `~/.amplifier-agent/state/workspaces`.

`<workspace>` comes from the agent's workspace, which is derived from `cwd` when
you do not set it. Derivation resolves the path to absolute, replaces `/` and `\`
with `-`, drops `:`, and prefixes `-` if the result does not already start with
one. So `/home/alice/repos/api` becomes `-home-alice-repos-api`.

A session id containing `/`, `\`, or `..` is rejected with `config/invalid`.

## What each file is for

**`session.json`** is the session record. `list_sessions` reads these and nothing
else, which is why listing a thousand sessions does not load a thousand
transcripts.

```python
@dataclass(frozen=True)
class SessionRecord:
    format: str          # "amplifier-agent-session"
    version: str         # "1"
    session_id: str
    workspace: str
    parent_id: str | None
    working_dir: str
    started_at: datetime
    last_event_at: datetime
    ended_at: datetime | None
    status: Literal["running", "completed", "failed", "cancelled"]
    turn_count: int
    usage: Usage
```

`format` and `version` come first because a reader checks them before
interpreting anything else. A record it does not recognize is refused rather than
parsed into something plausible and wrong.

**`messages.jsonl`** is the conversation. It is the only resume-critical file:
`resume_session` reads it and needs nothing else. It is written durably as each
turn completes, so a process that dies between turns loses no completed turn.

**`context-intelligence/`** is the recording. It is observational, never required
to resume, and covered in [Context Intelligence](../04-context-intelligence.md).

## Persistence

`persist=True`, the default, writes conversation state durably as each turn
completes. Sessions outlive the agent and can be resumed later.

`persist=False` writes nothing under this layout. Sessions exist only in memory
for the life of the `Session` object, and `resume_session` raises
`session/not_found` for anything not still live in the process. Use it for
throwaway work, for tests, and anywhere a transcript on disk is a liability.

`delete_session` removes the whole session directory.

Nothing outside a session directory is part of this interface. The layout inside
one is what other tools can rely on.

## Reading a session directory yourself

The layout is stable enough to read directly, which is often the simplest way to
build reporting or auditing on top of an agent.

```python
import json
from pathlib import Path

session_dir = root / workspace / "sessions" / session_id

record = json.loads((session_dir / "session.json").read_text())
assert record["format"] == "amplifier-agent-session" and record["version"] == "1"

messages = [json.loads(line) for line in (session_dir / "messages.jsonl").read_text().splitlines()]
```

Check `format` and `version` before you parse. That check is the reason the fields
exist, and it is what lets the layout change later without your reader silently
misinterpreting a file it does not understand.

## Errors

- **`storage/unavailable`** session state could not be read or written while
  `persist` is on. Retryable.
- **`config/invalid`** a session id contained a path separator or `..`.
- **`session/not_found`** no session exists under that id.

See [Errors](errors.md) for the full registry.
