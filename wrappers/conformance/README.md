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
cd wrappers/conformance && npx tsx runner_ts.ts <fixture_path>
```

## Running Tests

```bash
# Python tests
uv run pytest wrappers/conformance/tests/ -v

# TypeScript tests
cd wrappers/conformance && pnpm test
```

## Fixture Location

Fixtures live at `src/amplifier_agent_lib/protocol/conformance/fixtures/`.
The Python runner imports from `amplifier_agent_lib.protocol.conformance.loader`.
The TypeScript runner ports the same shape contract using the `yaml` npm package.
