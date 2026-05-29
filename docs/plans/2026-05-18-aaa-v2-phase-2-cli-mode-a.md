# AaA v2 — Phase 2: CLI Binary, Mode A + Admin Verbs — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the `amplifier_agent_cli` package: the thin CLI binary exposing Mode A (single-turn argv invocation) plus admin verbs (`doctor`, `config show`, `cache clear`), backed by the Phase 1 `amplifier_agent_lib` engine.

**Architecture:** A `click`-based dispatcher in `__main__.py` routes subcommands to per-verb handler modules. Mode A boots the engine with `CliApprovalSystem`/`CliDisplaySystem` defaults (already built in Phase 1) and invokes `submit_turn`, printing JSON to stdout. Admin verbs are pure-Python diagnostics. Mode B is stubbed (Phase 3 will implement it). The CLI binary is a thin I/O adapter; the engine remains mode-agnostic.

**Tech Stack:** Python 3.11+, `click>=8.1` (argv parsing), `pytest` (tests), `CliRunner` (click's test harness), `pytest-mock` / `unittest.mock` (engine mocking).

---

## Prerequisites

**Phase 1 must be complete.** This plan assumes the following from `docs/plans/2026-05-18-aaa-v2-phase-1-engine-lib.md` already exist and are importable:

- `amplifier_agent_lib.engine.Engine` with `boot(...)` and `submit_turn(prompt) -> dict` methods
- `amplifier_agent_lib.protocol_points.defaults_cli.CliApprovalSystem(mode: Literal["prompt", "yes", "no"])`
- `amplifier_agent_lib.protocol_points.defaults_cli.CliDisplaySystem(verbosity: Literal["quiet", "normal", "verbose", "debug"], stream=sys.stderr)`
- `amplifier_agent_lib.persistence` module exposing XDG path helpers: `xdg_config_home()`, `xdg_cache_home()`, `xdg_state_home()`, `prepared_bundle_dir(version)`
- `amplifier_agent_lib.protocol.errors.AaaError` exception class with `.code` and `.message` attributes
- `amplifier_agent_lib.__version__` string
- Repo has `pyproject.toml` with `src/amplifier_agent_lib/` layout, `pytest` configured, ruff + pyright wired

If any of the above are missing, **STOP and complete Phase 1 first.**

---

## Reference

- **Design source:** `/Users/mpaidiparthy/repos/AaA/opus-recon/aaa-source/docs/designs/aaa-v2-design-checkpoint.md` (commit `c74316c`). Read §3 (CLI design), §6 (approval/display defaults), Appendix A (wire protocol), Appendix C (Mode A vs Mode B), Appendix D (V1 carryforwards).
- **`click` pattern reference:** `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-app-openclaw/src/amplifier_app_openclaw/cli.py` lines 15–195. Mirror the `@click.group(invoke_without_command=True)` + `@cli.command()` + `@cli.group()` for `config` subgroup pattern.
- **Gotchas to NOT replicate from OpenClaw:**
  - OpenClaw mutates `os.environ["NO_COLOR"] = "1"` at line 60 — **AaA does NOT mutate user env.**
  - OpenClaw silently auto-enables persistence when `--session-name` is given — **AaA requires explicit `--resume`.**
  - OpenClaw returns 0 on KeyboardInterrupt (line 94–96) — **AaA returns 130 (conventional SIGINT exit).**

---

## Conventions for this plan

- **Working directory:** `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/`
- **All `Run:` commands assume cwd is the repo root above.**
- **Python is invoked via `uv run`** to use the project venv (matches Phase 1 conventions).
- **Conventional commits:** `feat: …`, `test: …`, `chore: …`, `fix: …`.
- **TDD cycle, strict:** RED (write test) → run test, verify FAIL → GREEN (write minimal code) → run test, verify PASS → COMMIT.
- **stdout/stderr discipline (LOCKED, design §3):**
  - stdout: ONLY JSON result (Mode A) or admin command output. Nothing else.
  - stderr: ALL diagnostics, progress, prompts, `[level]`-prefixed lines.
- **Exit codes:** 0 success, 1 general error, 2 usage error (click default for bad flags), 130 SIGINT.

---

## Task 1: Add `click` dependency and scaffold the CLI package

**Files:**
- Modify: `pyproject.toml`
- Create: `src/amplifier_agent_cli/__init__.py`
- Create: `tests/cli/__init__.py`
- Create: `tests/cli/test_package_imports.py`

**Step 1: Read current pyproject.toml**
Run: `cat pyproject.toml`
Expected: existing Phase 1 file with `[project]` and `[project.dependencies]` sections. Note current dependencies list and existing `[project.scripts]` block (if any).

**Step 2: Write the failing test**

Create `tests/cli/__init__.py` (empty file).

Create `tests/cli/test_package_imports.py`:
```python
"""Smoke tests: package imports and version exposure."""
from __future__ import annotations


def test_cli_package_importable() -> None:
    import amplifier_agent_cli  # noqa: F401


def test_cli_package_exposes_version() -> None:
    import amplifier_agent_cli

    assert isinstance(amplifier_agent_cli.__version__, str)
    assert amplifier_agent_cli.__version__  # non-empty


def test_click_is_available() -> None:
    import click  # noqa: F401

    assert hasattr(click, "group")
    assert hasattr(click, "command")
```

**Step 3: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_package_imports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_cli'`.

**Step 4: Add the click dependency and the entry-point script**

Edit `pyproject.toml`:
- Under `[project] dependencies = [...]`, add `"click>=8.1"` if not already present.
- Add (or update) a `[project.scripts]` section:
  ```toml
  [project.scripts]
  amplifier-agent = "amplifier_agent_cli.__main__:main"
  ```

**Step 5: Create the package init**

Create `src/amplifier_agent_cli/__init__.py`:
```python
"""Amplifier-Agent CLI — thin I/O adapter over amplifier_agent_lib.

This package exposes the `amplifier-agent` console script. The library
(`amplifier_agent_lib`) is mode-agnostic; this package wraps it with two
invocation modes (A: argv single-turn, B: stdio JSON-RPC) plus admin verbs.

Phase 2 implements Mode A and admin verbs; Mode B is stubbed pending Phase 3.
"""

from __future__ import annotations

from amplifier_agent_lib import __version__ as _lib_version

__version__: str = _lib_version

__all__ = ["__version__"]
```

**Step 6: Re-sync the project venv**
Run: `uv sync`
Expected: resolves `click>=8.1` into the lockfile; exit 0.

**Step 7: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_package_imports.py -v`
Expected: 3 passed.

**Step 8: Commit**
Run:
```
git add pyproject.toml uv.lock src/amplifier_agent_cli/__init__.py tests/cli/__init__.py tests/cli/test_package_imports.py
git commit -m "feat(cli): scaffold amplifier_agent_cli package and add click dep"
```

---

## Task 2: Implement `provider_detect.py`

**Files:**
- Create: `src/amplifier_agent_cli/provider_detect.py`
- Create: `tests/cli/test_provider_detect.py`

**Design intent (from checkpoint §3, D5):** auto-detect provider from env vars. Precedence: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `AZURE_OPENAI_API_KEY` → `OLLAMA_HOST`. Honor `--provider` override. Raise structured error `provider_not_configured` when nothing is set.

> **Implementation update (2026-05-29, PR following #22):** the Azure env var was originally specified as `AZURE_OPENAI_KEY`, but the README, the upstream `amplifier-module-provider-azure-openai` module, and the Azure OpenAI Python SDK all use `AZURE_OPENAI_API_KEY`. The CLI now prefers `AZURE_OPENAI_API_KEY` and accepts the legacy `AZURE_OPENAI_KEY` spelling as a deprecated alias (one-time stderr warning when used). The code snippets and example tests below were authored before this alignment and still use the legacy spelling for historical fidelity; the ship-state precedence is the one quoted in this paragraph.

**Step 1: Write the failing test**

Create `tests/cli/test_provider_detect.py`:
```python
"""Tests for amplifier_agent_cli.provider_detect."""
from __future__ import annotations

import pytest

from amplifier_agent_cli.provider_detect import (
    ProviderNotConfigured,
    detect_provider,
)


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(key, raising=False)


def test_detects_anthropic_when_only_anthropic_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    provider = detect_provider(override=None)

    assert provider == "anthropic"


def test_detects_openai_when_only_openai_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    assert detect_provider(override=None) == "openai"


def test_detects_azure_when_only_azure_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az-test")

    assert detect_provider(override=None) == "azure-openai"


def test_detects_ollama_when_only_ollama_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    assert detect_provider(override=None) == "ollama"


def test_precedence_anthropic_over_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    assert detect_provider(override=None) == "anthropic"


def test_precedence_openai_over_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az")

    assert detect_provider(override=None) == "openai"


def test_precedence_azure_over_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    assert detect_provider(override=None) == "azure-openai"


def test_override_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    assert detect_provider(override="openai") == "openai"


def test_override_accepts_known_providers() -> None:
    for name in ("anthropic", "openai", "azure-openai", "ollama"):
        assert detect_provider(override=name) == name


def test_override_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderNotConfigured) as exc:
        detect_provider(override="bogus")
    assert exc.value.code == "provider_not_configured"
    assert "bogus" in exc.value.message


