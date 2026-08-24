"""Case data for the smoke suite: does amplifier-agent run on Windows at all.

Every case here is keyless. It proves the install landed, the entry point
resolves, and the bundle cache is readable, without spending a model call.
"""

from __future__ import annotations

from winframework.assertions import expect_names, expect_non_empty
from winframework.harness import WinCase

SMOKE: list[WinCase] = [
    # The entry point exists and the Python environment behind it imports.
    WinCase("smoke-version", command=["--version"], check=expect_non_empty()),
    # Reads the baked-in bundle cache. Catches a prime that silently failed.
    WinCase(
        "smoke-skills-list",
        command=["skills", "list", "--json"],
        check=expect_names({"code-review", "council"}),
    ),
    WinCase(
        "smoke-modes-list",
        command=["modes", "list", "--json"],
        check=expect_names({"plan", "brainstorm"}),
    ),
]
