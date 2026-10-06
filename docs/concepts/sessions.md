# Sessions

A session is one conversation. It holds the history that later turns can see, and it is
where the conversation is written down.

```
agent.create_session({ session_id?, persistence?, model?, reasoning_effort? })
SessionRecord { session_id, persistence, provider, model, reasoning_effort }
```

`model` and `reasoning_effort` replace the agent's defaults for this session; see
[models](models.md). `SessionRecord` holds the session's current provider, model, and
reasoning effort, which is `"medium"` when named nowhere.

## Switching models

```
session.set_model(provider, model, reasoning_effort?)
```

Switches the provider, model, and reasoning effort for every later turn. The
conversation carries over, including to another provider. An absent `reasoning_effort`
keeps the current value.

`set_model` loads the new provider before it returns, with credentials and endpoints
from the agent's [environment](agents.md#environment). When it fails, the session keeps
its previous selection.

```
a turn is active                                     busy, never queued
the session is closed                                closed
an unknown provider                                  invalid_input
the provider's package extra is missing              engine_unavailable
missing or rejected credentials, or a load failure   provider_failed
an unknown model                                     selector_rejected
a reasoning effort the new model does not take       selector_rejected
```

An unknown model can pass the call and fail the next turn, in `terminal`, instead.
Switching provider drops reasoning data only the previous provider can read; see
[switching providers](../providers.md#switching-providers).

## Identity

Supply a `session_id` or let one be generated as a lowercase UUIDv4.

A supplied id must match `[a-z0-9][a-z0-9-]{7,63}`, checked at creation. A bad one fails
`session_id_invalid`.

After creation the id is opaque. Do not parse it or read structure into it.

## Persistence

```
durable     the default. Resumes after close, and in another process.
ephemeral   not resumable once closed.
```

## Create, resume, delete

Creating and resuming are separate operations. There is no resume-or-create.

```
creating an id that already exists   already_exists
resuming an unknown id               not_found
a durable id that already has a live handle   session_in_use
```

`delete_session` removes a durable session and its transcript. An unknown id fails
`not_found`, and one with a live handle fails `session_in_use`. A later resume does not
bring it back.

`list_sessions` returns the durable sessions in the agent's
[sessions directory](../configuration.md#sessions-directory), including sessions with
live handles. Ownership spans agents and processes. Closing the handle or exiting its
process releases that ownership.

Call `session.close()` before another handle resumes it. Closing an agent also closes
its sessions. Read and save `session.info` or `session.history` before closing; calls
on closed handles fail `closed`.

Resume uses the new agent's instructions, tools, credentials, and approval authority.
It keeps the session's saved provider, model, and reasoning effort, not the resuming
agent's. Resume fails `engine_unavailable` when that provider's package extra is
missing, and `provider_failed` when the provider cannot load, such as for missing
credentials.

## Turns within a session

Sessions are ordered and multi-turn. A new turn sees every earlier turn that reached a
terminal.

An ephemeral session can begin with [supplied history](turns.md#supplying-a-conversation).
Later turns retain that seed without duplicating it. It cannot be replaced or supplied
again after a turn has been accepted.

`session.history` records turns actually taken. The first turn's `TurnRecord.input`
retains its supplied history; imported messages create no extra turn records, ids,
events, results or historical usage. Work that processes those messages counts toward
the new turn's usage.

One turn runs at a time. Starting a second fails `busy` rather than queueing behind the
first. Separate sessions run concurrently without interfering.

## Forking

```
child = session.fork()
```

The child sees the parent's history as of the fork, and nothing the child does appears in
the parent. It gets its own generated id and inherits the parent's persistence and
current selection. Forking a session with a turn in flight fails `busy`.

Any supplied conversation seed is inherited exactly once, including when it is retained
in a turn's input. A child with inherited conversation cannot accept another seed.
A fork of an empty ephemeral session can accept its first seed.

## The conversation stays on your side

A session's history lives in a local transcript, written where the agent runs. That
transcript is the only authoritative record of the conversation.

Providers are asked to keep nothing by default. Every request carries the full input,
request-level storage is disabled where supported, and no provider-side conversation
handle is ever load-bearing. Retention is an explicit opt-in through
[`extra_request_params`](../configuration.md). Hosted account retention and deletion
policies still need to meet your requirements; request flags alone do not establish
zero data retention. See [providers](../providers.md#statelessness).

Durable sessions preserve settled turns across process restarts:

```
kill every process between turns and settled conversation remains available
a durable session resumes later, in a different process, from the transcript alone
```

Keep the same [sessions directory](../configuration.md#sessions-directory) when
resuming. When it is computed, that means the same working directory. A resumed session
works in the resuming agent's working directory. Abrupt termination during a turn
restores the last committed transcript; inspect external effects before retrying work
that may have run before the process stopped.

Durable sessions are stored in the Amplifier session layout:

```
<sessions_directory>/sessions/<session_id>/
    transcript.jsonl            the conversation, authoritative
    metadata.json
    context-intelligence/       observation capture
```

You may read it; only the engine writes it. `list_sessions` and `resume_session`
remain the way back to a conversation, and `session.history` remains the typed record
of turns. Anything else in the sessions directory is internal.

## Observation capture

Beside the transcript, every session (durable or ephemeral) keeps an observation
capture: the ordered record of runtime events behind each turn, in the Amplifier
Context Intelligence form, with secrets redacted before anything is written. It is
observation, never authority. Resume reads the transcript alone, and deleting
`context-intelligence/` changes no history and no result.

Amplifier Context Intelligence tooling reads agent sessions when pointed at
`~/.amplifier-agent/projects`, where each folder takes the place of the CLI's project
slug.
A host can also forward the capture to a Context Intelligence server through the
[`context_intelligence`](../configuration.md#context_intelligence) setting. Forwarding
never fails a turn.
