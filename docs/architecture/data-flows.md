# Data Flows

Step-by-step traces of the three flows that carry real traffic: a CLI single turn, an
HTTP chat completion, and a wrapper SDK driving the CLI as a subprocess.

These are navigation aids, not contracts. Contracts live in `docs/SPEC.md`, which stays
free of implementation detail. This document is the opposite: it names modules and cites
lines on purpose.

## 1. CLI single turn

`amplifier-agent run "<prompt>"`

```
__main__.main -> single_turn.run -> _execute_turn -> Engine -> _runtime.handler -> kernel
```

1. **Entry.** `amplifier_agent_cli/__main__.py:78` `main()` invokes the Click group at
   `:55`. `run` is registered at `:61` from `amplifier_agent_cli/modes/single_turn.py:641`.

2. **Session leader.** `single_turn.py:669` calls `os.setsid()` when the process is not
   already a session leader, so MCP children spawned later share one process group and
   die with the parent. Failure is tolerated (debuggers, test harnesses).

3. **Argv validation.** Mutually exclusive flags at `:682` (`-y`/`-n`), `:686`
   (`--quiet` vs `-v`/`--debug`), `:690` (`--resume` vs `--fresh`). Missing prompt at
   `:694` exits 2. Malformed JSON flag values go through `_parse_json_or_atpath`
   (`:222`) which emits the error envelope via `_emit_argv_envelope` (`:171`).

4. **Host config.** `:708` `load_config(config_arg=config_path)` from
   `amplifier_agent_lib/config/loader.py`. The schema is closed at the top level; a
   `ConfigError` becomes an envelope with exit code 2.

5. **Provider resolution.** `:725-728`. `host_config["provider"]["module"]` wins;
   otherwise `_read_bundle_default_provider()` (`:56`) parses `default_provider:` out
   of the vendored `bundle.md` front matter. A missing or non-string value is a bundle
   integrity error. Per-provider settings come from
   `provider_sources.provider_config_from_host` at `:734`.

6. **Protocol points.** `:739` builds `CliApprovalSystem` with the mode resolved by
   `_resolve_approval_mode` (`:82`): argv flag, then `host_config.approval.mode`, then
   TTY, then fail-fast with `approval_unconfigured` for headless runs with no policy.
   `:746-753` selects `JsonDisplaySystem` under `--display ndjson`, otherwise
   `CliDisplaySystem`. Both write to `sys.stderr`
   (`amplifier_agent_lib/protocol_points/defaults_cli.py:46` and `:152`).

7. **Protocol version.** `:771` compares `--protocol-version` against the compiled
   `PROTOCOL_VERSION` (`protocol/methods.py:11`, currently `0.3.0`). Strict equality
   unless `host_config.allowProtocolSkew` is set.

8. **Workspace.** `:805` `resolve_workspace(argv, env, cwd)` from
   `amplifier_agent_lib/persistence.py:73`. Explicit slugs are validated against
   `SLUG_RE` (`persistence.py:29`); the cwd fallback goes through
   `derive_workspace_from_cwd` (`persistence.py:48`). An invalid slug exits 2 with
   `argv_workspace_invalid`.

9. **Stdout capture.** `:816` stashes the real stdout object, then `:825` redirects
   `sys.stdout` to stderr for the whole turn under `--output json`. Any stray `print()`
   inside the bundle lands on stderr and cannot corrupt the envelope.

10. **Bundle prepare.** `_execute_turn` at `:461` calls
    `bundle/cache.py:71` `load_and_prepare_cached(aaa_version=__version__)`. Warm path
    unpickles `prepared.pickle` from
    `~/.amplifier-agent/cache/prepared/<version>/<sha256(bundle.md)[:16]>/`
    (`cache.py:42`). Cold path runs `bundle/loader.py:23` against the vendored
    `bundle.md` (`bundle/__init__.py:24`) and writes the pickle plus `manifest.json`.

11. **Mode resolution.** `:485` `resolve_mode(spec.mode, discover_known_modes(...))`
    (`mode_resolution.py:123`). Placed after prepare, because prepare is what puts the
    `hooks-mode` discovery package on `sys.path`, and before the `--fresh` rmtree, so a
    rejected turn deletes nothing and costs no tokens.

12. **Provider injection.** `:508` clears `mount_plan["providers"]` (bundle.md declares
    every catalog stub so cold-prepare can install them), then `inject_provider` and
    `inject_routing_matrix` at `:509-510`. Credentials resolve per invocation, so no
    secret ever enters the pickle.

