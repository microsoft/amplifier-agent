"""Serve the Agent Client Protocol on stdin and stdout, with host settings from the environment."""

import asyncio
import logging
import sys
from typing import cast

from acp import Agent, run_agent

from amplifier_agent_acp._agent import AcpAgent


def main() -> None:
    # stdout carries only protocol messages.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(run_agent(cast(Agent, AcpAgent())))


if __name__ == "__main__":
    main()
