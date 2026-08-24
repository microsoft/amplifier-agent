"""Case model and runner for the Windows e2e suite.

Mirrors the shape of the DTU harness (``tests/e2e/framework/harness.py``)
without sharing code with it. The two suites are deliberately independent:
this one is an approximation that answers "is Windows support working", not a
parity twin of the DTU contract suite.

The baseline criterion is the same: exit 0. A case with ``check=None`` asserts
only that the command ran clean.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from winframework import container


@dataclass(frozen=True)
class WinCase:
    """A single Windows e2e case.

    ``command`` is argv WITHOUT the leading ``amplifier-agent``.
    """

    name: str
    command: list[str] = field(default_factory=list)
    check: Callable[[Any], None] | None = None


def _parse(raw: str) -> Any:
    """Parse stdout as JSON, falling back to the raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def run_cli_case(name: str, case: WinCase) -> None:
    """Run a case inside the container named ``name``."""
    result = container.exec_(name, ["amplifier-agent", *case.command])
    exit_code = result["exit_code"]
    assert exit_code == 0, (
        f"[{case.name}] expected exit 0, got {exit_code}\n"
        f"command: amplifier-agent {' '.join(case.command)}\n"
        f"stdout:\n{result['stdout']}\n"
        f"stderr:\n{result['stderr']}"
    )
    if case.check is not None:
        case.check(_parse(result["stdout"]))
