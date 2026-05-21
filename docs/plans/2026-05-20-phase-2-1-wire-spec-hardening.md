# Phase 2.1 — Wire Spec Hardening Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Promote the Python `TypedDict`s in `src/amplifier_agent_lib/protocol/` to the authoritative source of a generated, language-neutral wire specification — Markdown reference + JSON Schemas — and lay down the shared YAML wire-sequence fixture corpus that Plan 3 (TS + Py wrappers) will consume.

**Architecture:** A single generator `protocol/_gen.py` reads the TypedDicts via `typing.get_type_hints()` and emits `protocol/spec.md` (one human-readable file) plus `protocol/schemas/*.schema.json` (one Draft 2020-12 schema per TypedDict). All generated artifacts are committed to git and gated by a CI staleness test. Five YAML fixtures under `protocol/conformance/fixtures/` codify the cross-language behavioral contracts (D7); a small loader + structural validator makes them safely loadable by future wrapper test harnesses. No wrapper code is written in this plan.

**Tech Stack:** Python 3.12+, `typing.get_type_hints()` / `typing.get_origin()` / `typing.get_args()`, `click` for the CLI entry point, `pyyaml` (added dependency) for fixture loading, `pytest` + `pytest-asyncio` for tests, `hatchling` for wheel packaging.

---

## Audience note

You (the implementer) have zero context on this codebase but are skilled at coding. Read the locked design at `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` first — especially §4.1, §8 D1, §8 D7, and §10.3. Phase 2.0c (the predecessor phase) is already merged on `main`; this plan builds on those artifacts. Run every command from the repo root unless stated otherwise.

## Conventions used in this plan

- **Per-task ceremony is tight.** Each task body is a numbered list mixing test-write, verify-fail, implement, verify-pass, and commit. The TDD cycle is **mandatory** but no longer split into separate numbered tasks — perform every bullet in order.
- **One commit per task.** Use conventional-commits style; see `git log --oneline 4166ae3..3720352` for examples.
- **`python_check` is checkpointed.** Run it at the explicit "🔎 Quality checkpoint" markers between task groups, not per task.
- **Pattern references** point at file:line in the existing codebase. Read those before guessing.
- **All Python commands use `uv run`** (this repo uses `uv`). Example: `uv run pytest tests/test_protocol_gen.py -v`.
- **The implementer is the same person across tasks.** If something written earlier in this plan is contradicted later, the later version wins — that means a real issue surfaced. Stop and ask.

## Source-of-truth files the generator reads

| File | What lives there |
|---|---|
| `src/amplifier_agent_lib/protocol/methods.py` | `PROTOCOL_VERSION = "2026-05-aaa-v0"`, `InitializeParams`/`InitializeResult`, `TurnSubmitParams`/`TurnSubmitResult`, plus session, shutdown, cache-info shapes (15 TypedDicts) |
| `src/amplifier_agent_lib/protocol/notifications.py` | `CANONICAL_DISPLAY_EVENTS` tuple (9 events) + 11 notification TypedDicts |
| `src/amplifier_agent_lib/protocol/errors.py` | `AaaError` exception, `ErrorCode` StrEnum (16 codes) |
| `src/amplifier_agent_lib/protocol/capabilities.py` | `ClientCapabilities`, `ServerCapabilities`, `ApprovalCapability`, `DisplayCapability`, `negotiate_capabilities()` |

**Important:** The generator is dumb. It mirrors what's in the code. It does **NOT** add fields the design doc mentions but the code does not have. If you spot a divergence between the design `§4.4` event taxonomy (9 events including `turn/started`, `assistant/text`, etc.) and the code's `CANONICAL_DISPLAY_EVENTS` (9 events including `result/delta`, `thinking/delta`, etc.) — that's a known gap, NOT a bug for this plan. Per D1, code wins. Note it as a follow-up if you want, but do not edit `notifications.py` to "fix" it.

## Scope boundaries (re-stated for safety)

**IN:** generator + generated artifacts + fixtures + loader + staleness CI + wheel packaging + exit-gate test.

**OUT:** TS wrapper, Py wrapper, conformance harnesses (TS or Py), cross-language parity lint, any test that *executes* against the wrappers (none exist yet).

---

## Task list (11 tasks total)

```
Task 1   Pre-flight: feature branch + plan commit
Task 2   protocol/_gen.py skeleton (Click CLI)
Task 3   TypedDict → JSON Schema extractor (TDD core)
Task 4   Wire up all 4 protocol modules → schemas/*.schema.json
Task 5   Markdown spec generator (spec.md)
                                                            🔎 Quality checkpoint A
Task 6   CI staleness regression test + regen command
Task 7   Fixture loader + structural validator (TDD)
Task 8   Fixtures (1/2): L14 synthesis + capability negotiation
Task 9   Fixtures (2/2): subagent lineage + version skew + resume continuity + loader smoke
                                                            🔎 Quality checkpoint B
Task 10  pyproject.toml wheel packaging for new artifacts
Task 11  Phase 2.1 exit-gate integration test
```

---

## Task 1 — Pre-flight: feature branch + plan commit

**Files:**
- Create: feature branch `feat/phase-2-1-wire-spec-hardening` off current `main`
- Commit: `docs/plans/2026-05-20-phase-2-1-wire-spec-hardening.md` (this file)

**Steps:**
1. Confirm you are on `main` and the tree is clean (the untracked file `docs/architecture/amplifier-as-agent-presentation.html` is **explicitly out of scope** — do not stage it, do not delete it, just leave it alone for the entire plan).
2. Run: `git checkout -b feat/phase-2-1-wire-spec-hardening`.
3. Run: `git add docs/plans/2026-05-20-phase-2-1-wire-spec-hardening.md && git commit -m "docs(phase-2-1): implementation plan for wire spec hardening"`.
4. Run: `git status` — confirm clean except the ignored HTML file.

No tests in this task. Commit is documentation only.

---

## Task 2 — `protocol/_gen.py` skeleton

**Files:**
- Create: `src/amplifier_agent_lib/protocol/_gen.py`
- Create: `tests/test_protocol_gen.py`

**Goal:** Establish the Click-based CLI entry point and module layout. The generator does nothing useful yet — it just parses args, prints a banner, and creates the output directory. We isolate the CLI plumbing here so subsequent tasks can focus on the type-translation logic.

**Pattern reference:** CLI shape mirrors `src/amplifier_agent_cli/admin/verify.py:76-90` — `@click.command()` at module bottom, internal helpers above.

**Steps:**
1. **Write the failing test first.** Create `tests/test_protocol_gen.py` with one test using Click's `CliRunner` to invoke `_gen.main(["--output-dir", str(tmp_path)])` and assert exit code 0 plus that `tmp_path / "schemas"` exists.

```python
# tests/test_protocol_gen.py
"""Tests for protocol/_gen.py — the wire-spec generator."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner


def test_gen_cli_runs_and_creates_output_dirs(tmp_path: Path) -> None:
    """The generator CLI runs cleanly and prepares the schemas/ subdirectory."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])

    assert result.exit_code == 0, f"stdout={result.output!r} exc={result.exception!r}"
    assert (tmp_path / "schemas").is_dir(), "schemas/ subdirectory should be created"
```

2. **Verify it fails:** `uv run pytest tests/test_protocol_gen.py::test_gen_cli_runs_and_creates_output_dirs -v` → expect `ModuleNotFoundError` or similar.

3. **Implement `_gen.py`:**

