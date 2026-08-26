# Wrapper Contract

## Scope

The contract between a wrapper SDK and the `amplifier-agent` binary: binary discovery, the
pre-spawn version probe, argv assembly, the two output channels, exit code handling, environment
composition, the MCP config spill file, and process group lifecycle. This is the conformance target
a third wrapper in any language must satisfy. Does not cover the envelope shape (see
`envelope-and-errors.md`), the JSON-RPC message set (see `wire-protocol.md`), or engine CLI flags
beyond the ones a wrapper emits (see `cli.md`).

Two reference wrappers ship, TypeScript and Python. They are normatively identical; where they
differ, one of them is wrong.

## Binary discovery

Resolution order:

```
1. $AMPLIFIER_AGENT_BIN   returned verbatim when set and non-empty
2. PATH lookup            for the name `amplifier-agent`
3. neither                fail with code binary_not_found
```

The environment variable wins over PATH. Its value is returned even when nothing exists at that
path: the failure is deferred to spawn time so the error names the real cause rather than a
discovery miss.

There is deliberately no binary-path constructor parameter. The environment variable covers
non-PATH installs, and the resolved path is readable back off the handle for debugging.

## The pre-spawn version probe

A wrapper MUST run `<binary> version --json` once during initialization, before constructing a
session handle and before any turn subprocess exists. One roundtrip is far cheaper than discovering
a mismatch after the engine has completed a full bundle load.

The engine's response is exactly two keys:

```json
{"version": "0.12.0", "protocolVersion": "0.3.0"}
```

Both reference wrappers additionally type an optional `bundleDigest` field on the engine info they
expose, defaulting it to `""`. The engine never populates it, in the probe payload or in any
envelope. A host reading it gets an empty string forever. Do not build on it.

The probe result is compared by strict string equality against the wrapper's own required protocol
version, and is cached for the handle's engine-info accessor. Probe timeout is 5 seconds; on
timeout the wrapper kills and reaps the probe process before failing.

Failure codes:

```
engine_probe_failed        binary unstartable (ENOENT / EACCES)  -> classification transport
                           timeout                               -> classification transport
                           non-zero exit                         -> classification transport
                           non-JSON or non-object stdout         -> classification protocol
                           missing version or protocolVersion    -> classification protocol

protocol_version_mismatch  probe succeeded, versions differ      -> classification protocol
                           the message carries both versions
```

`allowProtocolSkew: true` downgrades a probe failure to non-fatal: the wrapper falls back to empty
version metadata and skips the equality check entirely.

## argv assembly

The following ordering is normative. Emit exactly this sequence, in this order:

```
run
--session-id <sessionId>
--resume | --fresh                (always exactly one)
[--cwd <cwd>]                     (only when set)
[--config <configPath>]           (only when set)
--output json                     (always)
--protocol-version <version>      (always)
[--display text|ndjson]           (only when explicitly set)
[--workspace <slug>]              (only when set and non-empty)
-y | -n | (nothing)               (approval policy)
--prompt-file <path>              (when the prompt was spilled)
-- <prompt>                       (otherwise; the -- separator is ALWAYS emitted)
```

Exactly one of the last two lines is emitted, never both. The `--` separator is emitted
unconditionally, not only when the prompt begins with `-`.

argv assembly must be a pure transformation of already-resolved inputs. Spill file writing,
environment resolution, and capability composition happen before it, not inside it.

Approval flag emission:

```
approvalMode "yes"        -> -y
approvalMode undefined    -> -y        the default when the field is omitted
approvalMode "no"         -> -n
approvalMode "prompt"     -> emit NOTHING
                                       the only way to defer to the engine's
                                       host config approval mode or its TTY-based default
```

`--display` semantics. `ndjson` makes the engine emit one JSON-RPC notification per line on stderr.
A host that consumes typed notifications (cost, model, cache tokens, LLM duration) MUST set it.
`text` produces human-readable `[type] summary` lines that an NDJSON consumer cannot decode, so the
notification path silently stays empty. Omitting the flag makes the engine fall back to `text`.

`--workspace` must satisfy the engine's slug grammar `[a-z0-9][a-z0-9-]{0,63}`. The engine rejects
an invalid slug with `argv_workspace_invalid` and exit 2. Hosts running multiple agents in one
process should set it so each agent's transcripts land in a separate directory.

Removed flags. A wrapper MUST NOT emit any of these. The engine rejects unknown options with a
usage error on stderr, outside the envelope contract:

