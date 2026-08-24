"""Case data for the hello suite: a real model turn on Windows.

Needs ANTHROPIC_API_KEY on the host, forwarded into the container. This is the
only suite that spends a model call, and it exists to prove the whole path
works end to end: provider config, network, the agent loop, and stdout
carrying the JSON envelope back out of a Windows container.
"""

from __future__ import annotations

from winframework.assertions import expect_contains
from winframework.harness import WinCase

# Baked into the image by the Dockerfile. Anthropic provider, approvals auto.
CONFIG = "C:/e2e/host-config.json"

HELLO: list[WinCase] = [
    WinCase(
        "hello-generates-output",
        command=["run", "-y", "--config", CONFIG, "Reply with exactly the word: hello"],
        check=expect_contains("hello"),
    ),
]
