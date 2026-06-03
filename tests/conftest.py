"""Pytest conftest for the amplifier-agent test suite.

G3 introduced a fail-fast guard in ``_resolve_approval_mode``: a non-TTY run
with no ``-y``/``-n`` and no ``host_config.approval.mode`` now exits 2 at
startup instead of silently auto-denying every tool call. This is the right
behavior for production hosts but breaks every existing test that invokes
``amplifier-agent run`` (via ``CliRunner`` or as a real subprocess) without
explicitly opting into an approval policy.

Two autouse fixtures restore the pre-G3 testing UX without weakening the
production guard:

1. **In-process tests** (CliRunner-based) — :func:`_default_stdin_tty_true`
   patches ``is_stdin_tty`` to return ``True``. The default test environment
   behaves "as if a human is at the terminal" and the approval mode resolves
   to ``"prompt"`` (which is safe to construct because tests either mock
   ``_execute_turn`` or supply their own ``-y``/``-n``/``host_config``
   that overrides the prompt path).

2. **Subprocess tests** (real ``amplifier-agent`` subprocesses) — the
   in-process ``is_stdin_tty`` patch does NOT propagate across the fork
   boundary, so we instead seed ``$AMPLIFIER_AGENT_CONFIG`` with a temp
   file containing ``{"approval": {"mode": "yes"}}``. The engine's loader
   precedence is ``--config <path> > $AMPLIFIER_AGENT_CONFIG > none``, so
   any test that supplies its own ``--config`` flag overrides this default
   (tests with explicit host_config still test what they intend to test).

Tests that explicitly need to exercise non-TTY fail-fast behavior (the G3
fail-fast test, the host_config approval.mode tests, the prompt-required
test) all nest their own ``patch(... is_stdin_tty, return_value=False)``
context inside the test body and either delete ``$AMPLIFIER_AGENT_CONFIG``
or pass their own ``--config``. Nested patches override the autouse default.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _default_stdin_tty_true() -> Generator[None, None, None]:
    """Default test environment behaves as TTY-attached (in-process only).

    Tests that explicitly need non-TTY behavior wrap their own
    ``patch(... is_stdin_tty, return_value=False)`` around the specific
    ``runner.invoke`` call; nested patches override this default inside
    the with-block.
    """
    with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=True):
        yield


@pytest.fixture(autouse=True, scope="session")
def _default_subprocess_approval_mode(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Path, None, None]:
    """Seed ``$AMPLIFIER_AGENT_CONFIG`` for the test session.

    Subprocess tests inherit the parent's environment. By placing a config
    file at a known path and pointing ``$AMPLIFIER_AGENT_CONFIG`` at it, we
    give every subprocess test a default ``approval.mode: "yes"`` policy
    without modifying argv. Tests that pass their own ``--config <path>``
    override this (loader precedence: ``--config`` > env var > none).

    Tests that need to test the fail-fast behavior itself must explicitly
    ``monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)`` in
    their setup.
    """
    cfg_path = tmp_path_factory.mktemp("conftest-host-config") / "default.json"
    cfg_path.write_text(json.dumps({"approval": {"mode": "yes"}}), encoding="utf-8")
    prior = os.environ.get("AMPLIFIER_AGENT_CONFIG")
    os.environ["AMPLIFIER_AGENT_CONFIG"] = str(cfg_path)
    try:
        yield cfg_path
    finally:
        if prior is None:
            os.environ.pop("AMPLIFIER_AGENT_CONFIG", None)
        else:
            os.environ["AMPLIFIER_AGENT_CONFIG"] = prior
