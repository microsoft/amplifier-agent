# Streaming Hook Enrichment — Phase 1: Protocol Extension Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Extend the wire-protocol TypedDicts so the streaming hook (Phase 2) has typed slots for cost, timing, model/provider attribution, cache tokens, session-total cost, and sub-agent attribution — then regenerate the JSON Schemas and spec.

**Architecture:** The Python TypedDicts in `src/amplifier_agent_lib/protocol/notifications.py` are the authoritative wire spec. `schemas/*.schema.json` and `spec.md` are GENERATED from them by `_gen.py` and must never be hand-edited. This phase edits two TypedDicts (`UsageNotification`, the two tool notifications) and regenerates the artifacts. All additions are `NotRequired` (optional) except one **type change**: `UsageNotification.cost` moves from `float` to `str` to preserve `Decimal` monetary precision on the wire.

**Tech Stack:** Python 3.12, pytest (strict asyncio mode), `typing.NotRequired`, generated JSON Schema (Draft 2020-12).

---

## Context the implementer must know

### Why `cost` must be `str`, not `float`
The kernel models cost as `decimal.Decimal` and **rejects floats** (`reject_float_cost` validator at `amplifier_core/message_models.py:258`). It serializes cost to a **string** on the wire (`@field_serializer("cost_usd", when_used="always")` at `message_models.py:268`), e.g. `"0.0142"`. Floats accumulate rounding error across many turns — unacceptable for budget enforcement. The existing `UsageNotification.cost: NotRequired[float]` slot (`notifications.py:118`) is therefore wrong. It is currently never populated by any producer, so changing its type breaks no live consumer.

### The generator is the only way to touch schemas/spec
After editing any TypedDict you MUST run:
```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```
A CI gate (`tests/test_protocol_gen_staleness.py`) compares the checked-in `spec.md` and every `schemas/*.schema.json` byte-for-byte against fresh generator output. If you edit a TypedDict and forget to regenerate, that test fails. **Never hand-edit a `.schema.json` or `spec.md`.**

### Field name mapping (kernel snake_case → wire camelCase)
The wire uses camelCase. Phase 2 will map kernel fields to these wire names; Phase 1 only declares the wire names:

| Wire field (new) | Type | Source (Phase 2) |
|---|---|---|
| `cost` | `str` | `usage.cost_usd` (Decimal-as-string) |
| `llmDurationMs` | `int` | `data["duration_ms"]` |
| `model` | `str` | `data["model"]` |
| `provider` | `str` | `data["provider"]` |
| `cacheReadTokens` | `int` | `usage.cache_read_tokens` |
| `cacheWriteTokens` | `int` | `usage.cache_write_tokens` |
| `sessionCostTotal` | `str` | summed `session.cost` contributions |
| `agentName` | `str` | parsed from `session_id` |

---

### Task 1: Add failing introspection test for UsageNotification enrichment fields

**Files:**
- Test: `tests/test_protocol_notifications.py` (append new test)

**Step 1: Write the failing test**

Append to `tests/test_protocol_notifications.py`:

```python
def test_usage_notification_has_enrichment_fields() -> None:
    """UsageNotification must declare cost as NotRequired[str] plus the 7 enrichment fields."""
    from typing import get_args, get_type_hints

    from amplifier_agent_lib.protocol.notifications import UsageNotification

    hints = get_type_hints(UsageNotification, include_extras=True)

    # cost must be a string slot (Decimal-as-string), NOT a float.
    assert "cost" in hints
    assert get_args(hints["cost"]) == (str,), "cost must be NotRequired[str], not float"

    # All new optional fields must be present.
    expected_str_fields = {"model", "provider", "sessionCostTotal", "agentName"}
    expected_int_fields = {"llmDurationMs", "cacheReadTokens", "cacheWriteTokens"}
    for field in expected_str_fields:
        assert field in hints, f"missing {field}"
        assert get_args(hints[field]) == (str,), f"{field} must be NotRequired[str]"
    for field in expected_int_fields:
        assert field in hints, f"missing {field}"
        assert get_args(hints[field]) == (int,), f"{field} must be NotRequired[int]"

    # The two existing required fields remain required.
    assert "inputTokens" in hints
    assert "outputTokens" in hints
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_protocol_notifications.py::test_usage_notification_has_enrichment_fields -v`
Expected: FAIL — `cost` is currently `NotRequired[float]` (so `get_args` returns `(float,)`) and the new fields don't exist yet.

