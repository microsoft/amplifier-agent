# Wrapper conformance tests

Tests that lock in symmetry between the Python wrapper and the engine's
documented wire emissions. These complement the protocol-level harness
under `wrappers/conformance/` (which drives JSON-RPC fixtures through a
scripted transport) by validating the layer where the new Mode A v2
wrappers actually operate: stdout `§4.1` envelope and stderr NDJSON.

| Test file | Validates | Counterpart in TS |
|---|---|---|
| `test_engine_envelope.py` | `parse_run_output` produces the right `DisplayEvent` for every canonical engine envelope shape — success, every error classification, synthesized fallbacks. | `wrappers/typescript/test/run-output-parser.test.ts` |
| `test_engine_ndjson_stream.py` | `parse_ndjson_stream` surfaces every documented wire notification (`progress`, `tool/started`, `result/final`, `usage`, `approval/request`, etc.) verbatim, and handles framing edge cases (blank lines, CRLF, non-JSON, bare scalars). | `wrappers/typescript/test/transport.test.ts` (parseNdjsonStream cases) |

The fixtures are scripted inline as Python literals rather than YAML files
because each test exercises a tightly scoped invariant — adding fixture
indirection would obscure the contract rather than clarify it. The
protocol-level YAML fixtures continue to live under
`src/amplifier_agent_lib/protocol/conformance/fixtures/` for the
JSON-RPC runner harness.

When the wire protocol changes, both this directory and the TS test files
must be updated together so the wrappers stay in lockstep.
