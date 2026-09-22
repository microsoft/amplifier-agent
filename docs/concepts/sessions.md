# Sessions

A session is one conversation. It holds the history that later turns can see, and it is
where the conversation is written down.

```
SessionRecord { session_id, persistence }
```

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

`list_sessions` returns the durable sessions under the agent's resolved storage root
and workspace, including sessions with live handles. Ownership spans agents and
processes. Closing the handle or exiting its process releases that ownership.

Call `session.close()` before another handle resumes it. Closing an agent also closes
its sessions. Read and save `session.info` or `session.history` before closing; calls
on closed handles fail `closed`.

Resume uses the new agent's instructions, tools, credentials, and approval authority.
The saved session model must remain within that agent's ceiling and provider. A
different provider or incompatible replay is refused with a named error.

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
the parent. It gets its own generated id and inherits the parent's persistence. Forking
a session with a turn in flight fails `busy`.

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

Keep the same storage root and workspace when resuming. Abrupt termination during a
turn restores the last committed transcript; inspect external effects before retrying
work that may have run before the process stopped.

A relative storage path resolves against the working directory at agent construction.
Changing directories afterward does not move that agent's stored sessions or locks.

Durable sessions are stored in the Amplifier session layout:

```
<storage>/workspaces/<workspace>/sessions/<session_id>/
    transcript.jsonl            the conversation, authoritative
    metadata.json
    context-intelligence/       observation capture
```

You may read it; only the engine writes it. `list_sessions` and `resume_session`
remain the way back to a conversation, and `session.history` remains the typed record
of turns. Anything else under the root is internal.

## Observation capture

Beside the transcript, every session (durable or ephemeral) keeps an observation
capture: the ordered record of runtime events behind each turn, in the Amplifier
Context Intelligence form, with secrets redacted before anything is written. It is
observation, never authority. Resume reads the transcript alone, and deleting
`context-intelligence/` changes no history and no result.

Amplifier Context Intelligence tooling reads agent sessions when pointed at
`<storage>/workspaces`, where `<workspace>` takes the place of the CLI's project slug.
A host can also forward the capture to a Context Intelligence server through the
[`context_intelligence`](../configuration.md#context_intelligence) setting. Forwarding
never fails a turn.
