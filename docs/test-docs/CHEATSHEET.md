# amplifier-agent — Try It Cheat Sheet

A practical guide for testing the current build of `amplifier-agent`. Covers what works today, what to try, what to watch for, and what's deliberately not built yet.

---

## 0. Prerequisites

You need:

- Python 3.11+
- `uv` installed (`brew install uv` or see https://docs.astral.sh/uv/)
- An API key for at least one supported provider (see §2)

Clone & setup:

```bash
git clone git@github.com:microsoft/amplifier-agent.git
cd amplifier-agent
git checkout feat/phase-1-4-l4-implementation   # current PR branch
uv sync
```

Verify install:

```bash
uv run pytest -q              # should report 202 passed
uv run ruff check             # clean
uv run pyright                # clean
```

---

## 1. First commands (smoke test, no provider needed)

```bash
# Print version
uv run amplifier-agent --version

# See top-level help
uv run amplifier-agent --help

# See run command help
uv run amplifier-agent run --help

# Run diagnostic (reports which providers are configured, XDG paths, bundle cache state)
uv run amplifier-agent doctor

# Print resolved config with source annotations
uv run amplifier-agent config show
```

`doctor` is the safest first check — it'll tell you what's missing for the next steps.

---

## 2. Provider configuration

Set one of these env vars. Auto-detect precedence: **ANTHROPIC → OPENAI → AZURE → OLLAMA**.

```bash
# Anthropic (recommended for first try)
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
export OPENAI_API_KEY=sk-...

# Azure OpenAI
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com

# Ollama (local, no key)
export OLLAMA_HOST=http://localhost:11434
```

Verify detection:

```bash
uv run amplifier-agent doctor
# Should show the detected provider and its source env var
```

Override with flag:

```bash
uv run amplifier-agent run --provider openai "Hello"
```

If no provider is configured, `run` exits with structured error `provider_not_configured`.

---

## 3. Mode A — Single-turn (the simplest test)

One-shot invocation. Process exits after the model finishes.

```bash
# Simplest test
uv run amplifier-agent run "Hello! What can you help with?"

# With session id (for later resume)
uv run amplifier-agent run --session-id chat-1 "My favorite color is blue."

# Resume the same session
uv run amplifier-agent run --session-id chat-1 --resume "What did I say my favorite color was?"

# Fresh start, same session id (overwrites prior transcript)
uv run amplifier-agent run --session-id chat-1 --fresh "Start over."

# Verbose (stderr shows intermediate events)
uv run amplifier-agent run -v "Pick a number between 1 and 10"

# Debug (stderr shows thinking + progress)
uv run amplifier-agent run --debug "Pick a number between 1 and 10"

# Quiet (stderr suppressed)
uv run amplifier-agent run --quiet "Pick a number between 1 and 10"

# Override provider
uv run amplifier-agent run --provider openai "Hello"

# Override working directory (affects file-system tools)
uv run amplifier-agent run --cwd /tmp "List files here"
```

**Output format**:
- **stdout**: JSON result `{"text": "...", "sessionId": "..."}` (machine-parseable)
- **stderr**: `[info]` / `[warn]` / `[error]` lines (suppressed by `--quiet`, more verbose with `-v` / `--debug`)

> **First-run cost:** The first `amplifier-agent run` on a fresh machine pays a
> 5–30 s cliff. The vendored opinionated manifest references module repos at
> `@main`; on first invocation foundation `git clone`s them and runs
> `uv pip install --no-sources` for each. Subsequent runs hit the warm XDG
> pickle at `$XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)>/`
> and complete in under a second. The post-install hook
> (`amplifier-agent-post-install`) is the opt-in amortizer if you want a primed
> cache without paying it on the first user-facing run.
>
> Editing `src/amplifier_agent_lib/bundle/bundle.md` changes the cache key
> (`sha256(bundle.md)`) and automatically invalidates the warm pickle — no
> `cache clear` needed.

**Where stuff lives**:
- Bundle cache: `~/.cache/amplifier-agent/prepared/<version>/`
- Session transcripts: **not implemented in this build.** Planned as a CLI-layer hook (modeled on `amplifier-app-cli`'s `IncrementalSaveHook`); see `docs/designs/2026-05-19-baked-in-bundle-revisit.md`. `--session-id` is currently a logical tag only; nothing writes to `$XDG_STATE_HOME/amplifier-agent/sessions/` yet.

---

## 4. Approval testing

Some tools (file writes, shell commands) request approval before acting. Default behavior is **prompt-when-TTY, deny-otherwise**.

```bash
# Interactive — model will prompt you on stderr when it wants to do something sensitive
uv run amplifier-agent run "Create a file named test.txt in /tmp with the text 'hello'"

# Auto-accept all (apt-style)
uv run amplifier-agent run -y "Create a file named test.txt in /tmp"

# Auto-deny all
uv run amplifier-agent run -n "Create a file named test.txt in /tmp"

# Non-interactive (no TTY) → auto-denied with structured error
echo "" | uv run amplifier-agent run "Create a file named test.txt in /tmp"
```

When prompted interactively, the format on stderr is:

```
── APPROVAL REQUESTED ──
Kind:    bash.execute
Message: ...
Content: {...}
─────────────────────────
Approve? [y/N/c]:
```

Respond `y` to accept, `N` (or Enter) to decline, `c` to cancel the entire turn.

---

## 5. Mode B — Multi-turn JSON-RPC over stdio

For testing programmatic integration. Reads JSON-RPC requests from stdin, writes responses + notifications to stdout. Exits on EOF or `agent/shutdown`.

```bash
# Start the stdio loop
uv run amplifier-agent run --stdio
```

Send JSON-RPC requests (one JSON object per line):

```json
{"jsonrpc":"2.0","id":1,"method":"agent/initialize","params":{"protocolVersion":"2026-05-aaa-v0","clientInfo":{"name":"manual-test","version":"0.1"},"capabilities":{"approval":{"supported":false},"display":{"supported":true},"streaming":{"supported":true}}}}
{"jsonrpc":"2.0","id":2,"method":"session/create","params":{"sessionId":"test-session-1"}}
{"jsonrpc":"2.0","id":3,"method":"turn/submit","params":{"sessionId":"test-session-1","prompt":"Hello!"}}
{"jsonrpc":"2.0","id":4,"method":"agent/shutdown"}
```

The agent responds with:

- `agent/initialize` → server capabilities + bundle info + serverInfo
- `session/create` → success or error
- `turn/submit` → streaming `notifications/*` followed by the final response
- `agent/shutdown` → graceful exit

**Try it from Python** (no manual JSON typing):

```python
import asyncio, json

async def test_stdio():
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "amplifier-agent", "run", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def send(req):
        proc.stdin.write((json.dumps(req) + "\n").encode())
        await proc.stdin.drain()

    async def recv():
        line = await proc.stdout.readline()
        return json.loads(line) if line else None

    # initialize
    await send({"jsonrpc":"2.0","id":1,"method":"agent/initialize",
                "params":{"protocolVersion":"2026-05-aaa-v0",
                          "clientInfo":{"name":"test","version":"0.1"},
                          "capabilities":{"approval":{"supported":False},
                                          "display":{"supported":True},
                                          "streaming":{"supported":True}}}})
    print("INIT response:", await recv())

    # session
    await send({"jsonrpc":"2.0","id":2,"method":"session/create",
                "params":{"sessionId":"py-test-1"}})
    print("CREATE response:", await recv())

    # turn (will stream notifications then final response)
    await send({"jsonrpc":"2.0","id":3,"method":"turn/submit",
                "params":{"sessionId":"py-test-1","prompt":"Say hello."}})
    # Drain until we see the response for id=3
    while True:
        msg = await recv()
        if msg is None:
            break
        print("EVENT:", msg.get("method") or f"response for id={msg.get('id')}")
        if msg.get("id") == 3:
            break

    # shutdown
    await send({"jsonrpc":"2.0","id":4,"method":"agent/shutdown"})
    await proc.wait()
    print("EXIT:", proc.returncode)

asyncio.run(test_stdio())
```

Or look at `tests/cli/test_stdio_loop_subprocess.py` for canonical examples.

---

## 6. Admin commands

```bash
# Diagnose environment (Python, providers, XDG paths, bundle cache)
uv run amplifier-agent doctor

# Print resolved config with source annotations
uv run amplifier-agent config show

# Clear the prepared-bundle cache (forces re-prep on next invocation)
uv run amplifier-agent cache clear

# Cache clear is idempotent — safe to run multiple times
```

`doctor` reports:

- Python version OK?
- Which providers are detected from env?
- XDG cache/config/state paths writable?
- Is the bundle cache prepared? (`needs prepare` if not)

`config show` annotates each value with its source — env var name, default, override flag, etc.

---

## 7. Inspecting state on disk

```bash
# Bundle cache contents (the prepared.pickle + manifest.json for the current version)
ls -la ~/.cache/amplifier-agent/prepared/
```

**Session transcript persistence is NOT implemented in this build.** The bundle uses the `context-simple` context module, which buffers messages and handles compaction in-memory but does **not** write transcripts to disk. The cheatsheet previously described `~/.local/state/amplifier-agent/sessions/<id>/context-messages.jsonl` and claimed the `context-persistent` module owned that format — both claims were inaccurate. `context-persistent`'s README explicitly states *"No auto-save: Does not persist context back to files"*; it loads `AGENTS.md`-style memory files at session start, it doesn't write a session log.

Persistent transcripts are planned as a CLI-layer hook (modeled on `amplifier-app-cli`'s `IncrementalSaveHook` + `SessionStore`). The shape of that hook — and whether transcripts should live in the bundle or in the CLI host — is part of the broader baked-in-bundle architectural revisit captured in `docs/designs/2026-05-19-baked-in-bundle-revisit.md`. Until that lands, `--session-id` is a logical tag only.

---

## 8. Run the test suite

```bash
# Full suite (should report 202 passed)
uv run pytest -q

# Just the CLI tests
uv run pytest tests/cli/ -q

# A specific file
uv run pytest tests/cli/test_single_turn.py -v

# Mode B subprocess integration tests
uv run pytest tests/cli/test_stdio_loop_subprocess.py -v

# With coverage
uv run pytest --cov=src --cov-report=term-missing
```

The tests are also good usage examples. In particular:

- `tests/cli/test_single_turn.py` — every Mode A flag combination
- `tests/cli/test_stdio_loop_subprocess.py` — full Mode B integration via `asyncio.create_subprocess_exec`
- `tests/test_l14_synthesis.py` — L14 result/final synthesis contract
- `tests/test_jsonrpc.py` — wire framing + defensive read

---

## 9. Lint and type-check

```bash
uv run ruff check                # lint
uv run ruff format --check       # formatting check
uv run ruff format               # auto-fix formatting
uv run pyright                   # types
```

All should be clean. `tests/test_stdout_discipline.py` also enforces the no-`print()` / no-`sys.stdout.write` invariant in the library.

---

## 10. What's NOT in this build yet

These are intentionally deferred:

| Component | When | Notes |
|---|---|---|
| TS wrapper (`amplifier-agent-client-ts`) | Phase 2.1 | Designed, implementation deferred |
| Python wrapper (`amplifier-agent-client-py`) | Phase 2.1 | Designed, implementation deferred |
| NanoClaw adapter | Phase 2.2 | Adapter shape designed |
| Paperclip adapter | Phase 2.2 | Adapter shape designed |
| Turnkey installer (`curl ... \| sh`) | §2 of checkpoint | Pattern documented |
| Container packaging | §7 of checkpoint | NanoClaw container env contract NC-L1..L5 preserved |
| Cold-start measurement | Phase 2.0e | The load-bearing benchmark — coming soon |

You can build against the CLI directly today (shell-out from any language) using the JSON-RPC over stdio protocol. Wrappers will make this idiomatic later.

---

## 11. Known follow-ups (captured in PR #3)

Things flagged during execution that don't break the CLI but should be fixed:

1. **JSON-RPC error codes**: `stdio_loop.py:331, 375` use `-32600` (Invalid Request) and `-32601` (Method Not Found) where `-32603` (Internal Error) would be semantically correct. Affects how host clients categorize the error.
2. **Defensive skip logging**: `jsonrpc.read_message()` skips malformed lines silently; should add `logging.warning(...)` for production visibility.
3. **Stale docstring**: `__main__.py:11` still says Mode B is "stubbed until Task 8" — Phase 3 implemented it; needs update.

These will land in a child PR.

---

## 12. Where to look in the code

```
src/amplifier_agent_lib/             # transport-free engine library
├── engine.py                        # Engine class
├── protocol/                        # wire types
├── protocol_points/                 # Approval/Display abstractions + defaults
├── persistence.py                   # XDG paths
├── spawn.py                         # internal sub-agent spawner
├── jsonrpc.py                       # newline-framed JSON-RPC + L14 synthesis
└── bundle/                          # built-in bundle + cache + post-install

src/amplifier_agent_cli/             # the CLI binary
├── __main__.py                      # click entry point
├── modes/single_turn.py             # Mode A
├── modes/stdio_loop.py              # Mode B
├── admin/                           # doctor, config show, cache clear
├── provider_detect.py               # env-var precedence
└── tty_detect.py                    # TTY-aware approval

tests/                               # 202 tests — also usage examples
docs/designs/                        # design checkpoint + references
docs/decisions/                      # spike decisions (cache serialization)
docs/plans/                          # the 4 phase plans
```

---

## 13. Reporting issues

If something breaks:

1. Capture `uv run amplifier-agent doctor` output
2. Capture `uv run amplifier-agent config show` output
3. Re-run the failing command with `--debug` and capture stderr
4. Open an issue at https://github.com/microsoft/amplifier-agent/issues

For Mode B issues, also capture the JSON-RPC request that triggered the failure.

---

🤖 Built with [Amplifier](https://github.com/microsoft/amplifier).
