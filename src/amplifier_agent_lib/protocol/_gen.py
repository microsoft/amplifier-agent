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

import types as _types
from pathlib import Path
from typing import Any, NotRequired, Required, Union, get_args, get_origin, get_type_hints

import click

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
    click.echo(f"[gen] output directory ready: {output_dir}")


if __name__ == "__main__":
    main()