def test_raises_when_no_env_and_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)

    with pytest.raises(ProviderNotConfigured) as exc:
        detect_provider(override=None)
    assert exc.value.code == "provider_not_configured"
    assert "ANTHROPIC_API_KEY" in exc.value.message
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_provider_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_cli.provider_detect'`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_cli/provider_detect.py`:
```python
"""Provider auto-detection from environment variables.

Precedence (locked, design §3 + D5; Azure var renamed 2026-05-29 — see Task 2 note):
    ANTHROPIC_API_KEY > OPENAI_API_KEY > AZURE_OPENAI_API_KEY > OLLAMA_HOST
    (legacy alias AZURE_OPENAI_KEY still accepted, deprecated)

`--provider` override (CLI flag) bypasses detection. Unknown overrides
raise ProviderNotConfigured.

If none of the env vars is set and no override is given, raise
ProviderNotConfigured with an actionable message.
"""

from __future__ import annotations

import os
from typing import Final

KNOWN_PROVIDERS: Final[tuple[str, ...]] = (
    "anthropic",
    "openai",
    "azure-openai",
    "ollama",
)

# Ordered precedence: env var -> provider name.
_DETECTION_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY", "openai"),
    ("AZURE_OPENAI_KEY", "azure-openai"),
    ("OLLAMA_HOST", "ollama"),
)


class ProviderNotConfigured(Exception):
    """Raised when no provider can be selected.

    Maps to the `provider_not_configured` wire error code (Appendix A).
    """

    code: str = "provider_not_configured"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def detect_provider(override: str | None) -> str:
    """Return the provider name to use.

    Args:
        override: Value of `--provider` flag, or None.

    Returns:
        One of: "anthropic", "openai", "azure-openai", "ollama".

    Raises:
        ProviderNotConfigured: If override is unknown, or no env var is set.
    """
    if override is not None:
        if override in KNOWN_PROVIDERS:
            return override
        raise ProviderNotConfigured(
            f"Unknown provider {override!r}. "
            f"Known: {', '.join(KNOWN_PROVIDERS)}."
        )

    for env_var, provider in _DETECTION_ORDER:
        if os.environ.get(env_var):
            return provider

    raise ProviderNotConfigured(
        "No provider configured. Set one of "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_KEY, OLLAMA_HOST, "
        "or pass --provider <name>. "
        "See https://github.com/microsoft/amplifier-agent/blob/main/README.md "
        "for setup."
    )
```

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_provider_detect.py -v`
Expected: 11 passed.

**Step 5: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/provider_detect.py tests/cli/test_provider_detect.py && uv run pyright src/amplifier_agent_cli/provider_detect.py`
Expected: clean (no errors).

**Step 6: Commit**
Run:
```
git add src/amplifier_agent_cli/provider_detect.py tests/cli/test_provider_detect.py
git commit -m "feat(cli): provider auto-detect with --provider override and precedence"
```

---

## Task 3: Implement `tty_detect.py`

**Files:**
- Create: `src/amplifier_agent_cli/tty_detect.py`
- Create: `tests/cli/test_tty_detect.py`

**Design intent:** small pure-helpers wrapping `os.isatty(fd)`. Mode A's approval default (`prompt-when-tty, deny-otherwise`) consults these to decide whether to surface the readline prompt or hard-deny.

**Step 1: Write the failing test**

