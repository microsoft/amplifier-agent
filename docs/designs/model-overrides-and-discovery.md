# Model Overrides and Discovery Design

## Goal

Add two capabilities to the `amplifier-agent` CLI so model selection becomes a first-class, externally-controllable concern: (A) the ability to **override** which model a provider uses on a given `run`, and (B) the ability to **discover** which models a provider offers.

## Background

Today `amplifier-agent` never lets a caller choose a model. The model string is buried in a static catalog (`PROVIDER_CATALOG` in `src/amplifier_agent_cli/provider_sources.py:69-108`), one hardcoded `default_model` per provider (anthropic → `claude-opus-4-5`, openai → `gpt-5.5`, azure-openai → `gpt-4o`, ollama → `llama3.2`). That default is injected into the prepared bundle's `mount_plan["providers"]` at engine boot via `inject_provider()` (called from `modes/single_turn.py:345`). There is no way to:

- ask for a different model on a per-run basis, or
- enumerate what models a provider actually offers.

This blocks downstream work. nanoclaw (`amplifier-app-nanoclaw`) wants users to set a model per agent group and to surface a model picker, but it has nothing to call: `amplifier-agent run` silently ignores any model intent, and there is no `models` subcommand to query. amplifier-agent is the capability boundary; nanoclaw can only consume what this CLI exposes. These two additions unblock that downstream wiring without committing to any nanoclaw-side design.

The discovery half is not greenfield. The Provider protocol already defines `async def list_models(self) -> list[ModelInfo]` (`amplifier-core/python/amplifier_core/interfaces.py:93`), all four provider modules already implement it, and there is a working reference for calling it from outside a full session in `amplifier_app_cli/provider_loader.py::get_provider_models()`. This design ports that reference into the agent CLI rather than inventing a new mechanism.

## Approach

Two independent change sets on a single branch, deliberately decoupled:

- **Change Set 1 — Override path.** Add `--model` and `--effort` flags to `amplifier-agent run`. The override threads through the TypeScript wrapper (`argv-builder`) into the Python engine, where it replaces the catalog `default_model` (and effort default) in the prepared bundle config. Pass-through semantics: no validation that the model belongs to the provider — the provider rejects an invalid id at its first API call with its own error.

- **Change Set 2 — Discovery path.** Add a new `amplifier-agent models list --provider X` subcommand that loads the provider module, instantiates the provider class, calls `list_models()`, and emits the result as envelope-wrapped JSON or a human-readable table. No fallback: `list_models()` is a list API; if it raises (e.g. missing API key) the command exits non-zero and propagates the error so the consumer decides what to do.

The two sets share nothing but the repo. Either can ship without the other.

## Architecture

### Override flow (Change Set 1)

```
nanoclaw / human
  │  amplifier-agent run --provider anthropic --model claude-sonnet-4-5 --effort high "prompt"
  ▼
TypeScript wrapper (wrappers/typescript/src/argv-builder.ts)
  │  assembleArgv() emits  --model <m>  --effort <e>  (mirrors existing --provider handling)
  ▼
Python engine — run command (src/amplifier_agent_cli/modes/single_turn.py)
  │  parses --model / --effort into model_override / effort_override
  │  passes them to provider injection
  ▼
provider_sources.py
  │  inject_provider(prepared, provider, model_override=…, effort_override=…)
  │    └─ build_provider_entry(name, model_override=…, effort_override=…)
  │         config["default_model"] = model_override or catalog default
  ▼
prepared.mount_plan["providers"]  → kernel boots provider with chosen model
```

The single wiring subtlety: `single_turn.py:345` calls `inject_provider(prepared, spec.provider)`, and `inject_provider()` (`provider_sources.py:169`) internally calls `build_provider_entry()`. The override must therefore thread through **both** functions — `inject_provider` gains `model_override`/`effort_override` keyword parameters that it forwards to `build_provider_entry`. The call site in `single_turn.py` passes the parsed flag values.

