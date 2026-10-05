# Versioning

## Contract versions

Each moves independently.

```
agent-interface/1   the callable surface
turn-events/1       the event vocabulary and its ordering laws
host-config/1       what a host may set outside code
language-binding/1  how a language presents the interface
http-face/1         the chat-completions projection
```

Each is readable without invoking anything. `contract_version` (`contractVersion` in
TypeScript) reads `agent-interface/1`. `contract_versions` (`contractVersions`) lists
the library's applicable contract tokens; every event envelope carries `turn-events/1`.

None of these is a package version. The library you installed has its own number, moving
on its own schedule, and a change to it says nothing about the surface.

## How the surface changes

Mostly by addition:

```
new optional fields on existing records
new optional configuration keys
new registered event types
new registered error codes
new owned extension types and fields
```

A removal, rename, re-type, new default, or newly mandatory field is a breaking change.
Each one is dated in the contract's changelog and listed under **Breaking** in
[CHANGELOG.md](../CHANGELOG.md) with what to do. Read every entry between your installed
version and the one you move to.

## Read defensively

Preserve what you do not recognize. New optional fields and new owned extensions will
arrive within this major version, and code that drops them is code that will lose data it
was handed.

Match on registered names rather than on absence. An event type you have not seen is not a
reason to stop reading the stream, and an unrecognized field is not a reason to reject the
record carrying it.

A new registered event type always arrives as a contract change. It never appears on its
own.

## What is not versioned, because it is not yours

Everything beneath the surface: how the agent is assembled, how prompts are built, how
work is routed below your ceiling, what lies in the sessions directory beyond the
documented session layout, and how the library reaches the engine at all.

That includes replacing the engine outright. When it happens, the surface is unchanged
and your code does not move. Session ids created by an older engine may not resolve
against a new one, and `resume_session` says so with `not_found` rather than inventing a
conversation.

## Mismatches

```
contract_version_mismatch   the library and the engine do not agree on a contract
engine_unavailable          the engine could not be reached at all
```

Both are refusals by name, raised before any work starts. Neither is something to retry
into a different answer.