Create `tests/cli/test_tty_detect.py`:
```python
"""Tests for amplifier_agent_cli.tty_detect."""
from __future__ import annotations

from unittest.mock import patch

from amplifier_agent_cli.tty_detect import is_stdin_tty, is_stdout_tty


def test_is_stdin_tty_true_when_isatty_true() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=True) as mock:
        assert is_stdin_tty() is True
        mock.assert_called_once_with(0)


def test_is_stdin_tty_false_when_isatty_false() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=False):
        assert is_stdin_tty() is False


def test_is_stdin_tty_false_when_oserror() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", side_effect=OSError("bad fd")):
        assert is_stdin_tty() is False


def test_is_stdout_tty_true_when_isatty_true() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=True) as mock:
        assert is_stdout_tty() is True
        mock.assert_called_once_with(1)


def test_is_stdout_tty_false_when_isatty_false() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=False):
        assert is_stdout_tty() is False


def test_is_stdout_tty_false_when_oserror() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", side_effect=OSError):
        assert is_stdout_tty() is False
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_tty_detect.py -v`
Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_cli/tty_detect.py`:
```python
"""TTY detection helpers.

Used by Mode A (`single_turn.py`) to decide whether to surface approval
prompts via readline (TTY) or hard-deny (CI, pipe, daemon).

Both functions are defensive: any OSError from os.isatty (closed fd,
non-fd-backed stream, exotic platform behavior) returns False.
"""

from __future__ import annotations

import os


def is_stdin_tty() -> bool:
    """True iff fd 0 is a TTY."""
    try:
        return os.isatty(0)
    except OSError:
        return False


def is_stdout_tty() -> bool:
    """True iff fd 1 is a TTY."""
    try:
        return os.isatty(1)
    except OSError:
        return False
```

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_tty_detect.py -v`
Expected: 6 passed.

**Step 5: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/tty_detect.py tests/cli/test_tty_detect.py && uv run pyright src/amplifier_agent_cli/tty_detect.py`
Expected: clean.

**Step 6: Commit**
Run:
```
git add src/amplifier_agent_cli/tty_detect.py tests/cli/test_tty_detect.py
git commit -m "feat(cli): tty detection helpers for stdin/stdout"
```

---

## Task 4: Implement `__main__.py` click skeleton + version

**Files:**
- Create: `src/amplifier_agent_cli/__main__.py`
- Create: `src/amplifier_agent_cli/modes/__init__.py`
- Create: `src/amplifier_agent_cli/modes/single_turn.py` (stub)
- Create: `src/amplifier_agent_cli/admin/__init__.py`
- Create: `src/amplifier_agent_cli/admin/doctor.py` (stub)
- Create: `src/amplifier_agent_cli/admin/config_show.py` (stub)
- Create: `src/amplifier_agent_cli/admin/cache_clear.py` (stub)
- Create: `tests/cli/test_main_dispatch.py`

**Design intent (checkpoint §3, locked):**
- Top-level group `amplifier-agent`. No subcommand → print help (exit 0).
- `--version` prints package version and exits.
- `--help` prints help.
- Subcommands wired in this task (stubbed): `run`, `doctor`, `config show`, `cache clear`. The stubs raise `NotImplementedError` and exit 1 — they'll be replaced in subsequent tasks. We register them now so the dispatcher is complete and `--help` shows them.
- Unknown subcommand → click exits 2 (usage error).
- `main()` is the entry point referenced by `pyproject.toml [project.scripts]`.
- KeyboardInterrupt → exit 130.

**Step 1: Write the failing test**

Create `tests/cli/test_main_dispatch.py`:
```python
"""Tests for amplifier_agent_cli.__main__ — argv dispatch / version / help."""
from __future__ import annotations

from click.testing import CliRunner

from amplifier_agent_cli import __version__
from amplifier_agent_cli.__main__ import cli


def test_version_flag_prints_version_and_exits_0() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_flag_prints_help_and_exits_0() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "amplifier-agent" in result.output.lower() or "Usage:" in result.output


def test_no_subcommand_prints_help_and_exits_0() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_unknown_subcommand_exits_2() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["bogus-command"])

    assert result.exit_code == 2


def test_help_lists_run_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert "run" in result.output


def test_help_lists_doctor_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert "doctor" in result.output


def test_help_lists_config_subgroup() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert "config" in result.output


def test_help_lists_cache_subgroup() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert "cache" in result.output


def test_config_subgroup_shows_show_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--help"])

    assert result.exit_code == 0
    assert "show" in result.output


def test_cache_subgroup_shows_clear_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "--help"])

    assert result.exit_code == 0
    assert "clear" in result.output
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_main_dispatch.py -v`
Expected: FAIL — module `amplifier_agent_cli.__main__` does not exist.

**Step 3: Create the four stub handler modules**

Create `src/amplifier_agent_cli/modes/__init__.py` (empty file).

Create `src/amplifier_agent_cli/modes/single_turn.py`:
```python
"""Mode A — single-turn argv invocation. Real implementation in Task 8."""

from __future__ import annotations

import click


@click.command()
def run() -> None:
    """Run a single prompt (stub; replaced in Task 8)."""
    raise NotImplementedError("Task 8 will implement this")
```

Create `src/amplifier_agent_cli/admin/__init__.py` (empty file).

Create `src/amplifier_agent_cli/admin/doctor.py`:
```python
"""amplifier-agent doctor — stub; real implementation in Task 7."""

from __future__ import annotations

import click


@click.command()
def doctor() -> None:
    """Diagnose environment (stub; replaced in Task 7)."""
    raise NotImplementedError("Task 7 will implement this")
```

Create `src/amplifier_agent_cli/admin/config_show.py`:
```python
"""amplifier-agent config <verb> — stubs; real implementation in Task 6."""

from __future__ import annotations

import click


@click.group()
def config_group() -> None:
    """Inspect resolved config."""


@config_group.command(name="show")
def config_show() -> None:
    """Print resolved config (stub; replaced in Task 6)."""
    raise NotImplementedError("Task 6 will implement this")
```

Create `src/amplifier_agent_cli/admin/cache_clear.py`:
```python
"""amplifier-agent cache <verb> — stubs; real implementation in Task 5."""

from __future__ import annotations

import click


@click.group()
def cache_group() -> None:
    """Manage the prepared-bundle cache."""


@cache_group.command(name="clear")
def cache_clear() -> None:
    """Clear XDG prepared-bundle cache (stub; replaced in Task 5)."""
    raise NotImplementedError("Task 5 will implement this")
```

**Step 4: Create the dispatcher**

Create `src/amplifier_agent_cli/__main__.py`:
```python
"""amplifier-agent CLI entry point.

Dispatches argv to subcommands:
    run            Single-turn (Mode A) or stdio JSON-RPC (Mode B; Phase 3 stub)
    doctor         Diagnose environment (provider keys, XDG paths, cache, Python)
    config show    Print resolved config with source annotations
    cache clear    Invalidate the XDG prepared-bundle cache

The library (amplifier_agent_lib) is mode-agnostic; this binary chooses
the protocol-point defaults (CliApprovalSystem, CliDisplaySystem) and
turns argv shape into a single turn through the engine.

All diagnostics flow to stderr. stdout is reserved for the final JSON
result (Mode A) or admin-command output. See checkpoint §3.
"""

from __future__ import annotations

import sys