```python
# src/amplifier_agent_lib/protocol/_gen.py
"""Wire-spec generator.

Reads TypedDicts in this package and emits a language-neutral spec:
    <output_dir>/spec.md             — human-readable Markdown reference
    <output_dir>/schemas/*.schema.json — JSON Schema (Draft 2020-12) per TypedDict

Per design §8 D1, Python TypedDicts are the authoritative wire-spec source.
The Markdown and JSON Schema outputs are GENERATED — never hand-edit them.

Regenerate via:
    uv run python -m amplifier_agent_lib.protocol._gen \\
        --output-dir src/amplifier_agent_lib/protocol
"""

from __future__ import annotations

from pathlib import Path

import click


@click.command()
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory to write spec.md and schemas/ into.",
)
def main(output_dir: Path) -> None:
    """Generate spec.md and JSON Schemas from this package's TypedDicts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir = output_dir / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    click.echo(f"[gen] output directory ready: {output_dir}")


if __name__ == "__main__":
    main()
```

4. **Verify it passes:** `uv run pytest tests/test_protocol_gen.py -v`.

5. **Commit:** `git add src/amplifier_agent_lib/protocol/_gen.py tests/test_protocol_gen.py && git commit -m "feat(protocol): add _gen.py CLI skeleton"`.

---

## Task 3 — TypedDict → JSON Schema extractor (TDD core)

**Files:**
- Modify: `src/amplifier_agent_lib/protocol/_gen.py`
- Modify: `tests/test_protocol_gen.py`

**Goal:** Add a pure function `typed_dict_to_schema(td: type) -> dict` that returns a Draft 2020-12 JSON Schema object for a single TypedDict class. This is the core translation logic — every other piece of the generator calls into it.

**Behavior required:**
- Reads field names + types via `typing.get_type_hints(td, include_extras=True)`.
- Distinguishes `Required` vs `NotRequired` fields. The TypedDicts in this codebase use `NotRequired[...]` annotations from `typing`; everything else is required (unless the class uses `total=False`, e.g. `ClientCapabilities`).
- Maps Python types to JSON Schema:
  - `str` → `{"type": "string"}`
  - `int` → `{"type": "integer"}`
  - `float` → `{"type": "number"}`
  - `bool` → `{"type": "boolean"}`
  - `None`/`NoneType` → `{"type": "null"}`
  - `list[T]` → `{"type": "array", "items": <schema(T)>}`
  - `dict[str, T]` → `{"type": "object", "additionalProperties": <schema(T)>}`
  - `Any` / `object` → `{}` (permissive)
  - `Union[A, B]` / `A | B` → `{"anyOf": [<schema(A)>, <schema(B)>]}`
  - Nested `TypedDict` → `{"$ref": "<ClassName>.schema.json"}`
- Top-level shape: `{"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "<ClassName>", "type": "object", "properties": {...}, "required": [...], "additionalProperties": False}`.
- Field docstrings from the TypedDict's `__doc__` go into the top-level `"description"`. Individual field descriptions are not extractable from TypedDicts without AST parsing — skip them.

**Pattern reference:** read `src/amplifier_agent_lib/protocol/methods.py:41-58` (`InitializeParams` + `InitializeResult`) — these are the canonical exemplars with mixed Required / NotRequired / nested TypedDict references.

**Steps:**
1. **Write failing test.** Append to `tests/test_protocol_gen.py`:

```python
def test_typed_dict_to_schema_initialize_params() -> None:
    """Converts InitializeParams to a Draft 2020-12 JSON Schema."""
    from amplifier_agent_lib.protocol._gen import typed_dict_to_schema
    from amplifier_agent_lib.protocol.methods import InitializeParams

    schema = typed_dict_to_schema(InitializeParams)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "InitializeParams"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False

    # Required fields per methods.py:41-50
    assert set(schema["required"]) == {"protocolVersion", "clientInfo", "capabilities"}

    # NotRequired fields appear in properties but NOT in required
    props = schema["properties"]
    for opt_field in ("sessionId", "resume", "providerOverride", "cwd"):
        assert opt_field in props, f"{opt_field} missing from properties"

    # Scalar type mapping
    assert props["protocolVersion"] == {"type": "string"}
    assert props["resume"] == {"type": "boolean"}

    # Nested TypedDict reference
    assert props["clientInfo"] == {"$ref": "ClientInfo.schema.json"}


def test_typed_dict_to_schema_turn_submit_result_handles_optional_union() -> None:
    """``reply: str | None`` should become an anyOf union."""
    from amplifier_agent_lib.protocol._gen import typed_dict_to_schema
    from amplifier_agent_lib.protocol.methods import TurnSubmitResult

    schema = typed_dict_to_schema(TurnSubmitResult)
    reply_schema = schema["properties"]["reply"]
    assert "anyOf" in reply_schema
    types = {sub.get("type") for sub in reply_schema["anyOf"]}
    assert types == {"string", "null"}
```

2. **Verify failure:** `uv run pytest tests/test_protocol_gen.py -v` → both new tests fail with `ImportError` on `typed_dict_to_schema`.

3. **Implement.** Add to `_gen.py`:

```python
# Add to imports at top of _gen.py:
from typing import Any, Union, get_args, get_origin, get_type_hints
from typing import NotRequired, Required  # noqa: I001  (kept explicit)
import types as _types


# Add the extractor:

_SCALAR_MAP: dict[type, dict] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


def _annotation_to_schema(annotation: Any) -> dict:
    """Translate a Python type annotation to a JSON Schema fragment."""
    # NoneType
    if annotation is type(None):
        return {"type": "null"}

    # Bare permissive types
    if annotation is Any or annotation is object:
        return {}

    # Plain scalar
    if annotation in _SCALAR_MAP:
        return _SCALAR_MAP[annotation]

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Union types: typing.Union[...] and X | Y (types.UnionType)
    if origin is Union or isinstance(annotation, _types.UnionType):
        return {"anyOf": [_annotation_to_schema(a) for a in args]}

    # list[T]
    if origin in (list, tuple) and args:
        return {"type": "array", "items": _annotation_to_schema(args[0])}

    # dict[K, V] — JSON keys are always strings; V drives additionalProperties
    if origin is dict and len(args) == 2:
        return {"type": "object", "additionalProperties": _annotation_to_schema(args[1])}

    # Nested TypedDict — emit a $ref to a sibling schema file
    if hasattr(annotation, "__total__") or hasattr(annotation, "__required_keys__"):
        return {"$ref": f"{annotation.__name__}.schema.json"}

    # Fallback: permissive
    return {}


def typed_dict_to_schema(td: type) -> dict:
    """Translate a TypedDict class to a Draft 2020-12 JSON Schema object.

    Honours ``Required`` / ``NotRequired`` and ``total=False``.  Nested
    TypedDicts are emitted as ``$ref`` to a sibling ``<Name>.schema.json``
    file; cycle detection is intentionally not done — the wire types have
    no cycles by construction.
    """
    hints = get_type_hints(td, include_extras=True)
    required_keys = set(getattr(td, "__required_keys__", set()))

    properties: dict[str, dict] = {}
    for field_name, annotation in hints.items():
        # Strip Required[...] / NotRequired[...] wrapper
        if get_origin(annotation) in (Required, NotRequired):
            annotation = get_args(annotation)[0]
        properties[field_name] = _annotation_to_schema(annotation)

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": td.__name__,
        "type": "object",
        "properties": properties,
        "required": sorted(required_keys),
        "additionalProperties": False,
    }
    if td.__doc__:
        schema["description"] = td.__doc__.strip().splitlines()[0]
    return schema
```

