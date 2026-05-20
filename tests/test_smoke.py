"""Smoke tests — verify amplifier_agent_lib is importable and well-formed."""

from __future__ import annotations

import importlib

import pytest


def test_package_importable() -> None:
    """amplifier_agent_lib is importable and exposes a non-empty __version__ string."""
    import amplifier_agent_lib

    assert isinstance(amplifier_agent_lib.__version__, str)
    assert amplifier_agent_lib.__version__ != ""


def test_modes_stdio_loop_module_removed() -> None:
    """Mode B stdio_loop module must not exist (deleted in D10/A0 cleanup)."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("amplifier_agent_cli.modes.stdio_loop")


def test_defaults_stdio_module_removed() -> None:
    """Mode B defaults_stdio module must not exist (deleted in D10/A0 cleanup)."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("amplifier_agent_lib.protocol_points.defaults_stdio")
