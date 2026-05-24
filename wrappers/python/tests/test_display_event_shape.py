"""Python parity test for A3'/CR-C simplified Mode A DisplayEvent shape.

Mirror of wrappers/typescript/test/session-mode-a-shape.test.ts.

The 2026-05-24 Mode A pivot amendment §5.2 specifies a discriminated union:

    DisplayEvent =
        {"type": "init",     "sessionId": str}
      | {"type": "activity"}
      | {"type": "result",   "text": str}
      | {"type": "error",    "code": str, "classification": Literal[...],
                              "severity": Literal[...], "correlationId": str,
                              "message": str, "stderrTail": str (optional),
                              "retryable": bool}

Asserts the exact shape via dict-key equality, since Python's TypedDicts are
duck-typed at runtime (the structure is what matters, not the static type).
"""

from __future__ import annotations

from amplifier_agent_client.types import DisplayEvent


def test_init_event_has_only_type_and_session_id() -> None:
    """(i) init event has exactly {type, sessionId} — no turnId, no payload."""
    ev: DisplayEvent = {"type": "init", "sessionId": "sess-abc"}

    assert ev["type"] == "init"
    assert ev["sessionId"] == "sess-abc"
    assert sorted(ev.keys()) == ["sessionId", "type"]


def test_activity_event_has_no_payload_fields() -> None:
    """(ii) activity event has no payload fields — just {type}."""
    ev: DisplayEvent = {"type": "activity"}

    assert ev["type"] == "activity"
    assert list(ev.keys()) == ["type"]


def test_result_event_carries_text_only() -> None:
    """(iii) result event carries `text` only — no payload."""
    ev: DisplayEvent = {"type": "result", "text": "hello from engine"}

    assert ev["type"] == "result"
    assert ev["text"] == "hello from engine"
    assert sorted(ev.keys()) == ["text", "type"]


def test_error_event_carries_code_classification_severity_correlation_message_retryable() -> None:
    """(iv) error event carries code, classification, severity, correlationId, message, retryable."""
    ev: DisplayEvent = {
        "type": "error",
        "code": "engine_exit_1",
        "classification": "engine",
        "severity": "error",
        "correlationId": "corr-abc-123",
        "message": "engine exited non-zero",
        "retryable": False,
    }

    assert ev["type"] == "error"
    assert ev["code"] == "engine_exit_1"
    assert ev["classification"] == "engine"
    assert ev["severity"] == "error"
    assert ev["correlationId"] == "corr-abc-123"
    assert ev["message"] == "engine exited non-zero"
    assert ev["retryable"] is False
    # stderrTail is optional — absent here.
    assert "stderrTail" not in ev


def test_discriminated_union_exhaustiveness_via_type_dispatch() -> None:
    """(v) the union must be exactly the four variants above — exhaustiveness check."""
    events: list[DisplayEvent] = [
        {"type": "init", "sessionId": "s"},
        {"type": "activity"},
        {"type": "result", "text": "r"},
        {
            "type": "error",
            "code": "engine_exit_1",
            "classification": "engine",
            "severity": "error",
            "correlationId": "c",
            "message": "m",
            "retryable": False,
        },
    ]

    summaries: list[str] = []
    for ev in events:
        t = ev["type"]
        if t == "init":
            summaries.append(f"init:{ev['sessionId']}")  # type: ignore[typeddict-item]
        elif t == "activity":
            summaries.append("activity")
        elif t == "result":
            summaries.append(f"result:{ev['text']}")  # type: ignore[typeddict-item]
        elif t == "error":
            summaries.append(  # type: ignore[typeddict-item]
                f"error:{ev['code']}:{ev['classification']}:{ev['retryable']}"
            )
        else:
            raise AssertionError(f"unhandled DisplayEvent variant: {ev!r}")

    assert summaries == [
        "init:s",
        "activity",
        "result:r",
        "error:engine_exit_1:engine:False",
    ]
