"""Package-owned process bootstrap."""

import asyncio

from amplifier_agent_engine._runtime.server import RuntimeServer


def main() -> None:
    asyncio.run(RuntimeServer().serve())


if __name__ == "__main__":
    main()