import click

from amplifier_agent_cli import __version__
from amplifier_agent_cli.admin.cache_clear import cache_group as _cache_group
from amplifier_agent_cli.admin.config_show import config_group as _config_group
from amplifier_agent_cli.admin.doctor import doctor as _doctor_command
from amplifier_agent_cli.modes.single_turn import run as _run_command


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="amplifier-agent")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Amplifier-Agent — stdio coprocess for the Amplifier kernel."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


cli.add_command(_run_command)
cli.add_command(_doctor_command)
cli.add_command(_config_group, name="config")
cli.add_command(_cache_group, name="cache")


def main() -> None:
    """Console-script entry point. Maps KeyboardInterrupt → exit 130."""
    try:
        cli(standalone_mode=True)
    except KeyboardInterrupt:
        # Defensive: click usually handles SIGINT, but if anything escapes,
        # honor the conventional exit code. Do NOT print to stdout.
        print("\n[info] Interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
```

**Step 5: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_main_dispatch.py -v`
Expected: 10 passed.

**Step 6: Verify console-script is installed and `--version` works end-to-end**
Run: `uv sync && uv run amplifier-agent --version`
Expected: prints `amplifier-agent, version <version>` and exits 0.

**Step 7: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli tests/cli && uv run pyright src/amplifier_agent_cli`
Expected: clean.

**Step 8: Commit**
Run:
```
git add src/amplifier_agent_cli tests/cli/test_main_dispatch.py
git commit -m "feat(cli): click dispatcher skeleton with --version and subcommand stubs"
```

---

## Task 5: Implement `cache clear` admin verb (simplest admin)

**Files:**
- Modify: `src/amplifier_agent_cli/admin/cache_clear.py`
- Create: `tests/cli/test_cache_clear.py`

**Design intent (checkpoint §3):** `amplifier-agent cache clear` removes the prepared-bundle cache under `$XDG_CACHE_HOME/amplifier-agent/prepared/`. Reports which paths were removed. Idempotent — silent success on empty cache.

**Step 1: Write the failing test**

Create `tests/cli/test_cache_clear.py`:
```python
"""Tests for `amplifier-agent cache clear`."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture
def fake_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG_CACHE_HOME at a tmp dir and seed a fake prepared bundle."""
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

    prepared = cache_root / "amplifier-agent" / "prepared" / "0.0.0"
    prepared.mkdir(parents=True)
    (prepared / "bundle.json").write_text("{}", encoding="utf-8")
    (prepared / "sub").mkdir()
    (prepared / "sub" / "more.txt").write_text("hi", encoding="utf-8")

    return cache_root / "amplifier-agent"


def test_cache_clear_removes_prepared_dir(fake_cache: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])

    assert result.exit_code == 0
    assert not (fake_cache / "prepared").exists()


def test_cache_clear_reports_removed_path(fake_cache: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])

    assert result.exit_code == 0
    assert "prepared" in result.output


def test_cache_clear_idempotent_when_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])

    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "no cache" in result.output.lower()


def test_cache_clear_does_not_remove_unrelated_dirs(fake_cache: Path) -> None:
    sibling = fake_cache.parent / "some-other-tool"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])

    assert result.exit_code == 0
    assert sibling.exists()
    assert (sibling / "keep.txt").exists()
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_cache_clear.py -v`
Expected: FAIL — current stub raises NotImplementedError.

**Step 3: Replace the stub with the real implementation**

Replace `src/amplifier_agent_cli/admin/cache_clear.py`:
```python
"""amplifier-agent cache clear — invalidate the prepared-bundle cache.

Removes `$XDG_CACHE_HOME/amplifier-agent/prepared/` (all versions).
Idempotent: silent success when the directory does not exist.

Output goes to stdout (this is an admin command, not a turn result).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from amplifier_agent_lib.persistence import xdg_cache_home


def _prepared_root() -> Path:
    """Return $XDG_CACHE_HOME/amplifier-agent/prepared (path may not exist)."""
    return xdg_cache_home() / "amplifier-agent" / "prepared"


@click.group()
def cache_group() -> None:
    """Manage the prepared-bundle cache."""


@cache_group.command(name="clear")
def cache_clear() -> None:
    """Clear the prepared-bundle cache (forces re-prepare on next run)."""
    target = _prepared_root()

    if not target.exists():
        click.echo(f"Nothing to clear: {target} does not exist.")
        return

    shutil.rmtree(target)
    click.echo(f"Cleared prepared bundle cache at {target}.")
```

**Note:** If `amplifier_agent_lib.persistence.xdg_cache_home()` is not exported in Phase 1, add a thin local wrapper instead — DO NOT modify the lib seam here. If you need a local fallback:

```python
import os
from pathlib import Path

def xdg_cache_home() -> Path:
    value = os.environ.get("XDG_CACHE_HOME")
    if value:
        return Path(value)
    return Path.home() / ".cache"
```

Prefer the lib helper once available; record the choice in the commit message.

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_cache_clear.py -v`
Expected: 4 passed.

**Step 5: Verify the main-dispatch tests still pass (regression)**
Run: `uv run pytest tests/cli/ -v`
Expected: all green.

**Step 6: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/admin/cache_clear.py tests/cli/test_cache_clear.py && uv run pyright src/amplifier_agent_cli/admin/cache_clear.py`
Expected: clean.

**Step 7: Commit**
Run:
```
git add src/amplifier_agent_cli/admin/cache_clear.py tests/cli/test_cache_clear.py
git commit -m "feat(cli): implement cache clear admin verb"
```

---

## Task 6: Implement `config show` admin verb

**Files:**
- Modify: `src/amplifier_agent_cli/admin/config_show.py`
- Create: `tests/cli/test_config_show.py`

**Design intent (checkpoint §3, locked precedence):** print the resolved config (provider, XDG paths, flags) annotated with where each value came from: `(flag)`, `(env)`, `(file)`, `(default)`. Precedence: CLI flags > env vars > XDG config file > compiled defaults.

For Phase 2 we surface: provider, XDG config / cache / state paths. We do NOT yet read the optional `config.toml` file — leave the source annotation as `default` for fields with no env override. Reading `config.toml` is a follow-up; this task's contract is the output shape + source annotations for env + defaults.

**Step 1: Write the failing test**

Create `tests/cli/test_config_show.py`:
```python
"""Tests for `amplifier-agent config show`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


def test_config_show_outputs_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, dict)


def test_config_show_reports_provider_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["provider"]["value"] == "anthropic"
    assert parsed["provider"]["source"] == "env:ANTHROPIC_API_KEY"


