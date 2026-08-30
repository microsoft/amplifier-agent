# Proposal: a public seam for host-supplied tools at the Engine/prepare level

Status: **proposal**, not a spec. Filed by a downstream embedder against
`docs/INTEGRATION.md`'s "Embedding the library" path. Nothing here is a
request to change v0 behavior today -- it's a seam to consider, and a
heads-up that we plan to move off it the moment the v1 facade covers it.

## The gap

Our host is a `.dot`-pipeline runner. Some pipeline nodes declare their own
tools (a status-file writer, a delegation nudge) that the node's worker needs
in order to report back to the pipeline. When a node's worker is
`amplifier-agent`'s `Engine`, embedded per `docs/INTEGRATION.md`, there is no
public way to hand it one of those host-declared tools -- `load_and_prepare_cached`
-> `Engine(turn_handler)` -> `boot` -> `submit_turn` has no parameter for it, and
`PreparedBundle`/`mount_plan` are internal to `amplifier-foundation`, not this
library's contract.

We had an adapter that closed this gap by reaching into the hosted session's
own coordinator (`session.coordinator.mount("tools", tool, name=tool.name)`)
from inside our `turn_handler`. It worked -- it's the same call shape
`amplifier_agent_http/_session_runner.py` itself uses to mount
`HostToolProxy` instances onto a live per-turn coordinator
(`_session_runner.py:134`), so it clearly *can* be done safely. We deleted it
anyway: `coordinator` is your internals, reached from outside the one place
(`amplifier_agent_http`) that's actually part of this codebase. An embedder
depending on that shape has no compatibility guarantee and no changelog entry
protecting it. We'd rather disclose the capability gap to our own users than
keep leaning on a reach-in.

## What we're asking

Not a specific API -- just that host-supplied tools get a *public* attach
point at the `Engine`/prepare layer, so an embedder never needs
`session.coordinator` to do it. `amplifier_agent_http` already proves the
mechanism is safe internally; the ask is to expose the same capability
through the contract surface `docs/INTEGRATION.md` documents.

Roughly, something that fits alongside the existing recipe:

```python
prepared = await load_and_prepare_cached(aaa_version=__version__)
inject_provider(prepared, "anthropic")

# proposed: a public seam, shaped however fits amplifier-foundation's own
# mount-plan model -- this is illustrative, not a signature request.
inject_host_tools(prepared, [my_status_writer_tool])

handler = make_turn_handler(prepared, cwd=..., is_resumed=False, workspace=...)
engine = Engine(turn_handler=handler, protocol_points={...})
```

`inject_provider`/`inject_routing_matrix` are already exactly this shape --
a public function that mutates `prepared` before `boot`. A host-tools
counterpart would be consistent with a pattern the library has already
chosen, not a new one.

## We already see where this is headed, and we're glad

`docs_v1/05-interface/tools.md` documents `ToolsConfig(host_tools=[HostTool(...)])`
on `AgentConfig` for the `create_agent`/`Agent`/`Session` facade -- exactly
the caller-supplied-tools seam this proposal is asking for, designed better
than anything we'd have sketched ourselves (one flat tool set, no
`source`-mismatch surprises for the model, explicit `ToolResult`/`allow`/`deny`
semantics). The moment that facade ships as code, we intend to move our
embedding onto it and retire this ask entirely -- this proposal is scoped
only to the gap between now and then, for embedders on the current
`load_and_prepare_cached`/`Engine` contract in the meantime.

## Non-goals

- Not asking for tool-visibility policy, approval routing, or MCP -- those
  already have seams (`ApprovalSystem`, `docs_v1`'s `mcp_servers`).
- Not asking for a v0 commitment shaped like the v1 sketch above; whatever
  fits the current internals is fine.
- Not a reach-in request in the other direction either -- if the answer is
  "wait for v1," that's a fine answer, and this doc is as much a vote for
  prioritizing that facade's `host_tools` as it is a v0 ask.
