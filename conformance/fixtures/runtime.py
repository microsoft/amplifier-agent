"""Launch the owned connection with the kit's scripted model responses."""

from conformance.fixtures.scripted_provider import install


def main() -> None:
    from conformance.fixtures.bootstrap import install as install_bootstrap

    install_bootstrap()
    install()
    from conformance.fixtures.carriage import install as install_carriage

    install_carriage()
    from amplifier_agent_engine._engine import effects

    effects.APPROVAL_TIMEOUT_SECONDS = 0.25
    from amplifier_agent_engine._runtime.__main__ import main as runtime_main

    runtime_main()


if __name__ == "__main__":
    main()
