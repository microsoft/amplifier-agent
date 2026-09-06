import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVENTS = {
    "turn_started", "output_delta", "reasoning_delta", "reasoning_final", "tool_call",
    "tool_result", "approval_request", "approval_decision", "progress", "usage", "terminal",
}
OWNED = r"(?:[a-z][a-z0-9-]*\.)+[a-z][a-z0-9_-]*"


def validate_event_literals(source):
    names = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "emit" or not node.args:
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            assert value.value in EVENTS or re.fullmatch(OWNED, value.value), (
                f"Unregistered public event {value.value!r}"
            )
            names.append(value.value)
    return names


def test_public_event_emission_names_reject_unregistered_literals():
    source = ROOT / "packages/engine/src/amplifier_agent_engine/_engine"
    names = set()
    for path in source.glob("*.py"):
        text = path.read_text()
        names.update(validate_event_literals(text))
    assert {"turn_started", "terminal", "tool_call", "tool_result"} <= names
    validate_event_literals('turn.emit("org.example.trace", payload)')
    for name in ("done", "loop_started", "prompt_stack", "context_compacted"):
        with pytest.raises(AssertionError, match="Unregistered public event"):
            validate_event_literals(f'turn.emit("{name}", payload)')


def test_documented_event_vocabulary_matches_the_frozen_registry():
    contract = (ROOT / "contracts/turn-events.v1.md").read_text()
    vocabulary = contract.split("## 2. Vocabulary", 1)[1].split("```text", 1)[1].split("```", 1)[0]
    names = set(re.findall(r"^([a-z_]+)\s+", vocabulary, re.MULTILINE))
    assert names == EVENTS
    assert len(names) == 11


def test_owned_event_names_cannot_shadow_registered_or_internal_names():
    from amplifier_agent_engine._engine.adapters import ProviderHooks

    source = ast.parse(textwrap.dedent(inspect.getsource(ProviderHooks.emit)))
    patterns = [node.args[0].value for node in ast.walk(source)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "fullmatch" and isinstance(node.args[0], ast.Constant)]
    assert len(patterns) == 1

    def validate(pattern):
        for name in EVENTS | {"loop_started", "org..trace", "org.example.", ".trace"}:
            assert re.fullmatch(pattern, name) is None
        for name in ("org.example.trace", "com.example.tool_progress"):
            assert re.fullmatch(pattern, name)

    validate(patterns[0])
    with pytest.raises(AssertionError):
        validate(r".*")