`build_provider_entry` already constructs `config["default_model"]` at `provider_sources.py:163`. The change is to make that line honor the override:

```python
"default_model": model_override or entry["default_model"],
```

`--effort` follows the identical pattern — it is added to the same `config` dict (e.g. `config["effort"] = effort_override` when provided) so the kernel/provider can consume it. It is bundled into Change Set 1 because it is the same silent-drop problem on the same code path; splitting it would mean revisiting the same files twice.

### Discovery flow (Change Set 2)

```
nanoclaw / human
  │  amplifier-agent models list --provider anthropic [--output json|table] [--timeout N]
  ▼
src/amplifier_agent_cli/admin/models.py  (new)
  │  load_provider_class(provider)        # entry point, then direct import
  │  _try_instantiate_provider(cls)       # tries (api_key,config), (host,config), azure shape, …
  │  asyncio.run(provider.list_models())  # async-detected; calls provider.close() if present
  │  → list[ModelInfo]
  ▼
render JSON envelope  OR  table  → stdout
exit 0 on success (incl. empty list);  exit 2 if list_models() raises
```

The provider-loading and instantiation logic is ported from `amplifier_app_cli/provider_loader.py` (`load_provider_class`, `_try_instantiate_provider`, `get_provider_models`, and their helpers `_load_provider_module` / `_get_provider_module_name`). That reference already handles the realities: provider classes are found by convention (`{Name}Provider`), constructors vary by provider (anthropic/openai use `(api_key, config)`, ollama uses `(host, config)`, azure-openai a keyword-only shape), `list_models` may be sync or async, and `provider.close()` should be awaited if it exists. The port lets exceptions propagate (no swallowing) — matching the locked "no fallback" decision.

Note the differing per-provider behaviors of `list_models()`, all of which this command surfaces faithfully:

| Provider     | `list_models()` behavior                              | Command outcome                         |
|--------------|--------------------------------------------------------|-----------------------------------------|
| anthropic    | Live API; raises without `ANTHROPIC_API_KEY`           | exit 2 + propagated error               |
| openai       | Live API; raises without `OPENAI_API_KEY`              | exit 2 + propagated error               |
| ollama       | Queries local daemon; returns `[]` on connection error | exit 0 + empty `models` + stderr advisory |
| azure-openai | Always returns `[]` (deployments are customer-specific)| exit 0 + empty `models` + stderr advisory |

## Components

### Change Set 1 — Override path

**`src/amplifier_agent_cli/modes/single_turn.py`** (~6 lines)
- Add two Click options to the `run` command (alongside the existing `--provider`/`provider_override` at `:404`):
  - `@click.option("--model", "model_override", default=None, help="Override the default model for the selected provider.")`
  - `@click.option("--effort", "effort_override", default=None, help="Override the reasoning/effort level for the selected provider.")`
- Add `model_override: str | None` and `effort_override: str | None` to the `run` function signature (near `provider_override: str | None` at `:463`).
- Forward them at the injection call (`:345`): `inject_provider(prepared, spec.provider, model_override=model_override, effort_override=effort_override)`.

**`src/amplifier_agent_cli/provider_sources.py`** (~8 lines)
- `build_provider_entry(provider_name, model_override=None, effort_override=None)`: at the `default_model` line (`:163`) use `model_override or entry["default_model"]`; when `effort_override` is set, add it to the `config` dict.
- `inject_provider(prepared, provider_name, model_override=None, effort_override=None)`: forward both overrides to `build_provider_entry`.

**`wrappers/typescript/src/argv-builder.ts`** (~6 lines)
- Extend `AssembleArgvInput` with `modelOverride?: string` and `effortOverride?: string` (mirroring `providerOverride` at `:24-25`).
- In `assembleArgv()` emit the flags after the existing `--provider` block (`:58-60`):
  ```ts
  if (input.modelOverride !== undefined) { argv.push("--model", input.modelOverride); }
  if (input.effortOverride !== undefined) { argv.push("--effort", input.effortOverride); }
  ```

