"""Tests for amplifier_agent_cli package imports and basic availability."""

from __future__ import annotations


def test_cli_package_importable() -> None:
    """amplifier_agent_cli must be importable without error."""
    import amplifier_agent_cli  # noqa: F401


def test_cli_package_exposes_version() -> None:
    """amplifier_agent_cli must expose a non-empty __version__ string."""
    import amplifier_agent_cli

    assert isinstance(amplifier_agent_cli.__version__, str)
    assert len(amplifier_agent_cli.__version__) > 0


def test_click_is_available() -> None:
    """click must be importable and expose group/command decorators."""
    import click

    assert hasattr(click, "group")
    assert hasattr(click, "command")
