# Specification

The contracts of `amplifier-agent`. Each file under `spec/` defines one contract surface:
what callers may depend on and what is deliberately absent.

These are behavior specs. They describe observable behavior and external contracts, and
they stay true regardless of how the engine is implemented internally.

## Index

### Surfaces callers drive

```
spec/engine-api.md               the library API for embedders, and the primary surface:
                                 turn assembly, Engine lifecycle, the two protocol points,
                                 the transport-free invariant, spawn policy
spec/cli.md                      the command surface: run and every admin subcommand,
                                 flags, mutual exclusions, and the flags that stay removed
spec/envelope-and-errors.md      the stdout envelope, the error envelope, the full error
                                 code enum, classification, and exit codes
spec/wrapper-contract.md         what a wrapper SDK must satisfy: binary discovery, the
                                 version probe, argv ordering, stream parsing, exit codes
spec/http-face.md                the OpenAI-compatible server: endpoints, SSE, auth,
                                 mode directives, host tools
```

### The protocol

```
spec/wire-protocol.md            JSON-RPC over NDJSON, PROTOCOL_VERSION and the strict
                                 equality rule, the display event taxonomy, conformance
                                 fixtures. References the generated schemas, does not
                                 restate them.
```

### Configuration and state

```
spec/host-config.md              the closed host-config schema, resolution order, merge
                                 semantics, and the config error codes
spec/storage-and-workspace.md    the on-disk layout, workspace identity and slug rules,
                                 session storage, and migration
spec/bundle-and-cache.md         the vendored bundle manifest, the cache key, and the
                                 self-invalidation rule
spec/providers-and-models.md     the provider catalog, credential resolution, model id
                                 namespacing, and models list
spec/skills-and-modes.md         the skill sigil, discovery and shadowing, mode resolution
```

### Distribution

```
spec/install-and-distribution.md install.sh, update, post-install, the version probe, the
                                 real tag namespaces, and the compatibility rules
```

## Reading order

New to the codebase, start with `ARCHITECTURE.md`, then `architecture/data-flows.md`, then
the spec you need.

Writing a wrapper SDK: `spec/wrapper-contract.md`, then `spec/envelope-and-errors.md`, then
`spec/wire-protocol.md`.

Embedding the engine as a library: `spec/engine-api.md`, then `spec/bundle-and-cache.md`.

Operating or deploying it: `spec/install-and-distribution.md`, then `spec/host-config.md`,
then `spec/storage-and-workspace.md`.

## Conventions

Every spec file carries the same sections:

```
Scope             what it covers and what it does not
<contract>        the contract itself
Non-goals         surfaces deliberately absent or unsupported
```

**Non-goals are contracts.** An absent flag stays absent because callers depend on it not
existing. Do not introduce a surface listed under Non-goals without treating it as a
breaking change.

**Specs are implementation-agnostic.** They describe what a caller can observe: commands,
flags, wire shapes, file layouts, error codes, ordering guarantees. They do not name
internal modules, cite source lines, or describe how the behavior is produced. A spec
should still be correct if the engine were rewritten in another language. Implementation
detail belongs in `ARCHITECTURE.md` and `architecture/data-flows.md`.

## Related documents

```
ARCHITECTURE.md                          what the system is and how the pieces connect
architecture/data-flows.md               step-by-step traces of the three primary flows
E2E_TESTING.md                           the DTU end-to-end framework and how to add a suite
LAYERS_AND_RELEASES.md                   which layer a change lands in and what to release
src/amplifier_agent_lib/protocol/spec.md generated wire reference, never hand-edited
```

## Generated artifacts

The wire spec and the JSON schemas are machine-generated from the protocol type
definitions, never hand-edited:

```bash
uv run python -m amplifier_agent_lib.protocol._gen \
  --output-dir src/amplifier_agent_lib/protocol
```

Specs reference those artifacts rather than restating them.
