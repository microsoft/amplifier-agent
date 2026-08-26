"""Fixtures for the usage suite: install the Python wrapper SDK and seed its driver.

The envelope half of this suite needs nothing beyond the engine, which the DTU profile
already installs. The wrapper half needs ``amplifier_agent_py`` importable INSIDE the
container, built from the same working tree the engine came from -- otherwise the tests
would be checking whatever version of the SDK happens to be published, and a local
change to the wrapper would not be exercised at all.

That is what the ``#subdirectory=wrappers/python-py`` fragment buys: the DTU's
``url_rewrites`` rule matches ``github.com/microsoft/amplifier-agent`` on a boundary
match, so the URL is redirected to the in-DTU Gitea mirror of the local tree and the
fragment survives to select the wrapper package within it.

Both fixtures fail LOUDLY rather than skipping. A silent skip here would turn "the SDK
could not be installed" into "the usage tests did not run", which reads as green.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# Host config seeded by DTU provisioning (framework/provisioning/host-config.json):
# anthropic provider, claude-sonnet-5, approval mode yes. Nothing about this suite
# needs a bespoke config, so it reuses that one.
HOST_CONFIG = "/root/e2e/host-config.json"

# In-DTU location of the wrapper driver.
DRIVER_DEST = "/root/e2e/usage_driver.py"

# Installed from the SAME mirror the engine came from, so the wrapper under test is the
# local working tree's wrapper.
#
# ``--break-system-packages`` is required because Ubuntu's system interpreter ships a
# PEP 668 ``EXTERNALLY-MANAGED`` marker, which uv honours and refuses ``--system``
# against. The container is disposable and single-purpose, so relaxing that guard here
# costs nothing.
WRAPPER_SPEC = "git+https://github.com/microsoft/amplifier-agent#subdirectory=wrappers/python-py"
WRAPPER_INSTALL_CMD = f"uv pip install --system --break-system-packages {shlex.quote(WRAPPER_SPEC)}"


@pytest.fixture(scope="session")
def wrapper_sdk(dtu_id: str) -> None:
    """Install ``amplifier_agent_py`` into the DTU's system interpreter and verify it.

    The verification is a real import rather than a check of the installer's exit code:
    a resolver can report success while producing a package that does not import (wrong
    subdirectory, missing dependency), and every wrapper test would then fail with an
    import error that looks nothing like the feature being absent.
    """
    install = dtu.exec_json(dtu_id, ["bash", "-lc", WRAPPER_INSTALL_CMD])
    if install.get("exit_code") != 0:
        pytest.fail(
            "installing the Python wrapper SDK into the DTU failed.\n"
            f"command: {WRAPPER_INSTALL_CMD}\n"
            f"exit_code: {install.get('exit_code')}\n"
            f"stdout:\n{install.get('stdout', '')}\n"
            f"stderr:\n{install.get('stderr', '')}"
        )

    verify = dtu.exec_json(dtu_id, ["python3", "-c", "import amplifier_agent_py"])
    if verify.get("exit_code") != 0:
        pytest.fail(
            "the wrapper SDK installed but does not import in the DTU.\n"
            f"exit_code: {verify.get('exit_code')}\n"
            f"stdout:\n{verify.get('stdout', '')}\n"
            f"stderr:\n{verify.get('stderr', '')}"
        )


@pytest.fixture(scope="session")
def engine_bin(dtu_id: str) -> str:
    """Resolve the engine binary path inside the DTU.

    ``uv tool install`` puts ``amplifier-agent`` on PATH, so ``shutil.which`` in the
    wrapper would find it unaided. Resolving it here anyway and pinning
    ``AMPLIFIER_AGENT_BIN`` in the driver removes binary discovery as a possible
    explanation for a failed usage assertion.
    """
    result = dtu.exec_json(dtu_id, ["bash", "-lc", "command -v amplifier-agent"])
    path = result.get("stdout", "").strip()
    if result.get("exit_code") != 0 or not path:
        pytest.fail(
            "amplifier-agent is not on PATH inside the DTU; the wrapper has no engine to spawn.\n"
            f"exit_code: {result.get('exit_code')}\nstderr:\n{result.get('stderr', '')}"
        )
    return path


@pytest.fixture(scope="session")
def usage_driver(dtu_id: str, wrapper_sdk: None) -> str:
    """Push the wrapper driver into the DTU; return its in-DTU path.

    ``wrapper_sdk`` is requested for ordering only: the driver is useless until the SDK
    it imports is installed, and depending on it here means a failed install is reported
    once, by the fixture that owns it.
    """
    del wrapper_sdk
    dtu.push_file(dtu_id, str(FIXTURES / "usage_driver.py"), DRIVER_DEST)
    return DRIVER_DEST
