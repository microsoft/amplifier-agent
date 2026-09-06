import os

import pytest


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AMPLIFIER_AGENT_STORAGE", str(tmp_path / "storage"))
