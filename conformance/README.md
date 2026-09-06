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
uv run --all-packages python -m pytest tests/e2e/python
uv run --all-packages python -m pytest tests/e2e/python --engine replacement
uv run --all-packages python -m pytest tests/e2e/http
uv run --all-packages python -m pytest tests/e2e/http --engine replacement
uv run --all-packages conformance/run.py --output /tmp/amplifier-conformance.json
uv run --all-packages conformance/run.py --typescript --build-runtime --output build/conformance.json
uv run --all-packages conformance/run.py --full --build-runtime --output build/conformance.json
```

`scenarios/turns.json` supplies the shared binding inputs, provider scripts, and
expected observations. Fixture provisioning replaces the private provider factory;
scenario actions call only public binding operations. Provider requests and active
work are observed independently of emitted events. Deliberately broken observations
must fail the same assertions as runtime observations.

The reporter separates failed assertions, setup failures, and uncovered obligations.
Python, HTTP, TypeScript, engine component, and repository checks supply individual
outcomes under separate suite identities. Provider integrations exercise the Python
API against native provider protocols. `--typescript` adds compilation, binding
surface tests, public Node scenarios, and engine component tests. Engine component
outcomes cannot substitute for public binding evidence. The fixture runtime must
match its file manifest and the current engine sources; `--build-runtime` builds it
before the TypeScript checks.

Replacement acceptance reuses public actions and assertions with fresh sessions and
an independent engine, including its Node participant. Python and HTTP select it
with `--engine replacement`; production-specific cases are deselected explicitly.
TypeScript runs the same public test files in a disposable consumer. Replacement
observations retain separate suite identities. Installed production artifacts
receive the separate acceptance checks below.

`evidence/groups.json` names each suite and case selector once. The other
`evidence/*.json` files register each check and surface, referencing those group names
in `cases` and describing the violation the assertions reject. All referenced cases
must pass. Wildcard selectors
include an exact expected count; missing variants, skipped cases, duplicates, and
unexpected cases leave the check incomplete. Every declared surface must pass before
a check covers an obligation. Every required check must pass before the obligation
is satisfied.

`reviews.json` records source reviews with a reviewer, rationale, conclusion, and
SHA-256 hashes of reviewed repository files. Missing or changed reviews remain
uncovered; a current negative review is a reported failure. Tests cannot establish
independent authorship or historical owner approval;
those requirements need their own reviewed evidence.

`--installed` runs installed-package acceptance using the artifact inputs in
[development checks](../docs/development/checks.md#build-installable-artifacts).
`--full` includes TypeScript and installed-package checks and fails for any uncovered
obligation, including missing reviews and replacement acceptance. Failed assertions
and setup failures always produce a nonzero exit. Reports record source hashes and
reject source changes during verification. No successful command grants coverage
without its required case outcomes.

Installed acceptance checks the consumer package contents and native manifests
against the source being verified. Editable checkouts, stale distributions, missing
artifact inputs, and altered native files cannot substitute for matching installations.
`tests/e2e/installed/` separates Python and TypeScript native consumers, network HTTP
tests, and cross-binding restart tests. Only artifact, process, and controlled-service
helpers are shared between surfaces.

Keep uncovered obligations distinct from executable assertions that fail. A full
compatibility result requires all obligations, complete error records, lossless
values, live ordered events, and the replacement exercise. A fixture validator or a
partial scenario selection cannot establish that result alone.
