"""Keep the background refresh of the smart tools skill off the network in tests."""

import threading


def unreachable(url: str, accept: str, limit: int, stop: threading.Event) -> bytes:
    raise ConnectionRefusedError("Tests never fetch the smart tools skill; the bundled copy is used.")


def offline(monkeypatch) -> None:
    from amplifier_agent_engine._engine import smart_tools

    monkeypatch.setattr(smart_tools, "get", unreachable)


def install() -> None:
    """Replace the network access for the whole test runtime process."""
    from amplifier_agent_engine._engine import smart_tools

    setattr(smart_tools, "get", unreachable)