**`wrappers/typescript/src/argv-builder.d.ts`** (if a hand-maintained declaration file exists for this module)
- Mirror the two new optional fields. If declarations are generated by the TypeScript build rather than hand-maintained, no manual change is needed — verify during implementation.

### Change Set 2 — Discovery path

**`src/amplifier_agent_cli/admin/models.py`** (new, ~80-110 lines)
- A Click group `models` with one subcommand `list`.
- `list` options: `--provider <name>` (required), `--output json|table` (default: auto — `table` for a TTY, `json` for a pipe, matching standard CLI conventions and the existing `tty_detect.py` helper), `--timeout <seconds>` (default 15s; wraps the live API call).
- Ported helpers from `provider_loader.py`: `_get_provider_module_name`, `_load_provider_module`, `load_provider_class`, `_try_instantiate_provider`, and a `list_models` caller analogous to `get_provider_models` (async-detect, `asyncio.run`, `provider.close()` cleanup, exceptions propagate).
- Render path: serialize each `ModelInfo` via `model_dump()` into the JSON envelope, or filter to table columns.
- Follows the style of existing admin commands (`admin/doctor.py`, `admin/prepare.py`, `admin/config_show.py`) — stderr for diagnostics, stdout reserved for structured output.

**`src/amplifier_agent_cli/__main__.py`** (~2 lines)
- Import the new group and register it alongside the existing commands (`run`, `doctor`, `prepare`, `verify`, `version`, `config`, `cache` at `:42-48`):
  ```python
  from amplifier_agent_cli.admin.models import models_group as _models_group
  cli.add_command(_models_group, name="models")
  ```

## CLI Surface

### `run` (extended)

```
amplifier-agent run --provider anthropic --model claude-sonnet-4-5 "Summarize this repo."
amplifier-agent run --provider openai --model gpt-5.5 --effort high "Plan the migration."
```

- `--model <id>` — override the catalog default model for the selected provider. Pass-through; unvalidated.
- `--effort <level>` — override the reasoning/effort level. Pass-through; unvalidated.
- Both absent → existing behavior (catalog default), unchanged.

### `models list` (new)

```
amplifier-agent models list --provider anthropic
amplifier-agent models list --provider anthropic --output json
amplifier-agent models list --provider ollama --timeout 5
```

- `--provider <name>` — required; one of `PROVIDER_CATALOG` keys. Unknown value → usage error (exit 1).
- `--output json|table` — default auto-detected from TTY.
- `--timeout <seconds>` — default 15; applies to the live `list_models()` call.

## Output Schema

JSON output is envelope-wrapped for forward compatibility (a `schema_version` lets consumers like nanoclaw evolve safely, and `fetched_at` supports cache-TTL decisions):

```json
{
  "schema_version": 1,
  "provider": "anthropic",
  "fetched_at": "2026-06-10T17:36:53Z",
  "models": [
    {
      "id": "claude-sonnet-4-5",
      "display_name": "Claude Sonnet 4.5",
      "context_window": 200000,
      "max_output_tokens": 8192,
      "capabilities": ["tools", "vision", "thinking"],
      "defaults": {"temperature": 0.7, "max_tokens": 8192}
    }
  ]
}
```

- `models` entries are the full `ModelInfo.model_dump()` (fields per `amplifier-core/python/amplifier_core/models.py:325-344`: `id`, `display_name`, `context_window`, `max_output_tokens`, `capabilities`, `defaults`).
- `fetched_at` is an ISO-8601 UTC timestamp captured at call time.

Table view filters to four columns:

```
ID                   DISPLAY NAME         CONTEXT    CAPABILITIES
claude-sonnet-4-5    Claude Sonnet 4.5    200000     tools, vision, thinking
```

`CAPABILITIES` joins the list with `, `.

## Error Handling and Exit Codes

`models list`:

