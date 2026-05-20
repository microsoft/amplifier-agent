"""Smoke tests — verify amplifier_agent_lib is importable and well-formed."""

from __future__ import annotations


def test_package_importable() -> None:
    """amplifier_agent_lib is importable and exposes a non-empty __version__ string."""
    import amplifier_agent_lib

    assert isinstance(amplifier_agent_lib.__version__, str)
    assert amplifier_agent_lib.__version__ != ""
