# Envelope and Errors

## Scope

The stdout contract of `amplifier-agent run`: the success envelope, the error envelope, the error
code taxonomy, the classification-to-exit-code mapping, and the per-turn audit trail. It does not
cover the flag surface (see `cli.md`), the ndjson stderr event stream (see `wire-protocol.md`), or
what the wrapper SDKs synthesize when no envelope arrives (see `wrapper-contract.md`).

Machine-readable JSON Schemas for every wire type ship with the distribution and are authoritative
over the examples here.

## Stdout discipline

Under `--output json`, the envelope is the only thing that reaches stdout. Everything the turn
itself writes goes to stderr: output from bundle modules, provider diagnostics, dependency
warnings, anything else. This is structural, not a convention a module can violate.

Under `--output text` stdout is left intact so a human sees the reply as it is produced.

## Success envelope

```json
{
  "protocolVersion": "0.3.0",
  "sessionId": "sess-abc-001",
  "turnId": "turn-1",
  "reply": "It is 2:15pm Pacific time.",
  "error": null,
  "metadata": {
    "tokensIn": 1247,
    "tokensOut": 89,
    "durationMs": 1832,
    "bundleDigest": "",
    "engineVersion": "0.12.0",
    "protocolVersion": "0.3.0",
    "correlationId": "3f2a1b9c-4d5e-4f60-9a7b-1c2d3e4f5061",
    "activeMode": null
  }
}
```

- `sessionId` echoes `--session-id` when supplied, else the session id the engine assigned.
- `turnId` is the engine's turn id, defaulting to `"turn-1"`. One turn is submitted per process,
  so in practice it is always `turn-1`.
- `durationMs` is wall-clock time measured around the turn by the CLI, not by the engine.
- `activeMode` echoes `--mode` verbatim, or `null`. The mode is non-sticky: omitting `--mode` on a
  resume returns the field to `null`.
- `correlationId` is one UUID v4 minted per invocation and appears in every envelope that
  invocation emits.
- `bundleDigest` is always the empty string. The engine does not compute a bundle digest on any
  path. The field is present for schema stability; consumers must not treat it as a digest and
  must not require it to be populated.

## Error envelope

Emitted for failures raised once the turn is running.

```json
{
  "protocolVersion": "0.3.0",
  "sessionId": "sess-abc-001",
  "turnId": "turn-1",
  "reply": "",
  "error": {
    "code": "approval_translation_failed",
    "classification": "approval",
    "severity": "error",
    "correlationId": "3f2a1b9c-4d5e-4f60-9a7b-1c2d3e4f5061",
    "message": "unknown approval action 'review'"
  },
  "metadata": {
    "tokensIn": 0,
    "tokensOut": 0,
    "durationMs": 247,
    "bundleDigest": "",
    "engineVersion": "0.12.0",
    "protocolVersion": "0.3.0",
    "correlationId": "3f2a1b9c-4d5e-4f60-9a7b-1c2d3e4f5061",
    "activeMode": null
  }
}
```

Error-path invariants:

- `reply` is always `""`.
- `tokensIn` / `tokensOut` are always `0`.
- `severity` is always `"error"`. The value `"warning"` exists in the wrapper types; the engine
  never emits it.
- `activeMode` is always `null`.
- `correlationId` is duplicated at `error.correlationId` and `metadata.correlationId`.
- `stderrTail` is never emitted by the engine, on any path. When a wrapper reports a populated
  `stderrTail`, the wrapper synthesized it.
- `remediation` and `details` are absent on this path. `remediation` appears only on the pre-boot
  envelope below; `details` is never populated by the engine at all.

### Pre-boot (argv-validation) envelope

Failures detected before the turn starts emit the same envelope shape with three differences:

```
sessionId, turnId    always ""
classification       "protocol" for every argv and config failure; "engine" for
                     modes_unavailable, because when mode discovery itself failed, telling
                     the caller their mode name is wrong would be a lie
error.remediation    included when the failure has one
metadata             omits activeMode entirely, rather than setting it to null
```

This is the path for config errors, workspace-slug rejection, protocol skew, headless approval,
malformed argv JSON, unknown `--mode`, and unavailable mode discovery.

## Output modes

```
--output json   success -> envelope on stdout, exit 0
                failure -> error envelope on stdout, exit per classification
--output text   success -> bare `reply` plus a trailing newline on stdout, exit 0
                failure -> error envelope JSON on stdout, exit per classification
```

There is no text failure format. `--output text` governs the success path only; on failure both
modes emit the same JSON envelope on stdout. A text-mode consumer that reads only stdout will
receive JSON it did not ask for, and must be prepared to parse it or to detect it by the non-zero
exit code.

## Error codes

### Wire error codes

These are the wire-level codes carried in the protocol's `error.data.code` field. A subset also
reaches the envelope's `error.code`.