4. **Verify passing:** `uv run pytest tests/test_protocol_gen.py -v` — all three tests green.

5. **Commit:** `git add -u && git commit -m "feat(protocol): TypedDict to JSON Schema extractor"`.

---

## Task 4 — Wire up all 4 protocol modules → `schemas/*.schema.json`

**Files:**
- Modify: `src/amplifier_agent_lib/protocol/_gen.py`
- Create: `src/amplifier_agent_lib/protocol/schemas/__init__.py` (empty marker so the package is discoverable)
- Modify: `tests/test_protocol_gen.py`

**Goal:** Iterate over every TypedDict in `methods.py`, `notifications.py`, and `capabilities.py`, plus every value in `errors.ErrorCode`, and write one JSON file per TypedDict into `<output_dir>/schemas/`. Also emit one consolidated `error_codes.schema.json` (a JSON Schema whose `enum` is the StrEnum values).

**Pattern:** use `inspect.getmembers(module, predicate=...)` to discover TypedDict subclasses. A TypedDict has `__required_keys__` and `__optional_keys__` attributes — check for those rather than relying on `issubclass(..., TypedDict)` which doesn't work reliably.

**Steps:**
1. **Write failing test.** Append to `tests/test_protocol_gen.py`:

```python
def test_gen_emits_schema_for_every_typeddict(tmp_path: Path) -> None:
    """All TypedDicts across the four protocol modules become schema files."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    schemas_dir = tmp_path / "schemas"
    # Spot-check: known TypedDicts from each module must have schema files
    expected = {
        "InitializeParams.schema.json",
        "InitializeResult.schema.json",
        "TurnSubmitParams.schema.json",
        "TurnSubmitResult.schema.json",
        "ResultDeltaNotification.schema.json",
        "ResultFinalNotification.schema.json",
        "ApprovalRequestNotification.schema.json",
        "ClientCapabilities.schema.json",
        "ServerCapabilities.schema.json",
        "error_codes.schema.json",
    }
    actual = {p.name for p in schemas_dir.iterdir()}
    missing = expected - actual
    assert not missing, f"missing schema files: {missing}\nfound: {sorted(actual)}"


def test_gen_error_codes_schema_is_string_enum(tmp_path: Path) -> None:
    """error_codes.schema.json enumerates the ErrorCode StrEnum values."""
    import json

    from amplifier_agent_lib.protocol._gen import main
    from amplifier_agent_lib.protocol.errors import ErrorCode

    runner = CliRunner()
    runner.invoke(main, ["--output-dir", str(tmp_path)])

    schema = json.loads((tmp_path / "schemas" / "error_codes.schema.json").read_text())
    assert schema["type"] == "string"
    assert set(schema["enum"]) == {ec.value for ec in ErrorCode}
```

2. **Verify failure:** `uv run pytest tests/test_protocol_gen.py -v`.

3. **Implement.** Add to `_gen.py`:

```python
# Add imports
import inspect
import json
import importlib

from amplifier_agent_lib.protocol.errors import ErrorCode

_PROTOCOL_MODULES: tuple[str, ...] = (
    "amplifier_agent_lib.protocol.methods",
    "amplifier_agent_lib.protocol.notifications",
    "amplifier_agent_lib.protocol.capabilities",
)


def _is_typed_dict(obj: object) -> bool:
    """Heuristic: TypedDicts expose __required_keys__ AND __optional_keys__."""
    return (
        inspect.isclass(obj)
        and hasattr(obj, "__required_keys__")
        and hasattr(obj, "__optional_keys__")
    )


def _discover_typed_dicts() -> list[type]:
    """Return every TypedDict defined in the protocol modules, in import order."""
    found: list[type] = []
    seen: set[str] = set()
    for mod_name in _PROTOCOL_MODULES:
        mod = importlib.import_module(mod_name)
        for _, obj in inspect.getmembers(mod, _is_typed_dict):
            # Only emit if defined in one of our modules (skip re-exports)
            if obj.__module__ in _PROTOCOL_MODULES and obj.__name__ not in seen:
                found.append(obj)
                seen.add(obj.__name__)
    return found


def _write_error_codes_schema(schemas_dir: Path) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ErrorCode",
        "description": "Wire-level error codes for the JSON-RPC error.data.code field.",
        "type": "string",
        "enum": sorted(ec.value for ec in ErrorCode),
    }
    (schemas_dir / "error_codes.schema.json").write_text(json.dumps(schema, indent=2) + "\n")


# Update main() to actually emit files:

@click.command()
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
def main(output_dir: Path) -> None:
    """Generate spec.md and JSON Schemas from this package's TypedDicts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir = output_dir / "schemas"
    schemas_dir.mkdir(exist_ok=True)

    typed_dicts = _discover_typed_dicts()
    for td in typed_dicts:
        schema = typed_dict_to_schema(td)
        path = schemas_dir / f"{td.__name__}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n")

    _write_error_codes_schema(schemas_dir)

    click.echo(f"[gen] wrote {len(typed_dicts)} schemas + error_codes.schema.json to {schemas_dir}")
```

4. Create empty marker: `mkdir -p src/amplifier_agent_lib/protocol/schemas && touch src/amplifier_agent_lib/protocol/schemas/__init__.py`.

5. **Verify passing:** `uv run pytest tests/test_protocol_gen.py -v`.

6. **Generate the artifacts and commit them:** `uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol`.

7. **Inspect.** Run `ls src/amplifier_agent_lib/protocol/schemas/` — you should see ~17 files. Spot-check one: `cat src/amplifier_agent_lib/protocol/schemas/TurnSubmitParams.schema.json`.

8. **Commit (one logical task = one commit):** `git add -A && git commit -m "feat(protocol): emit JSON Schemas for all wire TypedDicts"`.

---

## Task 5 — Markdown spec generator

**Files:**
- Modify: `src/amplifier_agent_lib/protocol/_gen.py`
- Modify: `tests/test_protocol_gen.py`

**Goal:** Emit a single `spec.md` reference document that a human (and a future TS/Py wrapper author) can read top-to-bottom. Sections:

1. **Header banner** — "GENERATED FILE — DO NOT HAND-EDIT. Regenerate with `uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol`."
2. **Protocol version** — `PROTOCOL_VERSION` from `methods.py`.
3. **Methods** — one section per request/response pair grouped by RPC name (initialize, turn/submit, session/create, session/end, agent/shutdown, cache/info). For each: param TypedDict name, result TypedDict name, link to each `.schema.json`.
4. **Notifications** — listing of `CANONICAL_DISPLAY_EVENTS` plus one row per notification TypedDict.
5. **Errors** — table of every `ErrorCode` enum member with its wire value.
6. **Capabilities** — table summarising client and server capability shapes.

Each section links to the relevant `schemas/<Name>.schema.json` for the authoritative shape.

**Steps:**
1. **Write failing test.** Append:

```python
def test_gen_emits_spec_md_with_required_sections(tmp_path: Path) -> None:
    """spec.md contains all required top-level sections and the DO NOT EDIT banner."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    runner.invoke(main, ["--output-dir", str(tmp_path)])

    spec = (tmp_path / "spec.md").read_text()
    assert "DO NOT HAND-EDIT" in spec
    assert "2026-05-aaa-v0" in spec, "PROTOCOL_VERSION must appear"
    for required_section in ("## Methods", "## Notifications", "## Errors", "## Capabilities"):
        assert required_section in spec, f"missing section: {required_section}"
    # Schema links must point at the schemas/ subdir
    assert "schemas/InitializeParams.schema.json" in spec
    assert "schemas/error_codes.schema.json" in spec
```

