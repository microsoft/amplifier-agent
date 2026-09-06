"""Launch the owned connection with the independent deterministic engine."""


def main() -> None:
    from conformance.fixtures.bootstrap import install

    install()
    from amplifier_agent_engine._engine import assembly
    from amplifier_agent_engine._runtime.__main__ import main as runtime_main

    from conformance.fixtures.replacement import create_engine

    assembly.create_engine = create_engine
    runtime_main()


if __name__ == "__main__":
    main()