def test_config_show_reports_xdg_config_home_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["value"] == str(cfg)
    assert parsed["xdg_config_home"]["source"] == "env:XDG_CONFIG_HOME"


def test_config_show_reports_default_when_env_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["source"] == "default"


def test_config_show_handles_no_provider_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    # Reports the state explicitly; does NOT crash with exit != 0
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["provider"]["value"] is None
    assert parsed["provider"]["source"] == "unset"
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_config_show.py -v`
Expected: FAIL — current stub raises NotImplementedError.

**Step 3: Replace the stub with the real implementation**

Replace `src/amplifier_agent_cli/admin/config_show.py`:
```python
"""amplifier-agent config <verb> — resolved-config inspection.

`config show` prints a JSON object describing each config value AND its
source (CLI flag > env > XDG file > default). Phase 2 surfaces env +
defaults; the XDG config file reader is a follow-up.

Output on stdout (admin command); diagnostics on stderr.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from amplifier_agent_cli.provider_detect import (
    ProviderNotConfigured,
    detect_provider,
)


def _annotate_env_or_default(env_var: str, default: Path) -> dict[str, Any]:
    value = os.environ.get(env_var)
    if value:
        return {"value": value, "source": f"env:{env_var}"}
    return {"value": str(default), "source": "default"}


def _resolve_provider() -> dict[str, Any]:
    # Precedence (env vars), shape mirrors detect_provider().
    precedence = (
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("OPENAI_API_KEY", "openai"),
        ("AZURE_OPENAI_KEY", "azure-openai"),
        ("OLLAMA_HOST", "ollama"),
    )
    for env_var, name in precedence:
        if os.environ.get(env_var):
            return {"value": name, "source": f"env:{env_var}"}
    # Try detect_provider to honor any future logic; if it fails, report unset.
    try:
        return {"value": detect_provider(override=None), "source": "default"}
    except ProviderNotConfigured:
        return {"value": None, "source": "unset"}


@click.group()
def config_group() -> None:
    """Inspect resolved config."""


@config_group.command(name="show")
def config_show() -> None:
    """Print resolved config as JSON with source annotations."""
    home = Path(os.environ.get("HOME", str(Path.home())))

    payload: dict[str, Any] = {
        "provider": _resolve_provider(),
        "xdg_config_home": _annotate_env_or_default("XDG_CONFIG_HOME", home / ".config"),
        "xdg_cache_home": _annotate_env_or_default("XDG_CACHE_HOME", home / ".cache"),
        "xdg_state_home": _annotate_env_or_default("XDG_STATE_HOME", home / ".local" / "state"),
    }

    click.echo(json.dumps(payload, indent=2))
```

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_config_show.py -v`
Expected: 5 passed.

**Step 5: Verify the full CLI test suite still passes (regression)**
Run: `uv run pytest tests/cli/ -v`
Expected: all green.

**Step 6: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/admin/config_show.py tests/cli/test_config_show.py && uv run pyright src/amplifier_agent_cli/admin/config_show.py`
Expected: clean.

**Step 7: Commit**
Run:
```
git add src/amplifier_agent_cli/admin/config_show.py tests/cli/test_config_show.py
git commit -m "feat(cli): implement config show admin verb with source annotations"
```

---

## Task 7: Implement `doctor` admin verb

**Files:**
- Modify: `src/amplifier_agent_cli/admin/doctor.py`
- Create: `tests/cli/test_doctor.py`

**Design intent (checkpoint §3 + §7 risks):** self-diagnostic reporting per-subsystem `OK / FAIL` for: provider keys, XDG path writability (config + cache + state), prepared-bundle cache presence, Python version (>=3.11). Output is human-readable. Exit 0 if all required checks pass; exit 1 if any fail.

For Phase 2, the prepared-bundle cache check is informational only — Phase 4 will populate the cache; in Phase 2 a missing cache is reported but NOT a failure (it's expected).

**Step 1: Write the failing test**

Create `tests/cli/test_doctor.py`:
```python
"""Tests for `amplifier-agent doctor`."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture
def writable_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def _clear_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)


