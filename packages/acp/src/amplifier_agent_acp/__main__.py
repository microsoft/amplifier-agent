"""Serve the Agent Client Protocol on stdin and stdout, with host settings from the environment."""

import asyncio
import logging
import sys

from amplifier_agent_acp._agent import serve


def main() -> None:
    # stdout carries only protocol messages.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