```
--mcp-config-path      MCP config flows via AMPLIFIER_MCP_CONFIG or host config mcp.configPath
--mcp-servers          same
--host-capabilities    surface deleted entirely
--env-allowlist        env composition is the host's responsibility
--env-extra            same
--allow-protocol-skew  moved to host config allowProtocolSkew: true
--provider             all provider knobs moved to host config provider.{module,config}
--model                same
--effort               same
--skills-dir           host config skills.skills, or $AMPLIFIER_SKILLS_DIR
```

## stdout

Exactly one JSON document, the result envelope, written at process exit. A wrapper accumulates
stdout into a buffer and MUST NOT parse it line by line: there is nothing to parse until exit.

The whole subprocess outcome reduces to one terminal event under two precedence rules:

```
Rule 1  stdout parses as JSON AND passes the full shape check -> the envelope is authoritative.
        error === null  -> a result event carrying `reply` as its text
        error populated -> an error event built from the envelope's error fields
        The exit code is INFORMATIONAL and does not override the envelope.

Rule 2  envelope absent, unparseable, or partial -> synthesize from exit code plus stderr tail.
        Partial JSON is never half-parsed: if any required field is missing or type-wrong,
        the envelope is treated as unparseable.
        exit 0    -> code `envelope_missing`, classification protocol, message carrying the
                     first 512 bytes of stdout
        exit != 0 -> code `engine_exit_<N>`, classification engine
```

The envelope field shape is defined in `envelope-and-errors.md` and is not duplicated here.

## stderr

Under `--display ndjson`, one JSON object per line. Each parsed object is delivered verbatim; the
wrapper does not interpret the `method` or `params` shape. Non-JSON lines and JSON-parseable
non-objects go to a separate non-JSON sink, never to the frame dispatcher, and are never fatal.

Each notification is delivered on two paths: pushed onto the handle's event iterator as
`{type: "notification", method, params}`, and dispatched to the display event callback when one was
supplied. A host subscribing to both receives every notification twice. Subscribe to one.

Everything read from stderr, JSON and non-JSON alike, is also appended to a stderr buffer, so a
crash-time tail still carries wire-event context. On failure the last 4096 characters are attached
as `stderrTail`. The field is omitted entirely when stderr was empty.

## Exit codes

The engine's classification-to-exit-code mapping lives in `envelope-and-errors.md`. What a wrapper
does with it is asymmetric, and that asymmetry is contract:

- When the envelope parses, the wrapper reads `error.classification` from the envelope and ignores
  the exit code entirely.
- The exit code is load-bearing only when no parseable envelope exists, and then only coarsely:
  0 means `envelope_missing`, anything else means `engine_exit_<N>`.

A wrapper that maps exit 2 to a protocol error or exit 3 to an approval error from the exit code
alone is implementing a different contract. 130 gets no special handling on either side; it
surfaces as `engine_exit_130`.

## Environment

Subprocess environment composition happens at spawn time, before launch, and is a security
boundary.

```
Pass-through:  every name in the allowlist, plus every name starting with AMPLIFIER_ or LC_
Allowlist:     PATH, HOME, USER, LANG, TERM, TMPDIR
extra:         merged last, wins over everything allowlisted

Blocked in `extra`:
  PYTHONPATH  PYTHONHOME  PYTHONSTARTUP  PYTHONNOUSERSITE
  LD_PRELOAD  LD_LIBRARY_PATH
  DYLD_INSERT_LIBRARIES  DYLD_LIBRARY_PATH
  PATH
```

A blocked key in `extra` fails with `env_injection_rejected`, classification protocol, before any
subprocess work. These are dynamic-loader and interpreter hooks that hijack subprocess execution.
The block applies to `extra` only; `PATH` still passes through from the process environment via the
allowlist.

### MCP config spill

MCP server configuration is always spilled to a file, never passed on argv, so a large server map
cannot overflow the OS argv limit.

```
base dir   $XDG_RUNTIME_DIR/amplifier-agent   (typically tmpfs on Linux)
           <system temp dir>/amplifier-agent  (fallback)
path       <base>/<sessionId>/mcp.json        directory mode 0700, file mode 0600
content    {"mcpServers": <map verbatim>}
```

An empty or absent map produces no file and no path. When a path was produced, the wrapper injects
`AMPLIFIER_MCP_CONFIG=<path>` into the subprocess environment and the engine's MCP tooling picks it
up through its own config discovery. Spill file cleanup is an idempotent unlink that tolerates a
missing file, performed on every iterator exit path and again on cancellation.

### Prompt spill

A prompt at or above the threshold is spilled to a file and passed as `--prompt-file <path>`, so a
large prompt cannot overflow the OS argv limit.