def test_doctor_all_green_exits_0(writable_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_providers(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "OK" in result.output


def test_doctor_reports_provider_status(writable_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_providers(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert "provider" in result.output.lower()
    assert "openai" in result.output


def test_doctor_fails_when_no_provider(writable_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_providers(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "FAIL" in result.output or "fail" in result.output.lower()
    assert "provider" in result.output.lower()


def test_doctor_reports_xdg_paths(writable_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_providers(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert "config" in result.output.lower()
    assert "cache" in result.output.lower()
    assert "state" in result.output.lower()


def test_doctor_reports_python_version(writable_xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_providers(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert "python" in result.output.lower()


def test_doctor_reports_bundle_cache_missing_as_info_not_failure(
    writable_xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_providers(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    # Cache missing is informational in Phase 2 (Phase 4 populates).
    assert result.exit_code == 0
    assert "bundle" in result.output.lower() or "cache" in result.output.lower()
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_doctor.py -v`
Expected: FAIL — current stub raises NotImplementedError.

**Step 3: Replace the stub with the real implementation**

Replace `src/amplifier_agent_cli/admin/doctor.py`:
```python
"""amplifier-agent doctor — self-diagnostic.

Reports per-subsystem PASS/FAIL/INFO lines on stdout and exits 0 if all
required checks pass, 1 otherwise. Phase 2 checks:

    - Provider configured (FAIL if none)
    - XDG config / cache / state homes writable (FAIL if not)
    - Python >= 3.11 (FAIL if not)
    - Prepared-bundle cache present (INFO only in Phase 2; Phase 4 populates)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from amplifier_agent_cli.provider_detect import (
    ProviderNotConfigured,
    detect_provider,
)

_OK = "[ OK ]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _check_provider() -> tuple[bool, str]:
    try:
        name = detect_provider(override=None)
    except ProviderNotConfigured as exc:
        return False, f"{_FAIL} provider: {exc.message}"
    return True, f"{_OK} provider: {name}"


def _xdg(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else default


def _check_writable(label: str, path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{_FAIL} {label}: {path} ({exc.__class__.__name__})"
    return True, f"{_OK} {label}: {path}"


def _check_python_version() -> tuple[bool, str]:
    version = sys.version_info
    label = f"python: {version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < (3, 11):
        return False, f"{_FAIL} {label} (need >= 3.11)"
    return True, f"{_OK} {label}"


def _check_bundle_cache(cache_home: Path) -> tuple[bool, str]:
    # Phase 2: informational only. Phase 4 populates this.
    prepared = cache_home / "amplifier-agent" / "prepared"
    if prepared.exists() and any(prepared.iterdir()):
        return True, f"{_OK} bundle cache: {prepared}"
    return True, f"{_INFO} bundle cache: not yet prepared at {prepared}"


@click.command()
def doctor() -> None:
    """Diagnose environment for amplifier-agent."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    cfg = _xdg("XDG_CONFIG_HOME", home / ".config") / "amplifier-agent"
    cache = _xdg("XDG_CACHE_HOME", home / ".cache") / "amplifier-agent"
    state = _xdg("XDG_STATE_HOME", home / ".local" / "state") / "amplifier-agent"
    cache_home = _xdg("XDG_CACHE_HOME", home / ".cache")

    checks: list[tuple[bool, str]] = [
        _check_python_version(),
        _check_provider(),
        _check_writable("config home", cfg),
        _check_writable("cache home", cache),
        _check_writable("state home", state),
        _check_bundle_cache(cache_home),
    ]

    for _ok, line in checks:
        click.echo(line)

    # Required-pass checks: python, provider, writability. Bundle cache is INFO.
    required = checks[:5]
    if all(ok for ok, _ in required):
        sys.exit(0)
    sys.exit(1)
```

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_doctor.py -v`
Expected: 6 passed.

**Step 5: Verify full CLI test suite still passes**
Run: `uv run pytest tests/cli/ -v`
Expected: all green.

**Step 6: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/admin/doctor.py tests/cli/test_doctor.py && uv run pyright src/amplifier_agent_cli/admin/doctor.py`
Expected: clean.

**Step 7: Commit**
Run:
```
git add src/amplifier_agent_cli/admin/doctor.py tests/cli/test_doctor.py
git commit -m "feat(cli): implement doctor admin verb"
```

---

## Task 8: Implement Mode A — `run` command with engine wiring

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/cli/test_single_turn.py`

**Design intent (checkpoint §3 + §6, locked):**
- `amplifier-agent run "prompt" [flags]` boots the engine, calls `submit_turn(prompt)`, prints the JSON result to stdout, exits 0.
- Flags supported (per §3): `--session-id <id>`, `--resume`, `--fresh`, `--stdio`, `--idle-timeout <ms>`, `--provider <name>`, `--bundle <name>` (hidden), `--config <path>`, `--cwd <path>`, `-v/--verbose`, `--debug`, `-y/--yes`, `-n/--no`, `--quiet`.
- `--stdio` is a Phase 3 stub: prints `[error] --stdio (Mode B) is not yet implemented (Phase 3).` to stderr and exits 1. The flag is registered now so argv parsing is complete and `--help` is accurate.
- stdin discipline: if no prompt arg and stdin is NOT a TTY → emit `prompt_required` error on stderr and exit 2.
- Approval mode mapping (`-y` → "yes", `-n` → "no", default → "prompt" when TTY / "no" otherwise); `-y` and `-n` are mutually exclusive (raises `click.UsageError` → exit 2).
- Display verbosity mapping (`--debug` → "debug", `--verbose` → "verbose", `--quiet` → "quiet", default → "normal").
- `AaaError` from the engine → JSON error envelope `{"error": {"code": ..., "message": ...}}` on stdout + exit 1.

**Step 1: Write the failing test**

Create `tests/cli/test_single_turn.py`:
```python
"""Tests for Mode A — `amplifier-agent run "prompt" ...`.

Engine is mocked via patching the Engine class in modes.single_turn. We test
argv parsing, flag mapping, stdout/stderr discipline, and error envelopes.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


# --- Helpers ------------------------------------------------------------------


def _mock_engine(reply: str = "hello world") -> MagicMock:
    """Build a MagicMock that mimics the Engine class contract."""
    engine_instance = MagicMock()
    engine_instance.submit_turn.return_value = {
        "reply": reply,
        "turnId": "t-1",
        "usage": {"inputTokens": 4, "outputTokens": 2},
    }
    engine_cls = MagicMock()
    engine_cls.boot.return_value = engine_instance
    return engine_cls


def _set_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


# --- Tests --------------------------------------------------------------------


def test_run_with_prompt_prints_json_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine(reply="hello!")

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "say hi"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["reply"] == "hello!"


def test_run_passes_prompt_to_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "do the thing"])

    assert result.exit_code == 0
    instance = engine_cls.boot.return_value
    instance.submit_turn.assert_called_once_with("do the thing")


def test_run_y_flag_sets_approval_mode_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}

    def _boot(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return engine_cls.boot.return_value

    engine_cls.boot.side_effect = _boot

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "-y"])

    assert result.exit_code == 0
    assert captured["approval"].mode == "yes"


def test_run_n_flag_sets_approval_mode_no(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "-n"])

    assert result.exit_code == 0
    assert captured["approval"].mode == "no"


def test_run_y_and_n_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["run", "hi", "-y", "-n"])

    assert result.exit_code == 2


def test_run_default_approval_is_prompt_when_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with (
        patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls),
        patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=True),
    ):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi"])

    assert result.exit_code == 0
    assert captured["approval"].mode == "prompt"


def test_run_default_approval_is_no_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with (
        patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls),
        patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False),
    ):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi"])

    assert result.exit_code == 0
    assert captured["approval"].mode == "no"


def test_run_quiet_flag_sets_display_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "--quiet"])

    assert result.exit_code == 0
    assert captured["display"].verbosity == "quiet"


def test_run_verbose_flag_sets_display_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "--verbose"])

    assert result.exit_code == 0
    assert captured["display"].verbosity == "verbose"


def test_run_session_id_and_resume_passed_to_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "--session-id", "abc", "--resume"])

    assert result.exit_code == 0
    assert captured["session_id"] == "abc"
    assert captured["resume"] is True


def test_run_fresh_flag_passed_to_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    engine_cls = _mock_engine()
    captured: dict[str, Any] = {}
    engine_cls.boot.side_effect = lambda **kw: (captured.update(kw), engine_cls.boot.return_value)[1]

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi", "--session-id", "abc", "--fresh"])

    assert result.exit_code == 0
    assert captured["fresh"] is True


def test_run_stdio_is_phase_3_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_anthropic(monkeypatch)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["run", "--stdio"])

    assert result.exit_code == 1
    assert "Phase 3" in result.stderr or "stdio" in result.stderr.lower()


def test_run_missing_prompt_and_non_tty_fails_with_prompt_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_anthropic(monkeypatch)
    with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run"])

    assert result.exit_code == 2
    assert "prompt_required" in result.stderr or "prompt" in result.stderr.lower()


def test_run_no_provider_configured_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["run", "hi"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "provider_not_configured"


def test_run_engine_raising_aaa_error_returns_json_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_anthropic(monkeypatch)

    from amplifier_agent_lib.protocol.errors import AaaError

    engine_cls = MagicMock()
    instance = MagicMock()
    instance.submit_turn.side_effect = AaaError(
        code="bundle_load_failed", message="bad bundle"
    )
    engine_cls.boot.return_value = instance

    with patch("amplifier_agent_cli.modes.single_turn.Engine", engine_cls):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["run", "hi"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "bundle_load_failed"
    assert parsed["error"]["message"] == "bad bundle"
```

**Step 2: Run test to verify it fails**
Run: `uv run pytest tests/cli/test_single_turn.py -v`
Expected: FAIL — current stub raises NotImplementedError on all paths.

**Step 3: Replace the stub with the real implementation**

Replace `src/amplifier_agent_cli/modes/single_turn.py`:
```python
"""Mode A — single-turn argv invocation.

    amplifier-agent run "prompt" [--session-id X] [--resume] [--fresh]
                                 [--provider NAME] [--cwd PATH]
                                 [-y | -n] [--quiet | --verbose | --debug]

Boots the Engine with CliApprovalSystem + CliDisplaySystem defaults
(both injected from Phase 1), submits one turn, prints the JSON result
to stdout, exits.

stdout: JSON result only (success or error envelope).
stderr: prompts, diagnostics, display output.

Mode B (--stdio) is a Phase 3 stub.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from amplifier_agent_lib.engine import Engine
from amplifier_agent_lib.protocol.errors import AaaError
from amplifier_agent_lib.protocol_points.defaults_cli import (
    CliApprovalSystem,
    CliDisplaySystem,
)

from amplifier_agent_cli.provider_detect import (
    ProviderNotConfigured,
    detect_provider,
)
from amplifier_agent_cli.tty_detect import is_stdin_tty


def _emit_error(code: str, message: str) -> None:
    """Emit a structured error envelope to stdout."""
    click.echo(json.dumps({"error": {"code": code, "message": message}}, indent=2))


def _resolve_approval_mode(yes: bool, no: bool) -> str:
    if yes and no:
        # Click's mutex would normally catch this; defense-in-depth.
        raise click.UsageError("-y and -n are mutually exclusive")
    if yes:
        return "yes"
    if no:
        return "no"
    return "prompt" if is_stdin_tty() else "no"


def _resolve_verbosity(quiet: bool, verbose: bool, debug: bool) -> str:
    if debug:
        return "debug"
    if verbose:
        return "verbose"
    if quiet:
        return "quiet"
    return "normal"


@click.command()
@click.argument("prompt", required=False)
@click.option("--session-id", default=None, help="Logical session identifier.")
@click.option("--resume", is_flag=True, default=False, help="Load transcript before submitting.")
@click.option("--fresh", is_flag=True, default=False, help="Discard any existing transcript.")
@click.option("--stdio", is_flag=True, default=False, help="Mode B (Phase 3): JSON-RPC over stdio.")
@click.option("--idle-timeout", type=int, default=None, help="Mode B only: idle-timeout in ms.")
@click.option("--provider", "provider_override", default=None, help="Override provider auto-detect.")
@click.option("--bundle", default=None, hidden=True, help="Dev/test only.")
@click.option("--config", "config_path", default=None, help="Override XDG config path.")
@click.option("--cwd", default=None, help="Working directory for tool execution.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Verbose stderr diagnostics.")
@click.option("--debug", is_flag=True, default=False, help="Debug-level stderr diagnostics.")
@click.option("-y", "--yes", "yes_flag", is_flag=True, default=False, help="Mode A: auto-approve all.")
@click.option("-n", "--no", "no_flag", is_flag=True, default=False, help="Mode A: auto-deny all.")
@click.option("--quiet", is_flag=True, default=False, help="Mode A: suppress canonical display output.")
def run(
    prompt: str | None,
    session_id: str | None,
    resume: bool,
    fresh: bool,
    stdio: bool,
    idle_timeout: int | None,
    provider_override: str | None,
    bundle: str | None,
    config_path: str | None,
    cwd: str | None,
    verbose: bool,
    debug: bool,
    yes_flag: bool,
    no_flag: bool,
    quiet: bool,
) -> None:
    """Run a single prompt through an Amplifier session (Mode A)."""
    # --- Mode B stub (Phase 3) ---
    if stdio:
        click.echo(
            "[error] --stdio (Mode B) is not yet implemented (Phase 3).",
            err=True,
        )
        sys.exit(1)

    # --- Flag validation (Click's UsageError → exit 2) ---
    if yes_flag and no_flag:
        raise click.UsageError("-y and -n are mutually exclusive")

    # --- stdin discipline ---
    if prompt is None:
        if is_stdin_tty():
            raise click.UsageError("Missing argument 'PROMPT'.")
        click.echo(
            "[error] prompt_required: pass prompt as argument: "
            "`amplifier-agent run \"...\"`. For stdio JSON-RPC, use --stdio.",
            err=True,
        )
        sys.exit(2)

    # --- Provider resolution ---
    try:
        provider = detect_provider(override=provider_override)
    except ProviderNotConfigured as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)

    # --- Protocol-point defaults ---
    approval = CliApprovalSystem(mode=_resolve_approval_mode(yes_flag, no_flag))
    display = CliDisplaySystem(
        verbosity=_resolve_verbosity(quiet, verbose, debug),
        stream=sys.stderr,
    )

    # --- Boot + submit ---
    boot_kwargs: dict[str, Any] = {
        "provider": provider,
        "approval": approval,
        "display": display,
        "session_id": session_id,
        "resume": resume,
        "fresh": fresh,
        "cwd": cwd,
        "bundle_override": bundle,
        "config_path": config_path,
    }

    try:
        engine = Engine.boot(**boot_kwargs)
        result = engine.submit_turn(prompt)
    except AaaError as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)

    click.echo(json.dumps(result, indent=2))
```

**Note on `Engine.boot` signature:** Phase 1 is the source of truth for the actual contract. If Phase 1's signature differs from the `boot_kwargs` above, **adjust this file to match Phase 1, NOT vice versa** — and note the adjustment in the commit message. Do NOT modify `amplifier_agent_lib` to fit this plan.

**Step 4: Run tests to verify pass**
Run: `uv run pytest tests/cli/test_single_turn.py -v`
Expected: 15 passed.

**Step 5: Verify full CLI test suite still passes**
Run: `uv run pytest tests/cli/ -v`
Expected: all green (49+ tests).

**Step 6: Lint and type-check**
Run: `uv run ruff check src/amplifier_agent_cli/modes/single_turn.py tests/cli/test_single_turn.py && uv run pyright src/amplifier_agent_cli/modes/single_turn.py`
Expected: clean.

**Step 7: Commit**
Run:
```
git add src/amplifier_agent_cli/modes/single_turn.py tests/cli/test_single_turn.py
git commit -m "feat(cli): implement Mode A run command with engine wiring"
```

---

## Task 9: End-to-end smoke tests via console-script subprocess

**Files:**
- Create: `tests/cli/test_end_to_end.py`

**Design intent:** ONE high-value test module that exercises the full path with a real subprocess invocation (`uv run amplifier-agent ...`). We do NOT touch real providers — Mode A with a real `run "..."` would require a real engine boot. Instead, this test focuses on what `CliRunner` can't catch: console-script entry-point wiring, import-order surprises, mutating state at module load. We exercise help, version, doctor, config show, cache clear, the Mode-B stub, and the prompt-required path — all of which are pure-Python paths that do NOT require a real provider call.

**Step 1: Write the failing test**

Create `tests/cli/test_end_to_end.py`:
```python
"""End-to-end smoke tests using the installed console script.

These tests spawn `uv run amplifier-agent ...` as real subprocesses so
the console-script entry-point and package wiring are exercised
end-to-end (not just CliRunner's in-process dispatch).
"""
from __future__ import annotations

import json
import os
import subprocess


def _run(
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["uv", "run", "amplifier-agent", *args],
        capture_output=True,
        text=True,
        env=merged_env,
        input=input_text,
        timeout=30,
    )


def test_version_via_console_script() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert "amplifier-agent" in result.stdout.lower()


def test_help_via_console_script() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "doctor" in result.stdout


def test_doctor_runs_to_completion_when_provider_set() -> None:
    result = _run("doctor", env={"ANTHROPIC_API_KEY": "sk-test"})
    # exit 0 (all-green) or 1 (e.g. XDG paths unwritable in some CI environments)
    assert result.returncode in (0, 1)
    assert "python" in result.stdout.lower()


def test_config_show_emits_valid_json_to_stdout() -> None:
    result = _run("config", "show", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert "provider" in parsed


def test_cache_clear_returns_zero() -> None:
    result = _run("cache", "clear", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 0


def test_unknown_command_exits_2() -> None:
    result = _run("bogus-subcommand")
    assert result.returncode == 2


def test_run_with_no_prompt_and_piped_stdin_fails_with_prompt_required() -> None:
    # Pipe empty stdin → stdin is NOT a TTY → prompt_required path
    result = _run("run", env={"ANTHROPIC_API_KEY": "sk-test"}, input_text="")
    assert result.returncode == 2
    assert "prompt" in (result.stderr + result.stdout).lower()


def test_run_stdio_phase_3_stub_exits_1() -> None:
    result = _run("run", "--stdio", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 1
    combined = (result.stderr + result.stdout).lower()
    assert "phase 3" in combined or "stdio" in combined or "not yet implemented" in combined
```

**Step 2: Run test to verify**
Run: `uv run pytest tests/cli/test_end_to_end.py -v`
Expected: 8 passed. If any fail, the failure surfaces a real wiring issue.

**Step 3: If any test fails, fix the underlying wiring (NOT the test)**

Common failure modes and fixes:
- `command not found: amplifier-agent` → console-script not installed. Run `uv sync`. Confirm `[project.scripts]` block in `pyproject.toml` lists `amplifier-agent = "amplifier_agent_cli.__main__:main"`.
- `ModuleNotFoundError` at import time → check `src/amplifier_agent_cli/__main__.py`'s imports; ensure all four handler modules exist and export the expected names (`run`, `doctor`, `config_group`, `cache_group`).
- `doctor` exit code mismatch → some sandboxes can't write to `~/.cache`; that's why the assertion tolerates `(0, 1)`.

**Step 4: Run full test suite to confirm no regression**
Run: `uv run pytest -v`
Expected: all green (Phase 1 + Phase 2 tests).

**Step 5: Lint and type-check full Phase 2 surface**
Run: `uv run ruff check src/amplifier_agent_cli tests/cli && uv run pyright src/amplifier_agent_cli`
Expected: clean.

**Step 6: Commit**
Run:
```
git add tests/cli/test_end_to_end.py
git commit -m "test(cli): end-to-end smoke tests via console-script subprocess"
```

---

## Done — Phase 2 acceptance checklist

Before declaring Phase 2 complete, verify:

- [ ] `uv run amplifier-agent --version` prints the version, exits 0
- [ ] `uv run amplifier-agent --help` lists `run`, `doctor`, `config`, `cache`
- [ ] `uv run amplifier-agent run "hi"` (with `ANTHROPIC_API_KEY` set, Engine mocked in tests) returns JSON on stdout, exits 0
- [ ] `uv run amplifier-agent run --stdio` exits 1 with a Phase-3-stub message on stderr
- [ ] `uv run amplifier-agent doctor` reports provider, XDG paths, Python, bundle cache; exits 0 when all green, 1 if any required check fails
- [ ] `uv run amplifier-agent config show` emits valid JSON with source annotations
- [ ] `uv run amplifier-agent cache clear` removes the prepared cache; idempotent on empty
- [ ] All tests pass: `uv run pytest -v`
- [ ] All lint+types clean: `uv run ruff check src tests && uv run pyright src`
- [ ] stdout discipline: across every Mode A and admin verb, stdout never contains non-JSON diagnostic text
- [ ] No `os.environ` mutation anywhere in `amplifier_agent_cli`
- [ ] KeyboardInterrupt → exit 130 (verified by inspection of `__main__.py:main()`)

Once all boxes are checked, **Phase 2 is complete**. Phase 3 (Mode B / `run --stdio` JSON-RPC loop) is the next plan.

---

## Notes for the implementer

- **Do NOT skip the TDD verify-fail step.** It catches the class of bug where a test passes for the wrong reason (typo in test name, import failed silently, etc.).
- **Do NOT batch commits.** One commit per task. The plan's task boundaries are also the review boundaries.
- **Engine.boot signature is the source of truth from Phase 1.** If Task 8's `boot_kwargs` doesn't match what Phase 1 exposes, adjust this file to Phase 1's contract — note the adjustment in the commit message. Do NOT modify `amplifier_agent_lib` to match this plan.
- **If you see `os.environ["NO_COLOR"] = "1"` anywhere in your diff, delete it.** That's the OpenClaw anti-pattern we explicitly do not replicate.
- **If a test wants to check stderr separately from stdout, use `CliRunner(mix_stderr=False)`.** Default mixes them, which defeats the discipline.
- **Phase 3 (Mode B / stdio) will replace the stub in `single_turn.py`.** Keep the `--stdio` argv plumbing alive but pointed at the clear-failure stub. Phase 3 will route `--stdio` into its own handler module.
