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

## Turns within a session

Sessions are ordered and multi-turn. A new turn sees every earlier turn that reached a
terminal.

One turn runs at a time. Starting a second fails `busy` rather than queueing behind the
first. Separate sessions run concurrently without interfering.

## Forking

```
child = session.fork()
```

The child sees the parent's history as of the fork, and nothing the child does appears in
the parent. It gets its own generated id and inherits the parent's persistence. Forking
a session with a turn in flight fails `busy`.

## The conversation stays on your side

A session's history lives in a local transcript, written where the agent runs. That
transcript is the only authoritative record of the conversation.

Providers are asked to keep nothing. Every request carries the full input, server-side
retention is off, and no provider-side conversation handle is ever load-bearing. This is
ZDR-compatible without configuring anything; retention is an explicit opt-in through
[`extra_request_params`](../configuration.md).

Three things follow, and you can build on all three:

```
kill every process between turns and nothing is lost
a durable session resumes later, in a different process, from the transcript alone
no provider ends up holding a copy of your conversation
```

The transcript lives under the `storage` root. Its layout is not something to read or
depend on; `list_sessions` and `resume_session` are how you get back to a conversation.
