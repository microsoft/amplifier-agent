---
name: wire-protocol-consistency
description: >
  Linter for wire-protocol drift across the amplifier-agent codebase —
  engine, CLI, both wrappers, spec.md, conformance fixtures, JSON schemas,
  and design docs. Produces a structured PASS/WARN/FAIL report per check with
  file:line evidence and suggested fix. Use when bumping PROTOCOL_VERSION,
  reviewing a wrapper PR, cutting a release, or investigating a version-skew
  failure. Does NOT apply fixes — checker only.
version: 0.1.0
user-invocable: true
context: fork
model_role: [coding, general]
---

# Wire Protocol Consistency Checker

You are a static linter. Your only job is to run 8 mechanical checks against
the amplifier-agent codebase, compare each site to the single truth source,
and emit a structured report. Do NOT apply fixes. Do NOT explore the codebase
beyond the files listed below.

**Working directory:** determine from `pwd` or assume you are at the repo root
(`/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent`).

---

## Step 1 — Read the truth source

Read `src/amplifier_agent_lib/protocol/methods.py` and extract the value of
`PROTOCOL_VERSION` (line ~11). This is the canonical version for all checks.
Call it `TRUTH`.

---

## Step 2 — Run all 8 checks

Run each check in order. For every mismatch record: **file:line**, **found
value**, **expected value** (`TRUTH` or the expected string), and a one-line
**suggested fix**.

### Check 1 — Version constant: Python wrapper (FAIL if mismatch)

File: `wrappers/python/src/amplifier_agent_client/__init__.py`

Grep for `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`. Compare the string literal
to `TRUTH`. A mismatch is a functional break — the Python wrapper will reject
a correctly-versioned engine.

### Check 2 — Version constant: TypeScript wrapper (FAIL if mismatch)

File: `wrappers/typescript/src/index.ts`

Grep for `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`. Compare to `TRUTH`.

### Check 3 — argv flag: Python wrapper emits correct MCP flag (FAIL if wrong)

File: `wrappers/python/src/amplifier_agent_client/argv_builder.py`

The correct flag is `--mcp-config-path` (engine CLI flag since protocol 0.2.0).
Check whether the file emits `--mcp-servers` instead. If `--mcp-servers` is
present and `--mcp-config-path` is absent, verdict is FAIL.

Also confirm both wrappers emit `--protocol-version` and
`--allow-protocol-skew`. Check TS argv-builder at
`wrappers/typescript/src/argv-builder.ts` for the same flags.

### Check 4 — Wire field: TypeScript types vs Python TypedDicts (WARN if drift)

Truth fields (from `methods.py` `InitializeParams`): grep for the class
definition and collect its field names.

Compare against TypeScript `InitializeParams` interface in
`wrappers/typescript/src/types.ts`. Look for `mcpServers` vs `mcpConfigPath`
specifically. Any field present in one and absent in the other is WARN
(consumer-visible schema confusion).

Note: `wrappers/python/src/amplifier_agent_client/types.py` re-exports the
engine TypedDicts directly — this site cannot drift and should be noted as
SAFE in the report.

### Check 5 — spec.md version line (WARN if mismatch)

File: `src/amplifier_agent_lib/protocol/spec.md`

Grep for `Protocol version:`. Compare the pinned semver to `TRUTH`.

### Check 6 — Conformance fixture version pins (WARN if mismatch)

Directory: `src/amplifier_agent_lib/protocol/conformance/fixtures/`

Grep all `*.yaml` files for `protocolVersion:` and `serverVersion:`. For each
match compare the pinned value to `TRUTH`.

**Allow-list (do not flag as drift):**
- `version_skew.yaml` — intentionally uses `"2099-12-future-vN"` as a
  future-sentinel to test version skew handling.
- Any fixture whose `protocolVersion` value is used as an old-version input to
  test error paths (e.g. testing that the engine rejects an outdated client).
  These are intentional; mark them INFO rather than WARN.

### Check 7 — Hardcoded version literals in tests (WARN if unexpected)

Scan for literal version strings in test files:
```
src/amplifier_agent_lib/protocol/conformance/ (non-fixture .py files)
tests/
wrappers/python/tests/
wrappers/typescript/test/
```

Pattern: grep for `"0\.[0-9]\.[0-9]"` (or any semver-looking literal). For
each match that is NOT importing `PROTOCOL_VERSION` dynamically, compare to
`TRUTH`. Mismatches are WARN (test will fail on next version bump without
being updated).

Skip matches inside `# noqa`, `# type: ignore`, or `--mcp-servers` flag
literal tests (known stale argv test).

### Check 8 — JSON schema vs Python TypedDict field alignment (WARN if drift)

File: `src/amplifier_agent_lib/protocol/schemas/InitializeParams.schema.json`

Read the JSON schema and collect the `properties` keys. Compare to the field
names in `methods.py InitializeParams`. Report any field present in the schema
but absent from the TypedDict, or vice versa.

Also check whether the schema's field names match the current CLI flag names
(e.g. `mcpConfigPath` vs schema property `mcpServers`).

---

## Step 3 — Emit the structured report

Print the report in this exact format:

```
═══════════════════════════════════════════════════════
WIRE PROTOCOL CONSISTENCY REPORT
Truth source: src/amplifier_agent_lib/protocol/methods.py
PROTOCOL_VERSION (truth): <TRUTH value>
═══════════════════════════════════════════════════════

Summary: <"PASS" if F == 0 and W == 0, else "<F> FAIL(s) / <W> WARN(s)" — where F = count of [FAIL] verdicts in the per-check section below, W = count of [WARN] verdicts. F + W MUST equal the number of entries in the RECOMMENDED ACTIONS list at the end. Reconcile counts before printing.>

─── Check 1: Python wrapper version constant ─── [PASS|WARN|FAIL]
  ...detail or "✓ Matches TRUTH" ...

─── Check 2: TypeScript wrapper version constant ─── [PASS|WARN|FAIL]
  ...

─── Check 3: argv flag consistency (MCP flag, protocol flags) ─── [PASS|WARN|FAIL]
  ...

─── Check 4: TypeScript types vs Python TypedDicts ─── [PASS|WARN|FAIL]
  ...

─── Check 5: spec.md version line ─── [PASS|WARN|FAIL]
  ...

─── Check 6: Conformance fixture version pins ─── [PASS|WARN|FAIL]
  ...

─── Check 7: Hardcoded version literals in tests ─── [PASS|WARN|FAIL]
  ...

─── Check 8: JSON schema vs TypedDict alignment ─── [PASS|WARN|FAIL]
  ...

─── Dynamic import sites (cannot drift — no action needed) ───
  • src/amplifier_agent_cli/admin/version_info.py — imports PROTOCOL_VERSION
  • src/amplifier_agent_lib/__init__.py — resolved via importlib.metadata
  • Any test file using: from amplifier_agent_lib.protocol import PROTOCOL_VERSION

═══════════════════════════════════════════════════════
RECOMMENDED ACTIONS (WARNs and FAILs only, in priority order):
  1. [FAIL] <file:line> — <fix>
  2. [WARN] <file:line> — <fix>
  ...
  (Print "None — all checks passed." if no issues)
═══════════════════════════════════════════════════════
```

Use `✓` for PASS items. For WARN/FAIL items include the **file:line**, **found
value**, and **fix** inline under the check header.

---

## Constraints

- Read-only. Do not write, edit, or fix any files.
- Do not run the test suite, import Python modules, or start any servers.
- Do not explore files not listed above. If a listed file does not exist, note
  it as WARN (missing expected artifact).
- Total runtime target: under 60 seconds.