13. **Turn handler.** `:524` `make_turn_handler` (`_runtime.py:316`). At construction
    time it resolves the cwd (`:382`), resolves the workspace again with identical
    inputs (`:387`), computes `state_root()/workspaces/<slug>` (`:395`), runs
    `prepare_bundle_for_session` (`:403`, defined at `:65`) which does the
    `mcp.configPath` env forward, the `merge_config` overlay onto the mount plan, the
    `hook-context-intelligence` workspace seed, and the built-in skills/modes path
    injection, and finally hydrates the agent overlays (`:416`).

14. **Engine boot.** `:532` constructs `Engine` (`engine.py:78`) with the handler and
    protocol points. `:550` `engine.boot(init_params, bundle_override=prepared)`
    performs the strict version check (`engine.py:153`) and capability negotiation
    (`engine.py:171`).

15. **Turn submit.** `:558` `engine.submit_turn` (`engine.py:185`) builds a
    `TurnContext` (`engine.py:213`) carrying `session_id`, `turn_id`, `prompt`, and both
    protocol points, then awaits the handler.

16. **Session create.** `_runtime.py:422` is the handler. It mints an ephemeral
    telemetry id when no session id was supplied (`:444`), builds the `SessionStore`
    over the workspace root (`:450`), and on resume loads the transcript (`:453`) and
    repairs it through foundation's `diagnose_transcript`/`repair_transcript`
    (`:464`, helper at `:225`). `:470` `prepared.create_session(...)`.

17. **Wiring.** `:483-484` writes `workspace` and `project_slug` onto
    `coordinator.config`. `:504` seeds `session_state["active_mode"]` when `--mode` was
    passed (non-sticky, per turn). `:516` sets default event fields so every kernel
    event carries session and turn ids. `:520-522` registers `display.emit` and
    `approval.request` as coordinator capabilities.

18. **Hook mount.** `:528` `mount_streaming_hook(session.coordinator, {})` from
    `bundle/hook_streaming.py`. That hook subscribes to kernel events and emits each
    through `display.emit`. It filters against its own `CANONICAL_WIRE_EVENTS` tuple
    (`hook_streaming.py:30`), the seven kernel-sourced event types. The protocol's
    `CANONICAL_DISPLAY_EVENTS` (`protocol/notifications.py:29`) is wider by two:
    `progress` and `error`, which do not originate in the kernel event stream.

19. **Transcript replay.** `:538-541` pushes the loaded transcript into the context
    module via `set_messages`, guarded by `hasattr` so a context module without the
    method is skipped rather than crashing.

20. **Incremental save.** `:559-564` registers `IncrementalSaveHook`
    (`incremental_save.py:32`) on `tool:post`, checkpointing the transcript after every
    tool call.

21. **Spawn policy.** `:584-589` registers `session.spawn` so the `delegate` tool can
    create child sessions through `spawn.py:317` `spawn_sub_session`, using the
    pre-hydrated agent overlays.

22. **Execute.** `:604` `dispatch_skill_or_execute(session, ctx.prompt,
    prompt_role=USER_TURN_ROLE)` (`skill_dispatch.py`). A prompt starting with
    `!amplifier:skill` (`skill_dispatch.py:76`) goes deterministically to the mounted
    `load_skill` tool; everything else flows to `session.execute` unchanged. The sigil is
    honored only for genuine user turns.

23. **Final save.** `:610-618` persists the full transcript after the turn. The
    incremental hook covers crashes mid tool call; this covers conversational turns that
    never fire `tool:post`.

24. **Envelope.** Back in `single_turn.py:905-914`, `_build_envelope` (`:381`) writes
    exactly one JSON line to the captured real stdout under `--output json`. Under
    `--output text` (`:916`) only the reply text is written. Error paths at `:830-903`
    build the error envelope (`:339`) and map classification to exit code via
    `_EXIT_CODE_BY_CLASSIFICATION` (`:314`).

25. **Audit.** `:919` `_write_audit` (`:269`) writes
    `workspaces/<ws>/sessions/<id>/audits/turn-<id>.json` with sha256 digests of argv
    and env. No session id means no audit.

26. **Shutdown.** `engine.shutdown()` runs in the `finally` at `single_turn.py:560`
    (`engine.py:223`), idempotent.

## 2. HTTP chat completion

`amplifier-agent serve chat-completions` then `POST /v1/chat/completions`

```
serve -> uvicorn -> lifespan (once) -> route -> run_chat_turn -> kernel
                                            \-> display queue -> translator -> SSE
```

