"""Case data for modes discovery.

Covers the ``modes list --json`` CLI command and the ``GET /v1/modes`` HTTP route,
which enumerate the shipped modes. This targets functionality that does not exist yet,
so the tests carry an ``xfail`` marker (see docs/E2E_TESTING.md).
"""

from __future__ import annotations

from framework.assertions import expect_active_mode, expect_set
from framework.harness import E2ECase, Step

MODES: list[E2ECase] = [
    E2ECase("modes-list-cli", "cli", ["modes", "list", "--json"], check=expect_set({"plan", "brainstorm"})),
    E2ECase("modes-list-http", "http", ("GET", "/v1/modes"), check=expect_set({"plan", "brainstorm"})),
]

_CONFIG = "/root/e2e/host-config.json"
_WS_MODES = "/root/e2e/ws-modes"

# Mode activation via the `--mode` flag on `run`. Neither the flag nor the envelope's
# metadata.activeMode field exists yet, so these are xfail(strict=True) in test_modes.py.
#
# The active mode is set per turn by --mode and echoed in the JSON envelope. It is NOT sticky:
# re-passing --mode on a resume keeps it active; omitting --mode disables it (there is no separate
# clear verb). ACTIVATIONS proves set -> persist-on-re-pass -> disable-on-omit deterministically.
ACTIVATIONS: list[E2ECase] = [
    E2ECase(
        "mode-brainstorm-persist-and-disable",
        "cli-multi",
        [],
        steps=(
            # 1. Set brainstorm on a fresh session.
            Step(
                [
                    "run",
                    "-y",
                    "--output",
                    "json",
                    "--config",
                    _CONFIG,
                    "--session-id",
                    "{SID}",
                    "--mode",
                    "brainstorm",
                    "Let's brainstorm ideas for X",
                ],
                check=expect_active_mode("brainstorm"),
            ),
            # 2. Resume WITH --mode re-passed -> mode stays active.
            Step(
                [
                    "run",
                    "-y",
                    "--output",
                    "json",
                    "--config",
                    _CONFIG,
                    "--session-id",
                    "{SID}",
                    "--resume",
                    "--mode",
                    "brainstorm",
                    "keep exploring",
                ],
                check=expect_active_mode("brainstorm"),
            ),
            # 3. Resume WITHOUT --mode -> mode disabled (this is the disable mechanism).
            Step(
                [
                    "run",
                    "-y",
                    "--output",
                    "json",
                    "--config",
                    _CONFIG,
                    "--session-id",
                    "{SID}",
                    "--resume",
                    "now build it",
                ],
                check=expect_active_mode(None),
            ),
        ),
    ),
]

# Custom mode auto-discovered from the launch directory (_WS_MODES/.amplifier/modes/), seeded by the
# seeded_mode fixture. Proves custom mode discovery + activation. --mode flag not built yet -> xfail.
CUSTOM: list[E2ECase] = [
    E2ECase(
        "mode-custom-launch-dir",
        "cli",
        ["run", "-y", "--output", "json", "--config", _CONFIG, "--mode", "e2e-probe-mode", "hello"],
        check=expect_active_mode("e2e-probe-mode"),
        cwd=_WS_MODES,
    ),
]
