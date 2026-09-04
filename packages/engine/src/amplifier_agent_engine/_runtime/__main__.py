"""Package-owned process bootstrap."""

import asyncio

from .server import RuntimeServer


def main() -> None:
    asyncio.run(RuntimeServer().serve())


if __name__ == "__main__":
    main()
