"""Fixtures for the GitHub Copilot suite: seed per-model configs + the tool-call target.

The CLI cases select a model through ``provider.config.default_model`` in a host-config
file (there is no ``--model`` flag), so each model under test needs its own config
pushed into the DTU. The tool-call cases additionally need a real file on disk inside
the container for the model to read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from framework import dtu

from suites.github_copilot.cases import DTU_DIR, MODELS, SECRET_PATH, config_path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def ghcp_configs(dtu_id: str) -> dict[str, str]:
    """Push one host-config per model into the DTU. Returns {slug: in-DTU config path}.

    Session-scoped: the files are immutable and every case in the suite reads them, so
    pushing once per run is enough.
    """
    pushed: dict[str, str] = {}
    for slug, _model in MODELS:
        dest = config_path(slug)
        dtu.push_file(dtu_id, str(FIXTURES / f"host-config-ghcp-{slug}.json"), dest)
        pushed[slug] = dest
    return pushed


@pytest.fixture(scope="session")
def ghcp_secret(dtu_id: str) -> str:
    """Push the tool-call target file into the DTU. Returns its in-DTU path.

    The nonce inside it is the only evidence a tool call actually ran, so this must
    exist before any TOOLCALL case runs -- a missing file would make the case fail for
    a reason unrelated to the provider.
    """
    dtu.push_file(dtu_id, str(FIXTURES / "secret.txt"), SECRET_PATH)
    return SECRET_PATH


@pytest.fixture(scope="session")
def ghcp_env(ghcp_configs: dict[str, str], ghcp_secret: str) -> dict[str, str]:
    """Everything the suite needs seeded into ``DTU_DIR``, in one dependency."""
    return {"dir": DTU_DIR, "secret": ghcp_secret, **ghcp_configs}
