"""Import shims so the harness tests run with a plain `python3 -m pytest tests/`.

Two shims, both deliberately narrow:

1. ``src/`` goes on ``sys.path`` so ``deepswe_agents`` imports without an
   editable install.

2. ``pier`` is stubbed IF AND ONLY IF it is not importable in the interpreter
   running pytest. pier is installed as a `uv tool`, i.e. into its own venv, so
   the obvious developer command would otherwise die at import time before a
   single assertion ran. When the real pier IS importable it is used unchanged,
   so these tests keep their full value in a pier-equipped interpreter.

The stub exists only to satisfy MODULE-LEVEL imports in ``deepswe_agents.base``.
The tests exercise the agent's own teardown methods -- shielding, timeouts,
cancellation -- and never call into pier, so nothing here is load-bearing for
what is being asserted. If a test ever needs real pier behaviour, it must run
under an interpreter that has pier rather than gaining a richer fake.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _pier_importable() -> bool:
    try:
        return importlib.util.find_spec("pier") is not None
    except (ImportError, ValueError):
        return False


def _module(name: str) -> types.ModuleType:
    """Create, register, and attach a stub module to its parent package."""
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined] - make it importable as a package
    sys.modules[name] = mod
    parent, _, leaf = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], leaf, mod)
    return mod


def _install_pier_stub() -> None:
    for name in (
        "pier",
        "pier.agents",
        "pier.agents.installed",
        "pier.agents.installed.base",
        "pier.environments",
        "pier.environments.base",
        "pier.models",
        "pier.models.agent",
        "pier.models.agent.context",
        "pier.models.agent.install",
        "pier.models.agent.network",
    ):
        _module(name)

    class BaseInstalledAgent:
        """Stand-in base. Real pier plumbing is never exercised by these tests."""

    class BaseEnvironment:
        """Stand-in environment type (tests pass their own fakes)."""

    class AgentContext:
        """Mirrors the four fields the metrics pass populates."""

        def __init__(self, **kwargs: object) -> None:
            self.n_input_tokens = None
            self.n_cache_tokens = None
            self.n_output_tokens = None
            self.cost_usd = None
            for key, value in kwargs.items():
                setattr(self, key, value)

        def is_empty(self) -> bool:
            return all(value is None for value in vars(self).values())

    class _Record:
        """Permissive stand-in for the pydantic models used at import time."""

        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    sys.modules["pier.agents.installed.base"].BaseInstalledAgent = BaseInstalledAgent  # type: ignore[attr-defined]
    sys.modules["pier.environments.base"].BaseEnvironment = BaseEnvironment  # type: ignore[attr-defined]
    sys.modules["pier.models.agent.context"].AgentContext = AgentContext  # type: ignore[attr-defined]
    sys.modules["pier.models.agent.install"].InstallStep = _Record  # type: ignore[attr-defined]
    sys.modules["pier.models.agent.install"].AgentInstallSpec = _Record  # type: ignore[attr-defined]
    sys.modules["pier.models.agent.network"].NetworkAllowlist = _Record  # type: ignore[attr-defined]


PIER_IS_REAL = _pier_importable()
if not PIER_IS_REAL:
    _install_pier_stub()
