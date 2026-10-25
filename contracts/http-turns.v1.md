# HTTP Turns Contract v1 (FROZEN 2026-10-21)

**Who builds against this:** callers that cannot embed a binding and need what
[`http-face.v1`](http-face.v1.md) cannot carry: the whole event stream and a say in each
effect. A host written in a language without a binding, or a plugin running inside
another program's runtime, is the typical caller.

**What it freezes:** a projection of [`agent-interface.v1`](agent-interface.v1.md) turns
onto HTTP, carrying every [`turn-events.v1`](turn-events.v1.md) event and answering
approvals over a second request. It is served by the same server as `http-face.v1`,
under the same bearer token and the same server-start configuration.

This is a face, not a binding. It carries the turn and its approvals and nothing else:
no caller tools, no session management beyond what one turn needs.

## 1. Endpoints

```text
POST /v1/amplifier/turns                      one turn, streamed as turn events
POST /v1/amplifier/approvals/{request_id}     the answer to an approval request
```

Both require the bearer token of `http-face.v1` section 8. Failures before a stream
begins use the error shape and status codes of `http-face.v1`.

## 2. One request is one turn

A turn request carries the conversation exactly as a chat-completions request to
`http-face.v1` does: `model`, `messages`, `reasoning_effort`, and the
`Amplifier-Session-Id` header, with the same meaning, the same session rules
(`http-face.v1` section 12), and the same refusals. `stream` and `stream_options` are
not accepted; the response always streams.

It may also carry `approvals: "ask"`. Without it, the server's static approval policy
decides every effect, as in `http-face.v1`.

## 3. The response is the event stream

The response is `text/event-stream`. Each `data:` line holds one `turn-events.v1`
envelope as JSON, in order and unchanged: every type, every field, every owned
extension. Times are ISO 8601 strings and decimal amounts are decimal strings, as the
JSON forms of the binding records. The stream ends after `terminal`, and nothing follows
it.

Once the stream has begun, every failure arrives as the `terminal` event the turn would
produce through a binding. The connection never closes before `terminal` while the
server is running.

Closing the connection cancels the turn, as `Turn.cancel` does. The session, when the
request named one, and its settled turns remain.

## 4. Approvals by request

With `approvals: "ask"`, each effect the server's static policy allows is put to the
caller. Each effect the policy denies is denied without asking. A caller can therefore
narrow what the server allows and never widen it.

The `approval_request` event carries the request. The caller answers it with:

```text
POST /v1/amplifier/approvals/{request_id}
{"decision": "allow" | "deny" | "cancel", "reason"?: string}
```

The answer settles the request as an `ApprovalResponse` would, and the stream then
carries its `approval_decision`. An unknown, settled, or expired `request_id` is refused
with `not_found`. An unanswered request expires at the binding's deadline as `timeout`,
and a request still open when the connection closes settles as `cancel`.

Any request with this server's token can answer. Callers who must not answer each
other's requests use separate servers.

## 5. File changes on tool results

A `tool_result` for a built-in tool that changed files carries the owned extension
field `com.microsoft.amplifier.file_changes`: one entry per changed file, in the order
the tool changed them.

```text
{path, change: "added" | "modified" | "deleted", patch, additions, deletions}
```

`path` is relative to the working directory when the file is inside it, and absolute
otherwise. `patch` is a unified diff of the file's text before the call and after it.
`additions` and `deletions` count its changed lines. The field is absent when the tool
changed no file, and an entry is absent for a file whose content is not text.

## 6. Declared event subset

Per `turn-events.v1` section 5, this face carries all eleven types and every owned
extension.

## Invariants

1. **This face invents no agent behavior.** Events are the binding's events. The only
   addition is the file-change field, an observation of an effect that already ran.
2. **The caller never gains authority the server did not grant.** `ask` narrows the
   static policy; it never widens it.
3. **Every stream that begins ends with `terminal`.**

## What this face cannot carry

- **Host-executed tools.** A caller-supplied tool is a function in the caller's process,
  and this face has no process to reach into.
- **Per-request configuration beyond the model and approvals.** Instructions, tools, and
  the sessions directory are server-start settings.
- **Session management.** Listing, forking, and deleting sessions live behind a
  binding.

## Versioning

`http-turns/1`, independent of the other contracts and of releases. Every change is a
dated, owner-ratified amendment in the changelog below.

## Changelog

Dated, owner-ratified amendments only.

- 2026-10-21: v1 FROZEN by owner ratification, promoted from the `http-face.v1` backlog.
  The evidence: opencode runs plugins inside its own JavaScript runtime, where no
  binding runs on every platform it supports, and it needs every tool call, every
  approval, and the changed files to show Amplifier's work in its own interface.
