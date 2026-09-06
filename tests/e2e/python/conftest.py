import os

import pytest


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    host = tmp_path / "host.json"
    host.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
    monkeypatch.setenv("AMPLIFIER_AGENT_STORAGE", str(tmp_path / "storage"))