2. **Verify failure:** `uv run pytest tests/test_protocol_gen.py -v`.

3. **Implement.** Add to `_gen.py`:

```python
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION
from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS


_RPC_GROUPS: tuple[tuple[str, str, str], ...] = (
    # (rpc_name, ParamsType, ResultType)
    ("initialize", "InitializeParams", "InitializeResult"),
    ("turn/submit", "TurnSubmitParams", "TurnSubmitResult"),
    ("session/create", "SessionCreateParams", "SessionCreateResult"),
    ("session/end", "SessionEndParams", "SessionEndResult"),
    ("agent/shutdown", "AgentShutdownParams", "AgentShutdownResult"),
    ("cache/info", "CacheInfoParams", "CacheInfoResult"),
)


def _render_spec_md(typed_dicts: list[type]) -> str:
    td_by_name = {td.__name__: td for td in typed_dicts}
    notification_tds = [td for td in typed_dicts if td.__name__.endswith("Notification")]
    capability_tds = [td for td in typed_dicts if td.__module__.endswith(".capabilities")]

    lines: list[str] = []
    lines.append("<!-- GENERATED FILE — DO NOT HAND-EDIT.")
    lines.append("     Regenerate with:")
    lines.append("       uv run python -m amplifier_agent_lib.protocol._gen \\")
    lines.append("           --output-dir src/amplifier_agent_lib/protocol")
    lines.append("-->")
    lines.append("")
    lines.append("# Amplifier Agent — Wire Spec")
    lines.append("")
    lines.append(f"**Protocol version:** `{PROTOCOL_VERSION}`")
    lines.append("")
    lines.append("**Framing:** JSON-RPC 2.0 over NDJSON over stdio. ")
    lines.append("Stdout carries frames only; stderr is free-form log output.")
    lines.append("")

    # Methods
    lines.append("## Methods")
    lines.append("")
    lines.append("| RPC | Params | Result |")
    lines.append("|---|---|---|")
    for rpc, params, result in _RPC_GROUPS:
        params_link = f"[`{params}`](schemas/{params}.schema.json)" if params in td_by_name else f"`{params}`"
        result_link = f"[`{result}`](schemas/{result}.schema.json)" if result in td_by_name else f"`{result}`"
        lines.append(f"| `{rpc}` | {params_link} | {result_link} |")
    lines.append("")

    # Notifications
    lines.append("## Notifications")
    lines.append("")
    lines.append("Canonical display event taxonomy (engine → client):")
    lines.append("")
    for event_name in CANONICAL_DISPLAY_EVENTS:
        lines.append(f"- `{event_name}`")
    lines.append("")
    lines.append("Notification payload schemas:")
    lines.append("")
    lines.append("| TypedDict | Schema |")
    lines.append("|---|---|")
    for td in notification_tds:
        lines.append(f"| `{td.__name__}` | [`schemas/{td.__name__}.schema.json`](schemas/{td.__name__}.schema.json) |")
    lines.append("")

    # Errors
    lines.append("## Errors")
    lines.append("")
    lines.append(f"See [`schemas/error_codes.schema.json`](schemas/error_codes.schema.json) for the authoritative enum.")
    lines.append("")
    lines.append("| Code | Wire value |")
    lines.append("|---|---|")
    for ec in sorted(ErrorCode, key=lambda e: e.value):
        lines.append(f"| `{ec.name}` | `{ec.value}` |")
    lines.append("")

    # Capabilities
    lines.append("## Capabilities")
    lines.append("")
    lines.append("| TypedDict | Schema |")
    lines.append("|---|---|")
    for td in capability_tds:
        lines.append(f"| `{td.__name__}` | [`schemas/{td.__name__}.schema.json`](schemas/{td.__name__}.schema.json) |")
    lines.append("")

    return "\n".join(lines) + "\n"


# Update main() body to also write spec.md:
# After the schema loop and _write_error_codes_schema call, add:
#     (output_dir / "spec.md").write_text(_render_spec_md(typed_dicts))
#     click.echo(f"[gen] wrote spec.md to {output_dir}")
```

4. **Verify passing:** `uv run pytest tests/test_protocol_gen.py -v`.

5. **Regenerate artifacts:** `uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol`.

6. **Spot-check:** `head -50 src/amplifier_agent_lib/protocol/spec.md` — read it as a human would.

7. **Commit:** `git add -A && git commit -m "feat(protocol): generate spec.md with linked schema references"`.

---

### 🔎 Quality checkpoint A

Run before continuing:
```
python_check tool on:
  src/amplifier_agent_lib/protocol/_gen.py
  tests/test_protocol_gen.py
```
Fix any lint/type errors. **Do not** commit `python_check`'s `fix: true` changes blindly — review each diff. Then run `uv run pytest tests/test_protocol_gen.py -v` again. If green, fold any fixes into the most recent commit with `git commit --amend --no-edit`.

---

## Task 6 — CI staleness regression test + regen command

**Files:**
- Create: `tests/test_protocol_gen_staleness.py`

**Goal:** A CI-runnable test that regenerates the artifacts into a `tmp_path`, then byte-compares each generated file against the checked-in copy under `src/amplifier_agent_lib/protocol/`. If anything drifts, the test fails with a message telling the developer the exact command to run.

This is the single mechanism that enforces D1's "DO NOT HAND-EDIT" promise.

