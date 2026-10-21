import os

import pytest

from tests.support.credentials import anthropic_only


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    host = tmp_path / "host.json"
    host.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # An unset provider defaults from credentials; an Anthropic key alone keeps it anthropic.
    anthropic_only(monkeypatch)
    return host
