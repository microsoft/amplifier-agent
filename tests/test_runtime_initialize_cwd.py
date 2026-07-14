"""``handle_initialize`` honors the host-supplied cwd (InitializeParams.cwd).

Regression guard for the fix that stopped the wire/initialize path from letting
foundation default ``session.working_dir`` to the installed bundle directory.
``create_session`` sets ``session.working_dir`` to
``session_cwd or bundle.base_path or Path.cwd()``; when the engine passed
``session_cwd=None`` the capability resolved to ``bundle.base_path`` (the
site-packages bundle dir), which is never a valid workspace.

The handler must:
  - resolve a wire-supplied ``cwd`` and pass it as ``session_cwd``
  - fall back to the engine process cwd (``Path.cwd()``) when ``cwd`` is absent,
    never letting ``session_cwd`` be ``None``
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib import _runtime


def _make_prepared() -> MagicMock:
    """A fake PreparedBundle exposing only ``create_session``."""
    prepared = MagicMock()
    prepared.create_session = AsyncMock(return_value=MagicMock())
    return prepared


@pytest.mark.asyncio
async def test_initialize_forwards_wire_cwd_as_session_cwd(monkeypatch, tmp_path) -> None:
    """A wire-supplied ``cwd`` is resolved and passed as ``session_cwd``."""
    prepared = _make_prepared()
    monkeypatch.setattr(_runtime, "load_and_prepare_cached", AsyncMock(return_value=prepared))

    await _runtime.handle_initialize({"cwd": str(tmp_path)})

    _, kwargs = prepared.create_session.call_args
    assert kwargs["session_cwd"] == tmp_path.resolve()


@pytest.mark.asyncio
async def test_initialize_falls_back_to_process_cwd_when_cwd_absent(monkeypatch) -> None:
    """With no wire ``cwd``, ``session_cwd`` is the process cwd, never ``None``."""
    prepared = _make_prepared()
    monkeypatch.setattr(_runtime, "load_and_prepare_cached", AsyncMock(return_value=prepared))

    await _runtime.handle_initialize({})

    _, kwargs = prepared.create_session.call_args
    assert kwargs["session_cwd"] == Path.cwd()
    assert kwargs["session_cwd"] is not None


@pytest.mark.asyncio
async def test_initialize_treats_empty_cwd_as_absent(monkeypatch) -> None:
    """An empty-string ``cwd`` falls back to the process cwd (``or None`` guard)."""
    prepared = _make_prepared()
    monkeypatch.setattr(_runtime, "load_and_prepare_cached", AsyncMock(return_value=prepared))

    await _runtime.handle_initialize({"cwd": ""})

    _, kwargs = prepared.create_session.call_args
    assert kwargs["session_cwd"] == Path.cwd()