**Step 3: Write the minimal implementation**

In `src/amplifier_agent_lib/protocol/notifications.py`, replace the `UsageNotification` class (currently lines 111–118) with:

```python
class UsageNotification(TypedDict):
    """Token usage and optional cost summary for a turn."""

    sessionId: str
    turnId: str
    inputTokens: int
    outputTokens: int
    # cost is the Decimal-as-string serialization used by the kernel
    # (message_models.py serializes Decimal cost to str). String, not float,
    # to preserve monetary precision across many turns.
    cost: NotRequired[str]
    llmDurationMs: NotRequired[int]
    model: NotRequired[str]
    provider: NotRequired[str]
    cacheReadTokens: NotRequired[int]
    cacheWriteTokens: NotRequired[int]
    sessionCostTotal: NotRequired[str]
    agentName: NotRequired[str]
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_protocol_notifications.py::test_usage_notification_has_enrichment_fields -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_protocol_notifications.py src/amplifier_agent_lib/protocol/notifications.py
git commit -m "feat(streaming-hook): add enrichment fields to UsageNotification

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 2: Fix the existing UsageNotification roundtrip test for the cost type change

**Files:**
- Test: `tests/test_protocol_notifications.py:142-165` (modify existing `test_usage_notification_roundtrip`)

**Why:** The existing test constructs a `UsageNotification` with `"cost": 0.0015` (a float) and asserts `rt2["cost"] == 0.0015`. After Task 1, `cost` is typed `str`; pyright (run in Phase 3) will flag the float literal. Update it to use the string form to match the new contract.

**Step 1: Update the test (this is a type-contract fix, not new behavior)**

In `tests/test_protocol_notifications.py`, replace the `event_with_cost` block at the end of `test_usage_notification_roundtrip` (currently lines 157–165):

```python
    event_with_cost: UsageNotification = {
        "sessionId": "s",
        "turnId": "t",
        "inputTokens": 100,
        "outputTokens": 200,
        "cost": "0.0015",
    }
    rt2 = json.loads(json.dumps(event_with_cost))
    assert rt2["cost"] == "0.0015"
```

**Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/test_protocol_notifications.py::test_usage_notification_roundtrip -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_protocol_notifications.py
git commit -m "test(streaming-hook): update usage roundtrip for cost str contract

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 3: Add failing test for `agentName` on tool notifications

**Files:**
- Test: `tests/test_protocol_notifications.py` (append new test)

**Step 1: Write the failing test**

Append to `tests/test_protocol_notifications.py`:

```python
def test_tool_notifications_have_agent_name_field() -> None:
    """ToolStarted and ToolCompleted must declare agentName as NotRequired[str]."""
    from typing import get_args, get_type_hints

    from amplifier_agent_lib.protocol.notifications import (
        ToolCompletedNotification,
        ToolStartedNotification,
    )

    for td in (ToolStartedNotification, ToolCompletedNotification):
        hints = get_type_hints(td, include_extras=True)
        assert "agentName" in hints, f"{td.__name__} missing agentName"
        assert get_args(hints["agentName"]) == (str,), f"{td.__name__}.agentName must be NotRequired[str]"
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_protocol_notifications.py::test_tool_notifications_have_agent_name_field -v`
Expected: FAIL — `agentName` not declared on either notification.

**Step 3: Write the minimal implementation**

In `src/amplifier_agent_lib/protocol/notifications.py`, add `agentName: NotRequired[str]` as the last field of both `ToolStartedNotification` (currently lines 65–72) and `ToolCompletedNotification` (currently lines 75–83):

```python
class ToolStartedNotification(TypedDict):
    """Emitted when a tool call begins execution."""

    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    args: dict
    agentName: NotRequired[str]


