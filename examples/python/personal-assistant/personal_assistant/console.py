"""Terminal output: what the user should read on stdout, notes about the assistant itself on stderr."""

import sys


def say(message: str) -> None:
    print(message, flush=True)


def note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
