"""Run an isolated provider protocol with an append-only request ledger."""

import asyncio
import json
import os
import sys
from pathlib import Path

from .http_server import socket_server
from .provider_services import provider_service


class RequestLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, value: dict) -> None:
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(value, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())


async def main() -> None:
    provider, path = sys.argv[1:]
    application = provider_service(
        provider, RequestLedger(Path(path)), tool="counter", reasoning=True
    )
    async with socket_server(application) as url:
        print(json.dumps({"kind": "provider", "url": url}), flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