class ToolCompletedNotification(TypedDict):
    """Emitted when a tool call finishes execution."""

    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    result: Any
    durationMs: int
    agentName: NotRequired[str]
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_protocol_notifications.py::test_tool_notifications_have_agent_name_field -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_protocol_notifications.py src/amplifier_agent_lib/protocol/notifications.py
git commit -m "feat(streaming-hook): add agentName to tool notifications

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 4: Regenerate the JSON Schemas and spec.md

**Files:**
- Regenerate: `src/amplifier_agent_lib/protocol/spec.md`
- Regenerate: `src/amplifier_agent_lib/protocol/schemas/UsageNotification.schema.json`
- Regenerate: `src/amplifier_agent_lib/protocol/schemas/ToolStartedNotification.schema.json`
- Regenerate: `src/amplifier_agent_lib/protocol/schemas/ToolCompletedNotification.schema.json`

**Step 1: Run the generator**

Run:
```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```
Expected output: two `[gen] wrote ...` lines, exit code 0.

**Step 2: Verify the regenerated UsageNotification schema**

Run: `cat src/amplifier_agent_lib/protocol/schemas/UsageNotification.schema.json`
Expected: the `cost` property is now `{"type": "string"}` (was `{"type": "number"}`), and `llmDurationMs`, `model`, `provider`, `cacheReadTokens`, `cacheWriteTokens`, `sessionCostTotal`, `agentName` all appear under `properties`. The `required` array still contains only `inputTokens`, `outputTokens`, `sessionId`, `turnId`.

**Step 3: Verify the git diff is limited to expected files**

Run: `git status --short`
Expected: modified `spec.md`, `UsageNotification.schema.json`, `ToolStartedNotification.schema.json`, `ToolCompletedNotification.schema.json`. No other schema files should change.

**Step 4: Commit**

```bash
git add src/amplifier_agent_lib/protocol/spec.md src/amplifier_agent_lib/protocol/schemas/
git commit -m "feat(streaming-hook): regenerate protocol schemas + spec for enrichment

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 5: Verify the staleness gate and protocol tests are green

**Files:** none (verification only)

**Step 1: Run the staleness gate**

Run: `uv run pytest tests/test_protocol_gen_staleness.py -v`
Expected: PASS — confirms checked-in artifacts match generator output. (If this fails, you forgot to regenerate in Task 4 or hand-edited a generated file. Re-run the generator from Task 4 Step 1.)

**Step 2: Run the full protocol test suite**

Run: `uv run pytest tests/test_protocol_notifications.py tests/test_protocol_gen.py tests/test_protocol_gen_staleness.py -v`
Expected: PASS — all notification, generator, and staleness tests pass.

---

### Task 6: Quality gate for Phase 1

**Files:** none (verification only)

**Step 1: Type-check and lint the changed files**

Run: `uv run ruff format src/amplifier_agent_lib/protocol/notifications.py tests/test_protocol_notifications.py && uv run ruff check src/amplifier_agent_lib/protocol/notifications.py tests/test_protocol_notifications.py && uv run pyright src/amplifier_agent_lib/protocol/notifications.py`
Expected: no errors. (If ruff format makes changes, re-run the commit for the formatted files.)

**Step 2: Run the broader protocol + conformance suite to catch fallout**

Run: `uv run pytest tests/test_protocol_conformance_fixtures.py tests/test_conformance_fixtures_freshness.py -v`
Expected: PASS. Adding optional schema fields does not invalidate existing conformance fixtures (they omit the new fields, which are `NotRequired`). If a fixture-freshness test fails, STOP and report — do not blindly regenerate fixtures; the additive change should not require it.

**Phase 1 complete.** Proceed to Phase 2 only when all of the above is green.