| Exit | Condition                                                                 |
|------|---------------------------------------------------------------------------|
| 0    | Success — including a legitimately empty list (azure-openai, ollama-down) |
| 1    | Usage error — unknown `--provider`, bad flag                              |
| 2    | Provider error — `list_models()` raised (auth, network, timeout)          |

- **No fallback.** `list_models()` is a list API. If it raises, the error propagates and the command exits 2. The CLI does not fall back to catalog defaults, does not retry, and does not offer a `--source` flag. The consumer (nanoclaw, a human) decides what to do on failure — e.g. hardcode catalog defaults or prompt the user.
- **Empty list is a valid answer.** azure-openai always returns `[]` (deployments are customer-specific); ollama returns `[]` when the daemon is unreachable. Both exit 0 with an empty `models` array and emit a one-line stderr advisory (e.g. `# azure-openai: no live model list available; enter a deployment name manually`). The empty list is the truth, not an error.

`run`:
- `--model` / `--effort` are pass-through. An invalid model surfaces as the provider's own error at first API call — no boot-time validation, no extra API roundtrip, no catalog to keep current.

## Test Strategy

Unit tests with mocks only. No real-API E2E, no container-based tests, no CI smoke test.

| Test                                                        | Type           | Coverage                                                                                                   |
|-------------------------------------------------------------|----------------|------------------------------------------------------------------------------------------------------------|
| `wrappers/typescript/tests/argv-builder.test.ts` (extend)   | vitest, unit   | `--model` emitted when `modelOverride` set; `--effort` emitted when `effortOverride` set; both absent → baseline argv unchanged |
| Python test for `build_provider_entry` (new/extend)         | pytest, unit   | `build_provider_entry(name, model_override="X")` → `config["default_model"] == "X"`; `effort_override` lands in config; defaults unchanged when overrides absent |
| `tests/admin/test_models.py` (new)                          | pytest, unit   | `FakeProvider` returning models → correct JSON envelope shape; `FakeProvider` raising → exit 2 + stderr; `FakeProvider` returning `[]` → exit 0 + stderr advisory |

Rationale for no E2E: real-provider calls require live credentials and incur billing, and the per-provider behaviors (`list_models()` raising vs returning `[]`) are exercised deterministically via the `FakeProvider` double. The `models default` catalog-driven offline test was dropped along with that subcommand. Place `tests/admin/test_models.py` per the existing test layout — confirm the directory convention during implementation (existing admin tests live under `tests/` as `test_admin_*.py`; create `tests/admin/` only if that matches established structure).

## Out of Scope

Explicitly excluded from this design:

- **Any nanoclaw (`amplifier-app-nanoclaw`) change.** Downstream work, blocked on and unblocked by this. Not part of this branch.
- **Any provider module change.** All four already implement `list_models()` correctly.
- **Real-API integration tests.**
- **A `models default` subcommand.** Dropped — it returns a different data shape (single `default_model` string vs a `ModelInfo` list) and the consumer can read catalog defaults itself.
- **Auto-fallback or a `--source live|catalog` flag on `models list`.** Dropped — `list_models()` is a list API; failures propagate.
- **Validation that `--model` belongs to the chosen provider.** Pass-through; the provider rejects invalid ids.

## Open Implementation Questions

1. **`argv-builder.d.ts` maintenance** — whether the `.d.ts` is hand-maintained or build-generated. If generated, the two new optional fields need no manual edit; if hand-maintained, mirror them. Resolve by inspecting the wrapper build config during implementation.
2. **`tests/admin/` directory** — existing Python admin tests are named `tests/test_admin_*.py` at the top level. Confirm whether to add `tests/admin/test_models.py` (a new subdirectory) or follow the flat `tests/test_admin_models.py` convention. Match whatever the repo already does.
3. **`--effort` value space** — whether `--effort` should be a free-form pass-through string (consistent with the `--model` decision) or a Click `Choice`. Default to free-form pass-through for symmetry with `--model`; the provider validates.
