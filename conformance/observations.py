"""Assertions shared by fixture discrimination and the public scenario drivers."""

from typing import Any


def verify(case: dict[str, Any], observed: dict[str, Any]) -> None:
    for key, value in case["expected"].items():
        assert observed.get(key) == value, (
            f"{case['id']}: {key}: {observed.get(key)!r} != {value!r}"
        )
    events = observed["events"]
    assert events[0]["type"] == "turn_started"
    assert events[-1]["type"] == "terminal"
    assert sum(event["type"] == "terminal" for event in events) == 1
    assert sum(event["type"] == "turn_started" for event in events) == 1
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert len({(event["session_id"], event["turn_id"]) for event in events}) == 1
    assert all(event["contract_version"] == "turn-events/1" for event in events)
    assert all(pid == observed["pid"] for pid in observed["callback_pids"])
    assert observed["active_provider"] == 0
    if observed["state"] == "success":
        assert "".join(observed["deltas"]) == observed["text"]
        assert observed["delta_parts"] == observed["content"]
    else:
        assert observed["error"]["message"]
        assert observed["error"]["remedy"]
        assert isinstance(observed["error"]["retryable"], bool)


def discriminate(case: dict[str, Any], observed: dict[str, Any]) -> None:
    import copy

    verify(case, observed)
    mutants = []
    missing_terminal = copy.deepcopy(observed)
    missing_terminal["events"].pop()
    mutants.append(missing_terminal)
    wrong_sequence = copy.deepcopy(observed)
    wrong_sequence["events"][0]["sequence"] = 0
    mutants.append(wrong_sequence)
    abandoned = copy.deepcopy(observed)
    abandoned["active_provider"] = 1
    mutants.append(abandoned)
    wrong_effects = copy.deepcopy(observed)
    wrong_effects["effects"] += 1
    mutants.append(wrong_effects)
    if observed["callback_pids"]:
        outside = copy.deepcopy(observed)
        outside["callback_pids"][0] += 1
        mutants.append(outside)
    if "deltas" in case["expected"]:
        altered = copy.deepcopy(observed)
        altered["deltas"] = ["wrong"]
        mutants.append(altered)
    if len(observed.get("content", [])) > 1:
        merged = copy.deepcopy(observed)
        merged["content"] = [{"type": "text", "text": observed["text"]}]
        mutants.append(merged)
    for mutant in mutants:
        try:
            verify(case, mutant)
        except (AssertionError, IndexError):
            continue
        raise AssertionError(f"{case['id']}: accepted a broken observation")
