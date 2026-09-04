# Conformance

The [contracts](../contracts/README.md) govern the public surface. Compatibility
requires public API scenarios, exported-surface lint, and the same scenarios after
replacing the engine and rewiring the owned bindings.

Use `uv` for Python commands and `pnpm` for TypeScript development commands.

## Clause inventory

`clauses/` maps obligations to checks and discriminating violations. Source references
point to the governing contract sections. Runtime assertions observe Python and
TypeScript bindings or the HTTP projection; engine-seam obligations use those same
checks. Behavioral exclusions and contract amendments have review obligations.

```bash
uv run conformance/check_inventory.py
```

This validates the inventory's structure and references. A catalogued check is a
required proof, not evidence that an implementation passes it.

## HTTP fields and fixtures

`http/fields.json` pins request, completion, chunk, model-list, and error shapes.
`http/cases.json` supplies accepted and refused inputs, expected history projections,
responses, and successful and failed streams.

```bash
uv run --script conformance/http/check.py
uv run --script conformance/http/check.py --client
```

The first command checks fixture shapes, reconstruction, and rejection of deliberately
broken responses. The second also drives an unmodified OpenAI client against a
loopback fixture server, including a failure after partial streamed content. These
checks establish fixture validity and client compatibility. Runtime conformance uses
the real face and installed bindings against the scripted provider.

Successful streams end with a finish chunk and `[DONE]`. Failed streams end with the
standard error object and close. The error's `message` includes its remedy; no custom
response field is needed.

## Evidence

Keep uncovered obligations distinct from executable assertions that fail. A full
compatibility result requires all obligations, complete error records, lossless
values, live ordered events, and the replacement exercise. A fixture validator or a
milestone selection cannot establish that result alone.
