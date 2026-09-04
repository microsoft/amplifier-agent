"""Lossless conversion between contract records and private connection values."""

from __future__ import annotations

import dataclasses
import json
import types
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from .._records import AgentError


def to_data(value: Any) -> Any:
    if isinstance(value, AgentError):
        return {key: to_data(item) for key, item in vars(value).items() if item is not None}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            key: to_data(item)
            for key, item in vars(value).items()
            if item is not None and not key.startswith("_")
        }
    if isinstance(value, dict):
        return {key: to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_data(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal values must be finite")
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_data(value), allow_nan=False, separators=(",", ":"))


def loads(value: str) -> Any:
    def invalid_constant(constant: str) -> None:
        raise ValueError(f"Non-JSON numeric value: {constant}")

    return json.loads(value, parse_constant=invalid_constant)


def record(cls: type[Any], data: Any) -> Any:
    """Construct records while retaining owned extension fields for engine validation."""
    if not isinstance(data, dict):
        return data
    fields = {field.name for field in dataclasses.fields(cls)}
    hints = get_type_hints(cls)
    values = {
        name: convert(hints.get(name, Any), value) for name, value in data.items() if name in fields
    }
    instance = cls(**values)
    for name, value in data.items():
        if name not in fields:
            if "." not in name or name.startswith("_"):
                raise ValueError(f"Unknown field {name!r} on {cls.__name__}")
            object.__setattr__(instance, name, value)
    return instance


def convert(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for member in args:
            if member is not type(None):
                return convert(member, value)
    if origin is list and isinstance(value, list):
        return [convert(args[0], item) for item in value]
    if origin is dict and isinstance(value, dict):
        return {key: convert(args[1], item) for key, item in value.items()}
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return record(annotation, value)
    if annotation is Decimal:
        return Decimal(value)
    if annotation is datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value
