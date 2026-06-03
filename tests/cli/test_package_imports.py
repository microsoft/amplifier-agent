"""Tests for amplifier_agent_cli package imports and basic availability."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


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


def test_cli_skill_sources_module_absent() -> None:
    """skill_sources module must not exist in amplifier_agent_cli.

    Regression anchor for Task 3.3. With ``--skills-dir`` removed under D10
    (Task 3.2), ``skill_sources.py`` has no callers. Its responsibilities
    (extending the tool-skills mount entry's skill source list) move to the
    host_config merger per D12.

    BREAKING CHANGE: ``inject_skill_dirs()`` is no longer importable. Callers
    must use the host_config ``skills:`` block (D11) or the
    ``$AMPLIFIER_SKILLS_DIR`` environment variable (D13).
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("amplifier_agent_cli.skill_sources")


def test_inject_skill_dirs_symbol_absent_from_cli_package() -> None:
    """inject_skill_dirs must not be re-exported from amplifier_agent_cli.

    Companion to ``test_cli_skill_sources_module_absent``. Defends against
    a future regression where someone re-introduces the helper as a
    package-level export without restoring the dedicated module.
    """
    import amplifier_agent_cli

    assert not hasattr(amplifier_agent_cli, "inject_skill_dirs")


def test_cli_source_tree_has_no_skill_sources_file() -> None:
    """src/amplifier_agent_cli/skill_sources.py must not exist on disk.

    Filesystem-level anchor that complements the import-level assertions
    above. Catches cases where the file is added but not yet imported,
    and where stale copies linger in the source tree.
    """
    import amplifier_agent_cli

    package_root = Path(amplifier_agent_cli.__file__).resolve().parent
    assert not (package_root / "skill_sources.py").exists()