1. **Launch.** `amplifier_agent_cli/admin/serve.py:119` `chat-completions` publishes
   host and port into the environment (`:161-164`) and calls `uvicorn.run` at `:198`.

2. **Lifespan, once per process.** `amplifier_agent_http/app.py:69`.
   - `:75` `load_config()` for wire-shape settings (bind, port, api key, model label).
   - `:85` `load_and_prepare_cached` for the `PreparedBundle`.
   - `:104-117` optional host config via `--config`, propagating `ConfigError` so a bad
     config fails startup loudly.
   - `:129` `resolve_workspace` at process scope (single tenant; per-request workspace
     is not supported).
   - `:143` `prepare_bundle_for_session`, the same helper the CLI uses. `approval.mode`
     is deliberately not applied: the HTTP face has no human-in-the-loop seam.
   - `:174-187` `resolver.async_resolve` per catalog provider, so the provider modules
     are importable before model enumeration.
   - `:191` agent overlay hydration.
   - `:210-233` skills and modes discovery, each in its own try block, each recording a
     discovery error flag so a route can distinguish a bad caller name (400) from broken
     machinery (503).
   - `:278-328` per-provider model enumeration, building `available_models` and
     `served_models_registry`. Reseller ids are namespaced `<provider>/<id>` (`:325`).
     Zero served models exits 2 (`:344-350`).
   - `:352-400` one synthetic `mode-<name>` routing alias per discovered mode, kept out
     of `available_models` so it never shows in a client model picker.
   - `:420` writes the state file that `serve status` / `stop` / `restart` read.

3. **Auth.** Every chat request depends on `require_bearer`
   (`amplifier_agent_http/_auth.py:21`), wired at `routes/chat_completions.py:687`.

4. **Model routing.** `routes/chat_completions.py:729-758`. A `mode-<name>` alias
   resolves to its base model, provider, and mode. Otherwise the model is looked up in
   `served_models_registry`; an unknown model is a hard 400 with `unknown_model`, no
   silent fallback. `:765` strips the reseller namespace before the id leaves the layer.

5. **Mode directive.** `:774` `_detect_mode_from_messages` (`:86`) scans system messages
   for `[amplifier-agent:mode=<name>]`. This is the primary signal for the opencode
   integration; the model alias is the backward-compatible fallback. `:786-819`
   validates through `resolve_mode`, returning 400 `unknown_mode` for a bad name and 503
   `modes_unavailable` when discovery itself is broken. Both happen before the
   `StreamingResponse` is constructed, because once Starlette commits a 200 the status
   can no longer change.

6. **History split.** `:821` `_split_history_and_prompt` (`:148`) separates prior turns
   from the current prompt and returns a `history_eligible` mask marking which entries
   came from genuine client `role="user"` messages. That mask gates skill sigil
   rehydration. `_contain_system_messages` (`:270`) wraps client system messages so they
   cannot impersonate the bundle's own system prompt.

7. **Session correlation.** `:850` reads `X-Client-Session-Id`, falling back to
   `X-Session-Id` (opencode / Vercel AI SDK). Present means a deterministic
   `http-<client-sid>` session id, so all turns of one client session land in
   `state/workspaces/<ws>/sessions/http-<client-sid>/`. The workspace itself stays at
   process scope.

8. **Turn.** `_session_runner.py:139` `run_chat_turn`. Under `_create_session_lock`
   (`:267`) it swaps `mount_plan["providers"]` for the per-request provider (`:281-287`),
   re-seeds the `hook-context-intelligence` workspace (`:290-299`), calls
   `create_session` (`:307`), and restores the lifespan state in a `finally` (`:316-318`)
   so concurrent requests start clean. `:330` writes the workspace aliases, `:342` seeds
   `active_mode`, `:346` sets default event fields, `:354-356` registers `display.emit`
   and `approval.request`, `:360` mounts the streaming hook.

9. **Event path.** The display system is an `HttpQueueDisplaySystem` writing to an
   `asyncio.Queue`. `_stream_chat_completion` (`routes/chat_completions.py:428`) opens
   the stream with a role chunk (`:487`), then drains the queue (`:495`). Each `get()` is
   bounded by `asyncio.wait_for` so a timeout emits an SSE keepalive comment (`:497-500`)
   during silent phases like extended thinking. Usage events are absorbed into running
   totals rather than becoming chunks (`:506-520`); everything else goes through
   `_event_translator.translate_event` (`:58`) and is emitted as an OpenAI-shaped SSE
   chunk built in `_wire.py` (`sse_data` at `:316`, `sse_keepalive` at `:326`).

