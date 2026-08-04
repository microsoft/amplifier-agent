# Conformance Runners

Scripted-replay harnesses for the Python and TypeScript wrappers. Each runner:

1. Loads a YAML fixture (Plan 2 loader shape)
2. Drives the wrapper's JSON-RPC client through a `ScriptedTransport` that replays `server_to_client` frames at the correct sequence point
3. Captures all observable events (notifications + synthesized events)
4. Evaluates the fixture's `assertions:` list against the captured events
5. Emits a structured conformance report as JSON to stdout

## Report Shape

```json
{
  "fixture": "<fixture-name>",
  "language": "python" | "typescript",
  "passed": true | false,
  "assertions": [
    { "kind": "...", "passed": true, "detail": "..." }
  ]
}
```

Exit code `0` if all assertions pass, `1` otherwise.

## Supported Assertion Kinds

| Kind | Description |
|------|-------------|
| `notification_emitted` | A notification with `method` (and optional `payload_contains`) was captured |
| `no_notification` | No notification with `method` was captured; `source: engine` restricts to engine-emitted only |
| `error_returned` | An error was returned for the given `id` (optionally matching `code`) |
| `response_matches` | The response for `id` contains the expected `result` fields |
| `notification_order` | Skipped (ok=true) — not yet evaluated |
| `session_state` | Skipped (ok=true) — not yet evaluated |

## L14 Safety Net

After each `turn/submit` RPC call, both runners apply the L14 synthesis rule:
if the engine omitted a `result/final` notification but provided a non-null `reply`,
a synthetic `result/final` event with `synthesized: true` is added to captured events
(but NOT to the engine-notification list, so `no_notification: source: engine` assertions still pass).

## Usage

### Python

```bash
uv run python wrappers/conformance/runner_py.py <fixture_path>
```

### TypeScript

```bash
cd wrappers/conformance && pnpm exec tsx runner_ts.ts <fixture_path>
```

## Verifying Conformance

`verify-parity.py` is the single entry point. It is a plain script, not a test:
the conformance suite is a component (fixtures + two runners + driver), and the
driver lives with the things it drives.

```bash
# From the repo root
uv run python wrappers/conformance/verify-parity.py

# From this directory (equivalent)
cd wrappers/conformance && pnpm test
```

Add `-v` for per-assertion detail. Exit code `0` = verified, `1` = failure.

For every fixture in the canonical directory it checks:

1. **Freshness** — the fixture loads under `load_fixture` without
   `FixtureValidationError`, and `runner_py.py` exits 0/1 emitting a parseable
   JSON report. A crashed runner is a hard failure, never a silent pass.
2. **Conformance** — the fixture actually passes in *both* runners. A fixture
   reporting `passed: false` fails the run even if both runners agree on it.
3. **Parity** — both runners produce identical ordered `(kind, passed)`
   assertion tuples and an identical top-level `passed` flag.

Requires `pnpm install` in this directory for the TypeScript runner; the script
says so explicitly if `node_modules/` is missing.

## Fixture Location

Fixtures live at `src/amplifier_agent_lib/protocol/conformance/fixtures/`.
The Python runner imports from `amplifier_agent_lib.protocol.conformance.loader`.
The TypeScript runner ports the same shape contract using the `yaml` npm package.
