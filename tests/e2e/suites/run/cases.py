"""Case data for the ``amplifier-agent run`` command.

Covers a single-shot reply (``run-basic-reply``) and a multi-step session-resume
flow (``run-resume-session``). Add further ``run`` usage scenarios (modes, plan, etc.)
here as additional ``E2ECase`` / ``Step`` entries.
"""

from __future__ import annotations

from framework.assertions import expect_contains
from framework.harness import E2ECase, Step

_CONFIG = "/root/e2e/host-config.json"

BASIC: list[E2ECase] = [
    E2ECase(
        "run-basic-reply",
        "cli",
        ["run", "-y", "--config", _CONFIG, "reply with a short greeting"],
        check=None,
    ),
]

RESUME: list[E2ECase] = [
    E2ECase(
        "run-resume-session",
        "cli-multi",
        [],
        steps=(
            Step(["run", "-y", "--config", _CONFIG, "--session-id", "{SID}", "Remember that I like bananas"]),
            Step(
                ["run", "-y", "--config", _CONFIG, "--session-id", "{SID}", "--resume", "What do I like?"],
                check=expect_contains("bananas"),
            ),
        ),
    ),
]