10. **Termination.** `:534` awaits the turn task to surface exceptions. A host-tool yield
    (`_host_tool_signal.py:32`) sets `finish_reason: "tool_calls"` and the accumulated
    tool call is emitted as the terminal chunk; otherwise a stop chunk carries the summed
    usage, including `prompt_tokens_details.cached_tokens` and `cost_usd`. `activeMode` is
    echoed on the terminal chunk, the wire counterpart of the CLI envelope's
    `metadata.activeMode`.

11. **Non-streaming.** `stream: false` routes to `_collect_completion` (`:618`), which
    returns a single JSON body in the OpenAI `chat.completion` shape.

## 3. Wrapper subprocess

The TypeScript and Python SDKs drive the CLI as a subprocess. Paths below are from
`wrappers/typescript/src/`; `wrappers/python-py/src/amplifier_agent_py/` mirrors them
module for module, and `wrappers/conformance/` holds the cross-language fixtures that
keep the two honest.

1. **Version probe.** `spawn.ts:156` `probeEngineVersion` runs
   `<bin> version --json` with a 5s timeout and parses
   `{ version, protocolVersion, bundleDigest? }`.

2. **Version check.** `version.ts:39` `checkProtocolVersion` compares the wrapper's
   compiled constant against the engine's reported value. Strict equality unless
   `allowSkew` is set. A mismatch returns `protocol_version_mismatch` with a remediation
   string, and the wrapper never spawns the turn.

3. **Environment.** `spawn.ts:107` `buildEnv` passes through only allowlisted names plus
   anything starting with `AMPLIFIER_` or `LC_`. A blocked key in `extra` is rejected
   with `env_injection_rejected`.

4. **Argv assembly.** `argv-builder.ts` is pure: no I/O, no environment reads. It emits
   `run` with `--output json`, the protocol version, and the optional `--resume`,
   `--cwd`, `--config`, `-y`/`-n`, `--display ndjson`, `--workspace`, with the prompt
   last as a positional. MCP server maps are spilled to a temp file upstream
   (`mcp-spill.ts`) and passed via the host config or `AMPLIFIER_MCP_CONFIG`.

5. **Spawn.** `session.ts` starts the child, attaches an NDJSON reader to `stderr`, and
   buffers `stdout` whole.

6. **Stdout parse.** `run-output-parser.ts` implements the precedence rule: if the
   envelope parses against the full required field set, the envelope is authoritative
   and the exit code is informational. Partial JSON is never half-parsed; a missing
   required field makes the whole envelope unparseable.

7. **Stderr parse.** Each stderr line under `--display ndjson` is a JSON-RPC
   notification in the canonical display taxonomy
   (`protocol/notifications.py:29`). If the turn returned a non-null `reply` but no
   `result/final` arrived, the wrapper synthesizes one before closing the iterable, so
   consumers always see a terminal event.

8. **Failure synthesis.** With no parseable envelope, the wrapper builds an error event
   from the exit code and the last 4096 bytes of stderr
   (`run-output-parser.ts:23` `STDERR_TAIL_BYTES`). The classification-to-exit-code
   table is in `docs/spec/envelope-and-errors.md`; how the wrapper consumes it is in
   `docs/spec/wrapper-contract.md`.

## Stream discipline

Two rules, and they are the reason the subprocess protocol works at all.

```
stdout   exactly one JSON envelope per `run --output json` invocation, nothing else
stderr   display events (text or NDJSON) plus every diagnostic the process produces
```

Enforcement is layered:

- `amplifier_agent_lib` never touches stdout. `tests/test_stdout_discipline.py` is a
  static check: it strips docstrings and comments via `tokenize` plus `ast`, then fails
  if any executable line in the package calls `print(` or references `sys.stdout`. All
  output must flow through the injected `DisplaySystem`.
- The CLI captures the real stdout object before the turn
  (`single_turn.py:816`) and redirects `sys.stdout` to stderr for the duration
  (`:825`). A misbehaving bundle module that prints cannot corrupt the envelope.
- The envelope is written directly to the captured object exactly once
  (`:913`, and once per error path at `:865` and `:890`).

Without this, a single stray `print()` anywhere in the bundle, in a tool module, or in a
transitively imported dependency would make the wrapper's stdout parse fail and turn a
successful turn into a synthesized `envelope_missing` error.
