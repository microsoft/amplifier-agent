"""YAML wire-sequence fixture loader and structural validator.

See ``loader.load_fixture`` for the canonical fixture shape.  Loaders in
other languages (TS, future Go) MUST agree on this shape — see the
conformance fixtures themselves as the authoritative examples.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = ("name", "setup", "script", "assertions")
_VALID_DIRECTIONS: frozenset[str] = frozenset({"client_to_server", "server_to_client"})
_VALID_ASSERTION_KINDS: frozenset[str] = frozenset(
    {
        "response_matches",
        "error_returned",
        "notification_emitted",
        "no_notification",
        "notification_order",
        "session_state",
    }
)


class FixtureValidationError(ValueError):
    """Raised when a fixture file violates the canonical shape."""


@dataclass(frozen=True)
class Fixture:
    """A loaded, structurally-validated wire-sequence fixture."""

    name: str
    description: str
    setup: dict[str, Any]
    script: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    source_path: Path


def load_fixture(path: Path | str) -> Fixture:
    """Load and structurally validate a wire-sequence YAML fixture.

    Canonical fixture shape::

        # <fixture-name>.yaml — one scenario per file
        name: <kebab-case-scenario-name>
        description: One-sentence summary of the contract under test.

        setup:
          protocolVersion: "0.2.0"
          clientCapabilities: { ... }  # subset of capabilities/ClientCapabilities shape
          serverCapabilities: { ... }  # optional override; defaults to server_default_capabilities()

        script:
          # Ordered list of wire frames. Each frame is one of:
          #   {direction: client_to_server, method: "<rpc>", params: {...}, id: <int>}
          #   {direction: server_to_client, result: {...}, id: <int>}     # response to a prior id
          #   {direction: server_to_client, method: "<notif>", params: {...}}  # notification
          #   {direction: server_to_client, error: {...}, id: <int>}      # error response
          - direction: client_to_server
            method: initialize
            id: 1
            params: { ... }

        assertions:
          - kind: response_matches
            id: 1
            result: { ... }
          - kind: notification_emitted
            method: result/final
            payload_contains: { synthesized: true }
          - kind: no_notification
            method: tool/started

    Performs SHAPE validation only — does NOT execute the script or
    verify JSON-Schema conformance of any payload.  Wrapper harnesses
    (Plan 3) execute scripts and check assertions.

    Raises:
        FixtureValidationError: if any structural rule is violated.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise FixtureValidationError(f"{path}: top-level must be a mapping")

    missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in raw]
    if missing:
        raise FixtureValidationError(f"{path}: missing top-level keys: {missing}")

    if not isinstance(raw["script"], list) or not raw["script"]:
        raise FixtureValidationError(f"{path}: script must be a non-empty list")
    for i, frame in enumerate(raw["script"]):
        if not isinstance(frame, dict) or "direction" not in frame:
            raise FixtureValidationError(f"{path}: script[{i}] missing 'direction'")
        if frame["direction"] not in _VALID_DIRECTIONS:
            raise FixtureValidationError(
                f"{path}: script[{i}] direction {frame['direction']!r} not in {sorted(_VALID_DIRECTIONS)}"
            )

    if not isinstance(raw["assertions"], list) or not raw["assertions"]:
        raise FixtureValidationError(f"{path}: assertions must be a non-empty list")
    for i, assertion in enumerate(raw["assertions"]):
        if not isinstance(assertion, dict) or "kind" not in assertion:
            raise FixtureValidationError(f"{path}: assertions[{i}] missing 'kind'")
        kind = assertion["kind"]
        if kind not in _VALID_ASSERTION_KINDS:
            raise FixtureValidationError(
                f"{path}: assertions[{i}] kind {kind!r} not in {sorted(_VALID_ASSERTION_KINDS)}"
            )

    return Fixture(
        name=raw["name"],
        description=raw.get("description", ""),
        setup=raw["setup"],
        script=raw["script"],
        assertions=raw["assertions"],
        source_path=path,
    )
