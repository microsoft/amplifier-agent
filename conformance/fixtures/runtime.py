"""Launch the owned connection with the kit's scripted model responses."""

from conformance.fixtures.scripted_provider import install


def main() -> None:
    install()
    from amplifier_agent_engine._runtime.__main__ import main as runtime_main

    runtime_main()


if __name__ == "__main__":
    main()
