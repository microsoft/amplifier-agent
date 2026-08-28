# Surfaces

Three other ways to reach the agent. Each is built on the library and changes how
you talk to it, not what it can do.

- [CLI](cli.md) the `amplifier-agent` command, for shells and scripts.
- [HTTP](http.md) an OpenAI-compatible server, for anything over a network.
- [TypeScript](typescript.md) an SDK for Node, wrapping the CLI.

## They add nothing

A surface adds transport and presentation. It does not add capability, and it
does not take one away either.

That second half is the one worth stating. A surface that quietly resolves
approvals on your behalf, or drops an event type it does not know how to render,
is not a thinner path to the same product. It is a different product with weaker
guarantees, and that is a defect rather than a convenience.

So anything you can do here, you can do from the library, and the reverse holds
too. Every CLI command is a composition of library calls with a terminal-shaped
presentation on top, and each one is documented next to the calls it makes. A
command with no library equivalent would mean the CLI had grown a capability, and
that is the thing this rule exists to prevent.

## Choosing one

**The library**, when you are writing Python and want the agent inside your
application. Everything else here is built on it, so it is the only one with no
translation layer between you and the agent.

**The CLI**, for shell pipelines, CI steps, and anything where a process
boundary is what you want. It is also the fastest way to try something without
writing code.

**HTTP**, when the agent runs somewhere other than the caller, or when the caller
is not Python. It speaks the OpenAI chat completions API, so clients that already
target that shape work against it.

**TypeScript**, when the caller is Node. It spawns the CLI and parses its output,
so it needs both installed.

## Versions and compatibility

The CLI and a wrapper SDK agree on a surface version, and they compare it
exactly. Mismatched versions refuse each other rather than negotiating down,
because a partial agreement between two halves of one product is worse than a
clean failure.

A surface version covers one surface's own serialization: for the CLI, the argv
shape, the result envelope, and the event frames. It moves when that shape
moves, which is not when the library's `contract_version` moves and not when the
release version does.

For the same reason there is no capability handshake. A wrapper is a pipe, and a
pipe does not need to be told what will flow through it. What it does need is to
not choke on something it has not seen:

```
Wrappers ignore fields they do not recognize and forward event types they
do not recognize, unchanged.
```

That rule is what lets the agent gain an event without every wrapper needing a
release first. A wrapper that rejects an unknown event type breaks on the first
additive change, and additive changes are the common case.

## Installing them

The CLI ships in the same distribution as the library. The TypeScript SDK is a
separate package and does not bundle the agent, so it needs both. See
[Install](../01-install.md).
