import pytest

from tests.support.smart_tools import offline


@pytest.fixture(autouse=True)
def short_retry_backoff(monkeypatch):
    """Provider modules retry transient failures with backoff of a second or more."""
    from amplifier_agent_engine._engine import providers

    monkeypatch.setattr(providers, "RETRY_BACKOFF", {"min_retry_delay": 0.001, "max_retry_delay": 0.001})


@pytest.fixture(autouse=True)
def offline_smart_tools(monkeypatch):
    """Agent construction refreshes the smart tools skill in the background; tests use the bundled copy."""
    offline(monkeypatch)