```
threshold  16384 bytes, measured on the UTF-8 encoded length, not the character count
base dir   $XDG_RUNTIME_DIR/amplifier-agent   (typically tmpfs on Linux)
           <system temp dir>/amplifier-agent  (fallback)
path       <base>/<sessionId>/prompt.txt      directory mode 0700, file mode 0600
content    the prompt text verbatim, UTF-8, no newline translation
```

Below the threshold the prompt stays positional, behind the `--` separator. Both transports are
valid at every size; a wrapper MAY spill unconditionally.

The file is written and closed before the subprocess is spawned. Cleanup is the same idempotent
unlink as the MCP spill, on the same exit paths.

Mode bits are POSIX-only. On Windows the file's confidentiality rests on the per-user ACL of the
system temp directory instead.

## Session handle lifecycle

Creating a handle does no subprocess work beyond the version probe: it validates lifecycle, rejects
a mid-turn approval callback, resolves the binary, builds the environment, probes, and returns. The
turn subprocess is not spawned until submit.

Submit is one-shot. A second call fails with `lifecycle_unsupported`. The returned async iterable
behaves as follows:

```
(i)    yields {type:"init", sessionId} synchronously, BEFORE any async work
(ii)   spills the MCP config if servers were supplied
(iii)  builds argv and the subprocess environment
(iv)   spawns detached (POSIX setsid) so PID == PGID
(v)    accumulates stdout; parses stderr as NDJSON
(vi)   starts a 2 s activity ticker pushing {type:"activity"}
(vii)  races exit against the optional timeout; on timeout synthesizes engine_hung THEN cancels,
       so the iterator yields a terminal event even if SIGTERM and SIGKILL hang
(viii) cleans up the spill file on every exit path
(ix)   drains the queue until the terminal event, then returns
```

The 2 second ticker preserves a host's stuck-detection signal without requiring any engine-side
emission: a host with a 10 second stuck threshold gets 5x margin. A spawn failure (ENOENT, EACCES)
surfaces as `{type:"error", code:"spawn_failed", classification:"transport"}`. Whichever of
{timeout, exit, spawn error} fires first wins; later events are ignored.

The wall-clock timeout is opt-in. A default of 10 minutes is exported but never applied
automatically; unset, `0`, or negative disables the timer entirely.

`lifecycle: "one-shot"` is the only accepted value. `"burst"` is reserved as a wire enum value and
rejected at runtime with `lifecycle_unsupported`.

A mid-turn approval callback parameter is still present in the type for forward compatibility, but
passing a non-null callback fails with `approval_not_supported_in_v1` before any subprocess work.
Accepting it with a warning would ship silent auto-allow to a host author who believed their
callback was wired up, which is exactly the failure this rejection prevents.

### Process group lifecycle

Both sides participate.

Engine side: at startup the engine makes itself a session leader when it is not already one. This
is idempotent and tolerates the failure that occurs when a debugger or test harness already owns
the session. Every MCP child the engine spawns inherits the group.

Wrapper side: spawn detached (`detached: true` / `start_new_session=True`), which makes PID == PGID
on POSIX. Cancellation signals the whole group, never the single PID:

```
SIGTERM to the process group
wait up to 5 s
if still alive: SIGKILL to the same group
then unlink the MCP spill file
```

Signal errors are swallowed: a "no such process" result just means the group is already gone.
Cancellation is idempotent, and dispose is an alias for it.

Signaling only the engine PID would orphan the engine's MCP children, holding file descriptors,
ports, and sockets open until the OS reaps them minutes later.

POSIX only, on both sides. Windows has no session groups and provides neither `os.getsid` nor
`os.setsid`, so the engine skips the session-leader step there, and a negative-PID group signal is
not a Windows operation. The containment primitive on Windows is a Job Object, a different
mechanism on both sides of the boundary rather than a flag on this one. Until that is built, the
guarantee above does not hold on Windows: cancelling reaches the engine, and MCP children may
outlive it. Engine startup and single-turn `run` are unaffected.

## Non-goals

- **The wrapper never sees or configures the bundle.** No mount plan crosses the boundary, no
  bundle path is a parameter, and there is no bundle-digest negotiation. The engine owns bundle
  resolution.
- **No binary-path constructor parameter.** Discovery is `$AMPLIFIER_AGENT_BIN`, then PATH.
- **No multi-turn stdio session.** One subprocess per turn. Conversational state lives on disk and
  is reached with `--session-id` plus `--resume`.
- **No mid-turn approval channel.** The callback parameter exists but rejects.
- **No `turn/cancel` message.** SIGTERM to the process group is the cancel.
- **No engine-side environment allowlist flags.** Environment composition is entirely the host's
  job.
