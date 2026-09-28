"""Test runtime entry: the owned connection with scripted model responses."""

from tests.support.scripted_provider import install


def main() -> None:
    from tests.support.bootstrap import install as install_bootstrap

    install_bootstrap()
    install()
    from tests.support.carriage import install as install_carriage

    install_carriage()
    from amplifier_agent_engine._engine import effects

    setattr(effects, "APPROVAL_TIMEOUT_SECONDS", 0.25)
    from amplifier_agent_engine._runtime.__main__ import main as runtime_main

    runtime_main()


if __name__ == "__main__":
    main()