**Steps:**
1. **Write the test (which is also the implementation — there's no production code in this task):**

```python
# tests/test_protocol_gen_staleness.py
"""CI gate: checked-in spec.md + schemas/ must match what _gen.py emits.

Per design §8 D1, the Python TypedDicts are the source of truth.  PRs that
edit the generated artifacts without re-running the generator are blocked
by this test.

Regenerate via:
    uv run python -m amplifier_agent_lib.protocol._gen \\
        --output-dir src/amplifier_agent_lib/protocol
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

_REGEN_CMD = (
    "uv run python -m amplifier_agent_lib.protocol._gen "
    "--output-dir src/amplifier_agent_lib/protocol"
)

_PROTOCOL_DIR = Path(__file__).resolve().parent.parent / "src" / "amplifier_agent_lib" / "protocol"


def _generate_to(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_spec_md_is_up_to_date(tmp_path: Path) -> None:
    """The checked-in spec.md matches generator output byte-for-byte."""
    _generate_to(tmp_path)
    actual = (_PROTOCOL_DIR / "spec.md").read_text()
    expected = (tmp_path / "spec.md").read_text()
    assert actual == expected, (
        "spec.md is stale. Regenerate with:\n  " + _REGEN_CMD
    )


@pytest.mark.parametrize(
    "schema_name",
    sorted(p.name for p in (_PROTOCOL_DIR / "schemas").iterdir() if p.suffix == ".json"),
)
def test_schema_is_up_to_date(tmp_path: Path, schema_name: str) -> None:
    """Each checked-in schemas/*.schema.json matches generator output."""
    _generate_to(tmp_path)
    actual = (_PROTOCOL_DIR / "schemas" / schema_name).read_text()
    expected = (tmp_path / "schemas" / schema_name).read_text()
    assert actual == expected, (
        f"{schema_name} is stale. Regenerate with:\n  " + _REGEN_CMD
    )


def test_no_extra_schemas_checked_in(tmp_path: Path) -> None:
    """Checked-in schemas/ directory contains no orphans."""
    _generate_to(tmp_path)
    actual = {p.name for p in (_PROTOCOL_DIR / "schemas").iterdir() if p.suffix == ".json"}
    expected = {p.name for p in (tmp_path / "schemas").iterdir() if p.suffix == ".json"}
    extras = actual - expected
    assert not extras, (
        f"Extra schema files checked in: {extras}. Delete them or regenerate."
    )
```

2. **Verify it passes immediately** (because Task 5 just regenerated everything): `uv run pytest tests/test_protocol_gen_staleness.py -v`. If any test fails, the most likely cause is that Task 5's `uv run python -m amplifier_agent_lib.protocol._gen ...` step was skipped — go back and run it, then re-run this test.

3. **Sanity test the failure path** (don't commit this): temporarily edit `src/amplifier_agent_lib/protocol/spec.md` (add a stray character), re-run the staleness test, confirm it fails with the regen-command hint, then `git checkout -- src/amplifier_agent_lib/protocol/spec.md`.

4. **Commit:** `git add tests/test_protocol_gen_staleness.py && git commit -m "test(protocol): CI staleness gate for generated spec.md + schemas"`.

---

## Task 7 — Fixture loader + structural validator

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/__init__.py`
- Create: `src/amplifier_agent_lib/protocol/conformance/loader.py`
- Create: `tests/test_protocol_conformance_fixtures.py`
- Modify: `pyproject.toml` (add `pyyaml` to dependencies)

**Goal:** Define the YAML fixture shape and provide a single function `load_fixture(path) -> Fixture` that parses, structurally validates, and returns the fixture as a typed Python object. Every fixture must have exactly three top-level keys: `setup`, `script`, `assertions`. The loader fails loudly on anything malformed; future TS/Py wrappers consume the SAME fixture files via their own loaders.

**Fixture canonical shape (write this into the loader docstring):**

```yaml
# <fixture-name>.yaml — one scenario per file
name: <kebab-case-scenario-name>
description: One-sentence summary of the contract under test.

setup:
  # Required fields the harness must honour before the script begins.
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities: { ... }  # subset of capabilities/ClientCapabilities shape
  serverCapabilities: { ... }  # optional override; defaults to server_default_capabilities()

script:
  # Ordered list of wire frames. Each frame is one of:
  #   {direction: client_to_server, method: "<rpc>", params: {...}, id: <int>}
  #   {direction: server_to_client, result: {...}, id: <int>}     # response to a prior id
  #   {direction: server_to_client, method: "<notif>", params: {...}}  # notification
  #   {direction: server_to_client, error: {...}, id: <int>}      # error response
  - direction: client_to_server
    method: initialize
    id: 1
    params: { ... }

assertions:
  # List of post-script invariants the harness checks.
  # Each is {kind: "<one-of>", ...kind-specific-fields}.
  - kind: response_matches
    id: 1
    result: { ... }
  - kind: notification_emitted
    method: result/final
    payload_contains: { synthesized: true }
  - kind: no_notification
    method: tool/started
```

The loader does not execute scripts — it only validates structure. Execution lives in Plan 3 (wrapper harnesses).

**Steps:**
1. **Add the dependency.** Edit `pyproject.toml` and append `'pyyaml>=6.0'` to the `dependencies` list (currently `['click>=8.1', 'amplifier-foundation']`). Then run `uv sync`.

2. **Write failing tests:**

```python
# tests/test_protocol_conformance_fixtures.py
"""Tests for the YAML wire-sequence fixture loader."""

from __future__ import annotations

from pathlib import Path

import pytest

_VALID_FIXTURE = """\
name: smoke
description: Loader smoke test fixture.
setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities: {}
script:
  - direction: client_to_server
    method: initialize
    id: 1
    params: {}
assertions:
  - kind: response_matches
    id: 1
    result: {}
"""


def test_load_fixture_accepts_valid_shape(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol.conformance.loader import load_fixture

    p = tmp_path / "smoke.yaml"
    p.write_text(_VALID_FIXTURE)
    fixture = load_fixture(p)

    assert fixture.name == "smoke"
    assert fixture.setup["protocolVersion"] == "2026-05-aaa-v0"
    assert len(fixture.script) == 1
    assert fixture.script[0]["method"] == "initialize"
    assert fixture.assertions[0]["kind"] == "response_matches"


@pytest.mark.parametrize(
    "missing_key",
    ["name", "setup", "script", "assertions"],
)
def test_load_fixture_rejects_missing_top_level_key(tmp_path: Path, missing_key: str) -> None:
    import yaml

    from amplifier_agent_lib.protocol.conformance.loader import (
        FixtureValidationError,
        load_fixture,
    )

    data = yaml.safe_load(_VALID_FIXTURE)
    data.pop(missing_key)
    p = tmp_path / "broken.yaml"
    p.write_text(__import__("yaml").safe_dump(data))

    with pytest.raises(FixtureValidationError, match=missing_key):
        load_fixture(p)


def test_load_fixture_rejects_unknown_assertion_kind(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol.conformance.loader import (
        FixtureValidationError,
        load_fixture,
    )

    bad = _VALID_FIXTURE.replace("kind: response_matches", "kind: bogus_kind")
    p = tmp_path / "broken.yaml"
    p.write_text(bad)
    with pytest.raises(FixtureValidationError, match="bogus_kind"):
        load_fixture(p)
```

3. **Verify failure:** `uv run pytest tests/test_protocol_conformance_fixtures.py -v`.

4. **Implement the loader:**

```python
# src/amplifier_agent_lib/protocol/conformance/__init__.py
"""Cross-language wire conformance — shared YAML fixtures + loader.

Per design §4.6 / §8 D7.  The TS and Py wrapper conformance harnesses
(authored in a later plan) consume the SAME ``fixtures/*.yaml`` files
through language-specific loaders.  This module's ``loader`` is the
Python-side reference implementation and structural validator.
"""
```

```python
# src/amplifier_agent_lib/protocol/conformance/loader.py
"""YAML wire-sequence fixture loader and structural validator.

See ``loader.load_fixture`` for the canonical fixture shape.  Loaders in
other languages (TS, future Go) MUST agree on this shape — see the
conformance fixtures themselves as the authoritative examples.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = ("name", "setup", "script", "assertions")
_VALID_DIRECTIONS: frozenset[str] = frozenset({"client_to_server", "server_to_client"})
_VALID_ASSERTION_KINDS: frozenset[str] = frozenset({
    "response_matches",
    "error_returned",
    "notification_emitted",
    "no_notification",
    "notification_order",
    "session_state",
})


class FixtureValidationError(ValueError):
    """Raised when a fixture file violates the canonical shape."""


@dataclass(frozen=True)
class Fixture:
    """A loaded, structurally-validated wire-sequence fixture."""

    name: str
    description: str
    setup: dict[str, Any]
    script: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    source_path: Path


def load_fixture(path: Path | str) -> Fixture:
    """Load and structurally validate a wire-sequence YAML fixture.

    Performs SHAPE validation only — does NOT execute the script or
    verify JSON-Schema conformance of any payload.  Wrapper harnesses
    (Plan 3) execute scripts and check assertions.

    Raises:
        FixtureValidationError: if any structural rule is violated.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise FixtureValidationError(f"{path}: top-level must be a mapping")

    missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in raw]
    if missing:
        raise FixtureValidationError(f"{path}: missing top-level keys: {missing}")

    if not isinstance(raw["script"], list) or not raw["script"]:
        raise FixtureValidationError(f"{path}: script must be a non-empty list")
    for i, frame in enumerate(raw["script"]):
        if not isinstance(frame, dict) or "direction" not in frame:
            raise FixtureValidationError(f"{path}: script[{i}] missing 'direction'")
        if frame["direction"] not in _VALID_DIRECTIONS:
            raise FixtureValidationError(
                f"{path}: script[{i}] direction {frame['direction']!r} not in {sorted(_VALID_DIRECTIONS)}"
            )

    if not isinstance(raw["assertions"], list) or not raw["assertions"]:
        raise FixtureValidationError(f"{path}: assertions must be a non-empty list")
    for i, assertion in enumerate(raw["assertions"]):
        if not isinstance(assertion, dict) or "kind" not in assertion:
            raise FixtureValidationError(f"{path}: assertions[{i}] missing 'kind'")
        kind = assertion["kind"]
        if kind not in _VALID_ASSERTION_KINDS:
            raise FixtureValidationError(
                f"{path}: assertions[{i}] kind {kind!r} not in {sorted(_VALID_ASSERTION_KINDS)}"
            )

    return Fixture(
        name=raw["name"],
        description=raw.get("description", ""),
        setup=raw["setup"],
        script=raw["script"],
        assertions=raw["assertions"],
        source_path=path,
    )
```

5. **Verify:** `uv run pytest tests/test_protocol_conformance_fixtures.py -v`.

6. **Commit:** `git add -A && git commit -m "feat(protocol): YAML fixture loader and structural validator"`.

---

## Task 8 — Fixtures (1/2): L14 synthesis + capability negotiation

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/l14_synthesis.yaml`
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/capability_negotiation.yaml`
- Modify: `tests/test_protocol_conformance_fixtures.py`

**Goal:** Author the first two of the five D7 contracts as YAML fixtures.

**L14 synthesis** — design §4.6 contract #1. Two scenarios in one file (use a list under `scenarios:` OR — for our flat shape — author one fixture per branch with names `l14_synthesis_engine_emits` and `l14_synthesis_wrapper_synthesizes`). For simplicity, **one fixture, one scenario**: combine both branches into a single ordered script if it expresses the contract clearly, otherwise pick the "wrapper synthesizes" branch (the one most easily regressed). Document the omitted branch in `description:`.

**Capability negotiation** — design §4.6 contract is "engine respects client's advertised capability subset and emits ONLY events in the negotiated intersection." Script: client sends `initialize` with `display.events = ["result/final"]` only. Engine emits `result/final` (allowed) but a hypothetical `tool/started` would be suppressed. Assertion uses `notification_emitted` for the allowed one and `no_notification` for the disallowed one.

**Steps:**
1. **Write the fixtures.**

`src/amplifier_agent_lib/protocol/conformance/fixtures/l14_synthesis.yaml`:

```yaml
name: l14_synthesis_wrapper_synthesizes
description: >
  When engine returns a non-null reply but emits no result/final notification
  before the turn/submit response, the wrapper MUST synthesise a result/final
  with synthesized=true before closing the iterator (design §4.6 contract #1,
  L14 branch B).  Branch A (engine emits result/final) is exercised by every
  other fixture and is therefore not duplicated here.

setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities:
    display:
      events: [result/final]

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "2026-05-aaa-v0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
      sessionId: sess-l14-b

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display:
          events: [result/final]
      serverInfo: {name: amplifier-agent, version: "0.0.0"}
      sessionState: {sessionId: sess-l14-b, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-l14-b
      turnId: turn-1
      prompt: "say hello"

  # Engine returns turn/submit response WITHOUT first emitting result/final.
  - direction: server_to_client
    id: 2
    result:
      reply: "hello there"
      turnId: turn-1
      sessionId: sess-l14-b

assertions:
  # Wrapper-side: a synthesised result/final must surface to the consumer
  # before the async iterator ends.
  - kind: notification_emitted
    method: result/final
    payload_contains:
      synthesized: true
      turnId: turn-1
      text: "hello there"

  # No result/final must have come from the engine in this branch.
  - kind: no_notification
    method: result/final
    source: engine
```

`src/amplifier_agent_lib/protocol/conformance/fixtures/capability_negotiation.yaml`:

```yaml
name: capability_negotiation_intersection_enforced
description: >
  Engine emits ONLY notifications whose method is in the negotiated intersection
  of client and server capabilities (design §8 D7 contract #2).  Client
  advertises display.events = [result/final] only.  Engine must NOT emit
  tool/started even if internal hook would.

setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities:
    display:
      events: [result/final]

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "2026-05-aaa-v0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
      sessionId: sess-cap-1

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display:
          events: [result/final]
      serverInfo: {name: amplifier-agent, version: "0.0.0"}
      sessionState: {sessionId: sess-cap-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-cap-1
      turnId: turn-1
      prompt: "run a tool"

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-cap-1
      turnId: turn-1
      text: "done"

  - direction: server_to_client
    id: 2
    result:
      reply: "done"
      turnId: turn-1
      sessionId: sess-cap-1

assertions:
  - kind: notification_emitted
    method: result/final
  - kind: no_notification
    method: tool/started
  - kind: no_notification
    method: tool/completed
```

2. **Append a test** that loads each new fixture via the loader and confirms structural validity:

```python
@pytest.mark.parametrize(
    "fixture_name",
    ["l14_synthesis", "capability_negotiation"],
)
def test_authored_fixtures_load(fixture_name: str) -> None:
    from amplifier_agent_lib.protocol.conformance.loader import load_fixture

    base = Path(__file__).resolve().parent.parent / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
    fixture = load_fixture(base / f"{fixture_name}.yaml")
    assert fixture.name
    assert fixture.script
    assert fixture.assertions
```

3. **Verify:** `uv run pytest tests/test_protocol_conformance_fixtures.py -v`.

4. **Commit:** `git add -A && git commit -m "feat(protocol): L14 + capability-negotiation conformance fixtures"`.

---

## Task 9 — Fixtures (2/2): lineage + version-skew + resume + smoke-load-all

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/subagent_lineage.yaml`
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/version_skew.yaml`
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/resume_continuity.yaml`
- Modify: `tests/test_protocol_conformance_fixtures.py`

**Goal:** Author the remaining three D7 contracts and add a smoke test that loads **every** fixture file under `conformance/fixtures/` in one shot.

**subagent_lineage** — design §4.4 says sub-agent events carry `parentTurnId`. Script: parent turn submits, engine emits notifications for a hypothetical child session including `parentTurnId` in the payload. The TypedDicts in `notifications.py` do not yet have first-class `subagent/*` shapes — fixture payloads use `progress` or `result/delta` with an extra `parentTurnId` key to convey lineage. Document this caveat in the description.

**version_skew** — design §4.6 contract #4. Two scenarios:
- Strict-refuse default: client advertises `protocolVersion: "2099-12-future-vN"`; engine returns error with `code: protocol_version_mismatch` and a `remediation` string.
- Override-allowed: same client version, but `setup.allowProtocolSkew: true`; engine returns success.

Put both branches in one fixture, sequenced. Use the `error_returned` assertion kind for the strict branch and `response_matches` for the override branch.

**resume_continuity** — design §5.3. Two turns: turn 1 establishes session, turn 2 sent with `resume: true` references turn-1's context. Assertion: `session_state` shows `resumed: true` after turn 2's initialize.

**Steps:**
1. **Author the three YAML files.** Use the same shape as Task 8. Keep them ≤ 70 lines each. Below are skeletons; flesh them out following the precedents:

```yaml
# subagent_lineage.yaml
name: subagent_lineage_propagates_parent_turn_id
description: >
  Sub-agent notifications carry parentTurnId so hosts can correlate them with
  the originating turn (design §4.4).  Until a dedicated subagent/* event type
  is added to notifications.py, lineage is conveyed via an extra parentTurnId
  field on existing notification payloads.

setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities:
    display:
      events: [result/delta, result/final, progress]

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "2026-05-aaa-v0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display: {events: [result/delta, result/final, progress]}
      sessionId: sess-sub-1

  - direction: server_to_client
    id: 1
    result:
      capabilities: {display: {events: [result/delta, result/final, progress]}}
      serverInfo: {name: amplifier-agent, version: "0.0.0"}
      sessionState: {sessionId: sess-sub-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params: {sessionId: sess-sub-1, turnId: parent-turn, prompt: "spawn a sub-agent"}

  - direction: server_to_client
    method: progress
    params:
      sessionId: sess-sub-1
      turnId: parent-turn
      message: "child-agent started"
      parentTurnId: parent-turn

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-sub-1
      turnId: parent-turn
      text: "child finished"

  - direction: server_to_client
    id: 2
    result: {reply: "child finished", turnId: parent-turn, sessionId: sess-sub-1}

assertions:
  - kind: notification_emitted
    method: progress
    payload_contains: {parentTurnId: parent-turn}
  - kind: notification_emitted
    method: result/final
```

```yaml
# version_skew.yaml
name: version_skew_strict_refuse_then_override
description: >
  Default strict-refuse: client with a foreign protocolVersion gets a typed
  protocol_version_mismatch error including a remediation string.  Override
  branch: setup.allowProtocolSkew=true permits the handshake (design §8 D6).

setup:
  protocolVersion: "2099-12-future-vN"
  clientCapabilities: {display: {events: [result/final]}}
  allowProtocolSkew: false

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "2099-12-future-vN"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities: {display: {events: [result/final]}}
      sessionId: sess-skew-strict

  - direction: server_to_client
    id: 1
    error:
      code: -32000
      message: "Protocol version mismatch"
      data:
        code: protocol_version_mismatch
        clientVersion: "2099-12-future-vN"
        serverVersion: "2026-05-aaa-v0"
        remediation: "Reinstall the matching amplifier-agent and amplifier-agent-client packages, or pass --allow-protocol-skew."

assertions:
  - kind: error_returned
    id: 1
    code: protocol_version_mismatch
```

```yaml
# resume_continuity.yaml
name: resume_continuity_second_turn_sees_first
description: >
  Adapter spawns a session, runs one turn, exits.  Re-spawning with the same
  sessionId and resume=true must yield sessionState.resumed=true and allow a
  second turn that can reference first-turn context (design §5.3).

setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities: {display: {events: [result/final]}}

script:
  # --- First spawn ---
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "2026-05-aaa-v0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities: {display: {events: [result/final]}}
      sessionId: sess-resume-1
      resume: false
  - direction: server_to_client
    id: 1
    result:
      capabilities: {display: {events: [result/final]}}
      serverInfo: {name: amplifier-agent, version: "0.0.0"}
      sessionState: {sessionId: sess-resume-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params: {sessionId: sess-resume-1, turnId: turn-1, prompt: "remember the number 42"}
  - direction: server_to_client
    method: result/final
    params: {sessionId: sess-resume-1, turnId: turn-1, text: "acknowledged"}
  - direction: server_to_client
    id: 2
    result: {reply: "acknowledged", turnId: turn-1, sessionId: sess-resume-1}

  # --- Second spawn: same sessionId, resume=true ---
  - direction: client_to_server
    method: initialize
    id: 3
    params:
      protocolVersion: "2026-05-aaa-v0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities: {display: {events: [result/final]}}
      sessionId: sess-resume-1
      resume: true
  - direction: server_to_client
    id: 3
    result:
      capabilities: {display: {events: [result/final]}}
      serverInfo: {name: amplifier-agent, version: "0.0.0"}
      sessionState: {sessionId: sess-resume-1, resumed: true}

  - direction: client_to_server
    method: turn/submit
    id: 4
    params: {sessionId: sess-resume-1, turnId: turn-2, prompt: "what was the number?"}
  - direction: server_to_client
    method: result/final
    params: {sessionId: sess-resume-1, turnId: turn-2, text: "42"}
  - direction: server_to_client
    id: 4
    result: {reply: "42", turnId: turn-2, sessionId: sess-resume-1}

assertions:
  - kind: session_state
    after_id: 3
    expected: {sessionId: sess-resume-1, resumed: true}
  - kind: notification_emitted
    method: result/final
    payload_contains: {turnId: turn-2, text: "42"}
```

2. **Replace the parametrize list in the existing test** with a discovery-based smoke test that loads every fixture in the directory:

```python
# Replace the parametrized test_authored_fixtures_load with this:

def _all_fixtures() -> list[Path]:
    base = Path(__file__).resolve().parent.parent / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
    return sorted(base.glob("*.yaml"))


@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: p.name)
def test_every_fixture_loads_structurally(fixture_path: Path) -> None:
    """Every YAML file under conformance/fixtures/ parses and structure-validates."""
    from amplifier_agent_lib.protocol.conformance.loader import load_fixture

    fixture = load_fixture(fixture_path)
    assert fixture.name
    assert fixture.script
    assert fixture.assertions


def test_expected_fixture_set_is_complete() -> None:
    """Exactly the five D7 contracts must be present — no more, no fewer."""
    names = {p.stem for p in _all_fixtures()}
    expected = {
        "l14_synthesis",
        "capability_negotiation",
        "subagent_lineage",
        "version_skew",
        "resume_continuity",
    }
    assert names == expected, f"unexpected fixture set: {names ^ expected}"
```

3. **Verify:** `uv run pytest tests/test_protocol_conformance_fixtures.py -v`. All five fixtures should load. Fix any YAML syntax issues that surface.

4. **Commit:** `git add -A && git commit -m "feat(protocol): lineage, version-skew, resume-continuity fixtures + completeness gate"`.

---

### 🔎 Quality checkpoint B

Run:
```
python_check tool on:
  src/amplifier_agent_lib/protocol/_gen.py
  src/amplifier_agent_lib/protocol/conformance/loader.py
  tests/test_protocol_gen.py
  tests/test_protocol_gen_staleness.py
  tests/test_protocol_conformance_fixtures.py
```
Then `uv run pytest tests/ -v -k "protocol"` to confirm no regression. Amend the most recent commit with any fixups.

---

## Task 10 — `pyproject.toml` wheel packaging for new artifacts

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_phase_2_1_packaging.py`

**Goal:** Ensure the wheel ships `spec.md`, every `schemas/*.schema.json`, and every `conformance/fixtures/*.yaml`. The repo already uses `[tool.hatch.build.targets.wheel.force-include]` for non-Python assets (see `pyproject.toml:22-27`) — extend it.

**Pattern reference:** `pyproject.toml:22-27` — exact mechanism is `"src/<path>" = "<wheel_path>"` mappings.

**Steps:**
1. **Write the failing test** that builds a wheel into a `tmp_path` and inspects its contents:

```python
# tests/test_phase_2_1_packaging.py
"""Wheel packaging gate: spec.md, schemas, and fixtures ship in the wheel."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
def test_wheel_includes_phase_2_1_artifacts(tmp_path: Path) -> None:
    """Built wheel contains spec.md + schemas + fixtures."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_REPO_ROOT,
        check=True,
    )
    wheel = next(tmp_path.glob("amplifier_agent-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())

    # spec.md
    assert "amplifier_agent_lib/protocol/spec.md" in names

    # at least one schema
    assert any(
        n.startswith("amplifier_agent_lib/protocol/schemas/") and n.endswith(".schema.json")
        for n in names
    ), f"no schema files in wheel; wheel contents: {sorted(names)[:30]}"

    # all five fixtures
    required_fixtures = {
        f"amplifier_agent_lib/protocol/conformance/fixtures/{stem}.yaml"
        for stem in (
            "l14_synthesis",
            "capability_negotiation",
            "subagent_lineage",
            "version_skew",
            "resume_continuity",
        )
    }
    missing = required_fixtures - names
    assert not missing, f"missing fixtures in wheel: {missing}"
```

Note the `@pytest.mark.integration` marker — `pyproject.toml:57` defines `integration` as "slow end-to-end tests requiring full install." That keeps it out of the default fast pytest path while remaining runnable on demand.

2. **Verify failure:** `uv run pytest tests/test_phase_2_1_packaging.py -m integration -v`. Expect the schema/fixture assertions to fail (or, depending on hatch's default behavior, only the schemas to fail) because force-include doesn't cover the new files yet.

3. **Edit `pyproject.toml`** under `[tool.hatch.build.targets.wheel.force-include]`. Append entries for the new artifacts. The block should look like:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/amplifier_agent_lib/bundle/bundle.md" = "amplifier_agent_lib/bundle/bundle.md"
"src/amplifier_agent_lib/bundle/agents/explorer.md" = "amplifier_agent_lib/bundle/agents/explorer.md"
"src/amplifier_agent_lib/bundle/agents/planner.md" = "amplifier_agent_lib/bundle/agents/planner.md"
"src/amplifier_agent_lib/bundle/agents/coder.md" = "amplifier_agent_lib/bundle/agents/coder.md"
"src/amplifier_agent_lib/bundle/agents/tester.md" = "amplifier_agent_lib/bundle/agents/tester.md"
"src/amplifier_agent_lib/protocol/spec.md" = "amplifier_agent_lib/protocol/spec.md"
"src/amplifier_agent_lib/protocol/schemas" = "amplifier_agent_lib/protocol/schemas"
"src/amplifier_agent_lib/protocol/conformance/fixtures" = "amplifier_agent_lib/protocol/conformance/fixtures"
```

(Hatch's `force-include` accepts directory mappings.)

4. **Verify:** `uv run pytest tests/test_phase_2_1_packaging.py -m integration -v`. Should now pass.

5. **Commit:** `git add -A && git commit -m "build(protocol): include spec.md, schemas, and fixtures in wheel"`.

---

## Task 11 — Phase 2.1 exit-gate integration test

**Files:**
- Create: `tests/test_phase_2_1_exit_gate.py`

**Goal:** Single integration test that exercises the entire Phase 2.1 surface end-to-end:

1. Run the generator in-process.
2. Load the resulting `TurnSubmitParams.schema.json` from disk.
3. Build a `TurnSubmitParams`-shaped dict in Python and validate it against the schema (using `jsonschema` — add as a dev dependency).
4. Load all five conformance fixtures.
5. Assert `PROTOCOL_VERSION` is consistent between the spec.md, the methods.py constant, and the staleness assertions.

If this test passes, Phase 2.1 has shipped its contract.

**Steps:**
1. **Add `jsonschema>=4.20` to `[dependency-groups]/dev`** in `pyproject.toml`. Run `uv sync`.

2. **Write the test:**

```python
# tests/test_phase_2_1_exit_gate.py
"""Phase 2.1 exit gate — end-to-end smoke of the wire-spec hardening surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_lib.protocol._gen import main as gen_main
from amplifier_agent_lib.protocol.conformance.loader import load_fixture
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROTOCOL_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol"
_FIXTURE_DIR = _PROTOCOL_DIR / "conformance" / "fixtures"


def test_phase_2_1_exit_gate(tmp_path: Path) -> None:
    """End-to-end: generate, validate a payload, load all fixtures, version coherent."""
    pytest.importorskip("jsonschema")
    import jsonschema

    # 1. Generator runs cleanly into a clean directory
    runner = CliRunner()
    result = runner.invoke(gen_main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # 2. JSON Schema for TurnSubmitParams is well-formed Draft 2020-12
    schema_path = tmp_path / "schemas" / "TurnSubmitParams.schema.json"
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)

    # 3. A valid payload passes; a missing required field fails
    valid_payload = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "prompt": "hi",
    }
    jsonschema.validate(valid_payload, schema)

    invalid_payload = {"sessionId": "sess-1", "prompt": "hi"}  # missing turnId
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_payload, schema)

    # 4. All five conformance fixtures load
    fixture_names = sorted(p.stem for p in _FIXTURE_DIR.glob("*.yaml"))
    assert fixture_names == [
        "capability_negotiation",
        "l14_synthesis",
        "resume_continuity",
        "subagent_lineage",
        "version_skew",
    ]
    for path in _FIXTURE_DIR.glob("*.yaml"):
        fixture = load_fixture(path)
        assert fixture.setup.get("protocolVersion") in (PROTOCOL_VERSION, "2099-12-future-vN"), (
            f"{path.name}: protocolVersion in setup must be current ({PROTOCOL_VERSION}) "
            f"or the deliberate version-skew value"
        )

    # 5. Version coherence across spec.md and methods.py constant
    spec_md = (_PROTOCOL_DIR / "spec.md").read_text()
    assert PROTOCOL_VERSION in spec_md, "PROTOCOL_VERSION must appear in checked-in spec.md"
```

3. **Verify:** `uv run pytest tests/test_phase_2_1_exit_gate.py -v`.

4. **Final full-suite check:** `uv run pytest tests/ -v`. Every test must pass.

5. **Commit:** `git add -A && git commit -m "test(protocol): Phase 2.1 exit-gate integration test"`.

---

## Final checklist (before opening PR)

1. `uv run pytest tests/ -v` — full suite green.
2. `uv run pytest tests/ -v -m integration` — integration tests green.
3. `git log --oneline main..HEAD` — confirm ~11 commits, all conventional-commits style.
4. `git diff --stat main..HEAD` — sanity-check the file footprint matches "Scope boundaries" §IN. No file under `src/amplifier_agent_cli/` should be touched. No wrapper code anywhere.
5. The untracked `docs/architecture/amplifier-as-agent-presentation.html` should still be untracked — `git status` confirms it.
6. Generated artifacts (`spec.md`, every `*.schema.json`) are committed, not gitignored.
7. Open the PR against `main` with title `feat(phase-2-1): wire spec hardening — generator, schemas, fixtures`. Body references this plan file and the design doc §10.3.

When the PR lands, Phase 2.1 is done and Plan 3 (the TS + Py wrapper combined plan) becomes the next write-plan target.
