# src/amplifier_agent_lib/protocol/_gen.py
"""Wire-spec generator.

Reads TypedDicts in this package and emits a language-neutral spec:
    <output_dir>/spec.md             — human-readable Markdown reference
    <output_dir>/schemas/*.schema.json — JSON Schema (Draft 2020-12) per TypedDict

Per design §8 D1, Python TypedDicts are the authoritative wire-spec source.
The Markdown and JSON Schema outputs are GENERATED — never hand-edit them.

Regenerate via:
    uv run python -m amplifier_agent_lib.protocol._gen \
        --output-dir src/amplifier_agent_lib/protocol
"""

from __future__ import annotations

import importlib
import inspect
import json
import types as _types
from pathlib import Path
from typing import Any, NotRequired, Required, Union, get_args, get_origin, get_type_hints

import click

from amplifier_agent_lib.protocol.errors import ErrorCode
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION
from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS

# ---------------------------------------------------------------------------
# JSON Schema type-mapping helpers
# ---------------------------------------------------------------------------

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

    # Union types: typing.Union[...] and X | Y (types.UnionType, Python 3.10+)
    if origin is Union or isinstance(annotation, _types.UnionType):
        return {"anyOf": [_annotation_to_schema(a) for a in args]}

    # list[T] or tuple[T, ...]
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

    Note: ``__required_keys__`` is unreliable when ``from __future__ import
    annotations`` is active in the TypedDict's module (annotations are stored
    as strings, preventing the TypedDict machinery from evaluating them at
    class-definition time).  We therefore derive required/optional status
    directly from the *resolved* type hints returned by ``get_type_hints``.
    """
    hints = get_type_hints(td, include_extras=True)
    # TypedDict default is total=True (all fields required unless wrapped)
    total: bool = getattr(td, "__total__", True)

    properties: dict[str, dict] = {}
    required_keys: list[str] = []

    for field_name, annotation in hints.items():
        origin = get_origin(annotation)

        if origin is NotRequired:
            # Explicitly optional — strip the wrapper and do NOT add to required
            inner = get_args(annotation)[0]
            properties[field_name] = _annotation_to_schema(inner)
        elif origin is Required:
            # Explicitly required — strip the wrapper and add to required
            inner = get_args(annotation)[0]
            properties[field_name] = _annotation_to_schema(inner)
            required_keys.append(field_name)
        else:
            # No Required/NotRequired wrapper — use the class-level total flag
            properties[field_name] = _annotation_to_schema(annotation)
            if total:
                required_keys.append(field_name)

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


# ---------------------------------------------------------------------------
# Protocol module discovery
# ---------------------------------------------------------------------------

_PROTOCOL_MODULES: tuple[str, ...] = (
    "amplifier_agent_lib.protocol.methods",
    "amplifier_agent_lib.protocol.notifications",
    "amplifier_agent_lib.protocol.capabilities",
)


def _is_typed_dict(obj: object) -> bool:
    """Heuristic: TypedDicts expose __required_keys__ AND __optional_keys__."""
    return inspect.isclass(obj) and hasattr(obj, "__required_keys__") and hasattr(obj, "__optional_keys__")


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


# ---------------------------------------------------------------------------
# spec.md renderer
# ---------------------------------------------------------------------------

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
    """Render a human-readable Markdown reference document from the protocol TypedDicts."""
    td_by_name = {td.__name__: td for td in typed_dicts}
    notification_tds = [td for td in typed_dicts if td.__name__.endswith("Notification")]
    capability_tds = [td for td in typed_dicts if td.__module__ == "amplifier_agent_lib.protocol.capabilities"]

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
    lines.append("See [`schemas/error_codes.schema.json`](schemas/error_codes.schema.json) for the authoritative enum.")
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


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

    typed_dicts = _discover_typed_dicts()
    for td in typed_dicts:
        schema = typed_dict_to_schema(td)
        path = schemas_dir / f"{td.__name__}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n")

    _write_error_codes_schema(schemas_dir)

    (output_dir / "spec.md").write_text(_render_spec_md(typed_dicts))
    click.echo(f"[gen] wrote spec.md to {output_dir}")

    click.echo(f"[gen] wrote {len(typed_dicts)} schemas + error_codes.schema.json to {schemas_dir}")


if __name__ == "__main__":
    main()
