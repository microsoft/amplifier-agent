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

```bash
uv run --all-packages python -m pytest conformance/tests/test_scenarios.py
uv run --all-packages conformance/run.py --output /tmp/amplifier-conformance.json
uv run --all-packages conformance/run.py --full
```

`scenarios/turns.json` supplies the shared binding inputs, provider scripts, and
expected observations. Fixture provisioning replaces the private provider factory;
scenario actions call only public binding operations. Provider requests and active
work are observed independently of emitted events. Deliberately broken observations
must fail the same assertions as runtime observations.

The reporter separates failed assertions, setup failures, and uncovered obligations.
It exits nonzero for failed assertions or setup failures. `--full` also fails for
uncovered obligations. Passing one scenario does not cover an entire inventory check;
only explicitly registered complete checks reduce the uncovered list.

Keep uncovered obligations distinct from executable assertions that fail. A full
compatibility result requires all obligations, complete error records, lossless
values, live ordered events, and the replacement exercise. A fixture validator or a
partial scenario selection cannot establish that result alone.
