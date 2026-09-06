"""MCP executors with an independently observable effect ledger."""

import argparse
import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("effect-ledger")


@server.tool()
def record(value: str) -> str:
    """Append a value to the server's ledger."""
    with Path(os.environ["MCP_LEDGER"]).open("a") as stream:
        stream.write(json.dumps({
            "value": value, "pid": os.getpid(), "captured": os.environ.get("MCP_CAPTURED"),
        }) + "\n")
    return value


@server.tool()
def fail() -> str:
    """Report an authoritative failure."""
    raise ValueError("The ledger rejected this operation.")


@server.tool()
def uncertain(value: str) -> str:
    """Lose the server after recording an effect."""
    record(value)
    os._exit(17)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run()
    else:
        server.run("streamable-http", host="127.0.0.1", port=args.port, json_response=True)