```
agent_not_ready              engine is not booted
invalid_session              session id is not usable in this context
stale_session                session state is older than the engine can use
session_not_found            no transcript for the requested session id
config_validation            configuration failed validation
provider_not_configured      no provider credentials resolvable
provider_init_failed         the provider module raised during construction
prompt_required              no prompt supplied and stdin is not interactive
bundle_load_failed           bundle.md is missing or structurally invalid
spawn_failed                 subagent spawn failed
approval_denied              an approval request was declined
approval_timeout             an approval request was not answered in time
approval_translation_failed  an approval request could not map to the bundle hook shape
approval_protocol_violation  the approval channel violated its contract
env_injection_rejected       wrapper-side: refused to inject the supplied environment
tool_execution_failed        a tool raised during execution
runtime                      generic runtime failure
wire_protocol_violation      a message did not match the protocol schema
protocol_version_mismatch    wrapper and engine protocol versions differ
internal                     catch-all; any uncaught exception during `run`
```

### CLI-only codes

These are not wire codes. They are emitted by the CLI and the config loader, always through the
pre-boot envelope path.

```
approval_unconfigured           headless with no approval policy at any tier
argv_json_malformed             a flag's JSON value is unparseable or is not an object
argv_path_unreadable            a flag's `@path` value could not be read
argv_workspace_invalid          --workspace slug fails the grammar
argv_mode_unknown               --mode names a mode that discovery did not find
modes_unavailable               mode discovery itself could not run
config_unreadable               the config file could not be opened
config_malformed_json           the config file is not valid JSON, or is not an object
config_unknown_key              an unrecognized TOP-LEVEL key
config_invalid_type             a recognized key has the wrong type, or an unrecognized sub-key
                                in a closed inner shape
config_invalid_provider_module  provider.module is not a known provider
```

### Classification

Codes raised during the turn are classified by lookup on the code. Anything unmapped is `engine`.

| code | classification | exit |
|---|---|---|
| `approval_translation_failed` | approval | 3 |
| `approval_timeout` | approval | 3 |
| `approval_protocol_violation` | approval | 3 |
| `approval_unconfigured` | protocol | 2 |
| `protocol_version_mismatch` | protocol | 2 |
| `argv_json_malformed` | protocol | 2 |
| `argv_path_unreadable` | protocol | 2 |
| everything else | engine | 1 |

The pre-boot path sets its classification directly rather than by lookup, as described above.

### Classification to exit code

```
classification   exit   meaning
--------------   ----   -------
(error is null)  0      reply is the authoritative result
engine           1      the engine failed. reply is ""
transport        1      transport failure. reply is ""
unknown          1      unclassified failure. reply is ""
protocol         2      skew, schema violation, malformed argv, bad config.
                        Separable so CI can gate on it.
approval         3      approval-runtime failure. Separable so hosts can build
                        deferral flows without parsing the envelope.
n/a              130    SIGINT. `[info] Interrupted` on stderr, exit 130.
```

This table is the authoritative statement of the mapping. `cli.md`, `wrapper-contract.md`, and
`architecture/data-flows.md` point here rather than restating it.

Exit codes are informational. When a parseable envelope is present, the envelope is authoritative
and its `error` field drives the wrapper's error synthesis.

## Headless approval fail-fast

Approval mode precedence is `-y`/`-n`, then `approval.mode` in the host config, then `"prompt"`
when stdin is a TTY. A non-interactive context with no policy at any tier is a hard error, not a
silent deny:

```
code:           approval_unconfigured
classification: protocol
exit:           2
remediation:    Pass `-y` to auto-approve, `-n` to auto-deny, or set
                `{"approval": {"mode": "yes"|"no"|"prompt"}}` in your --config /
                $AMPLIFIER_AGENT_CONFIG file.
```

The behavior this replaces was silently falling through to `"no"`, which produced success-shaped
no-op runs: monitoring saw green, no work happened, and there was no programmatic signal to catch
it. A host that genuinely wants deny-all headless must say so.

## Per-turn audit trail

One file per turn:

```
<workspaces_root>/<workspace>/sessions/<sessionId>/audits/turn-<turnId>.json
```

```json
{
  "argvDigest": "sha256:<hex of the joined argv>",
  "envDigest": "sha256:<hex of a constant placeholder>",
  "protocolVersion": "0.3.0",
  "exitCode": 0,
  "correlationId": "3f2a1b9c-4d5e-4f60-9a7b-1c2d3e4f5061",
  "startedAt": "2026-07-31T15:00:00.000000+00:00",
  "endedAt": "2026-07-31T15:00:01.832000+00:00"
}
```

Invariant: secrets are digested, never persisted as literals. The purpose is to let an operator
answer "what did session X run with at turn N" without capturing argv at the wrapper layer, and to
make caller-config drift detectable by diffing successive audit files in one session.

Written on the success path and on both failure paths once the turn has started. Not written when
`--session-id` is absent (anonymous CLI use), and not written on any pre-boot failure, because
that path exits before the turn begins.

`envDigest` is present for schema stability only. It hashes a constant placeholder and carries no
information. There is no `mcpConfigPathDigest` field.

## Non-goals

- Mid-stream error events. The three approval error codes are turn-end errors surfaced through
  `error.classification = "approval"`, not protocol errors on a live channel. There is no live
  channel to carry them.
- `metadata.hostCapabilities`. Not part of the envelope, and there is no `--host-capabilities`
  flag. Do not introduce either.
- `error.details`. Present in the wrapper's error type, never populated by the engine.
- `metadata.bundleDigest` as a real digest. The field exists and is always empty.
- Engine-side `stderrTail`. The engine never emits one. A wrapper that synthesizes one is
  responsible for redacting it against the MCP env keys it declared.
- A text-formatted failure output for `--output text`.
