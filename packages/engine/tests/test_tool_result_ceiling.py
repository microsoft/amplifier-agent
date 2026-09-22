import pytest
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.effects import bounded_result
from amplifier_agent_engine._records import AgentError, AgentOptions

MARKER = "...[tool output reached limit: kept {kept} of {total} bytes]"


def test_content_within_the_ceiling_is_carried_unchanged():
    assert bounded_result("counted", 7) == ("counted", None)
    assert bounded_result("counted", None) == ("counted", None)
    assert bounded_result("", 1) == ("", None)


def test_content_above_the_ceiling_keeps_a_prefix_and_names_the_loss():
    content, original = bounded_result("x" * 100, 10)
    assert original == 100
    assert content == "x" * 10 + "\n" + MARKER.format(kept=10, total=100)


def test_a_character_straddling_the_cut_is_dropped_and_the_marker_counts_kept_bytes():
    content, original = bounded_result("a" + "\u00e9" * 4, 4)
    assert original == 9
    assert content == "a\u00e9" + "\n" + MARKER.format(kept=3, total=9)
    assert len(content.encode()) == 3 + 1 + len(MARKER.format(kept=3, total=9))


@pytest.mark.parametrize("ceiling", [0, -1, True, "big", 1.5])
def test_an_invalid_ceiling_is_refused_at_construction(ceiling):
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(tool_result_max_bytes=ceiling))
    assert caught.value.code == "invalid_input"
    assert caught.value.details == {"field": "tool_result_max_bytes"}
    assert caught.value.remedy == "Set tool_result_max_bytes to a positive integer or None."


@pytest.mark.parametrize("ceiling", [None, 1, 262_144])
def test_a_positive_ceiling_or_none_resolves(ceiling):
    assert resolve(AgentOptions(tool_result_max_bytes=ceiling)).tool_result_max_bytes == ceiling


def test_the_default_ceiling_is_the_contracted_size():
    assert resolve(AgentOptions()).tool_result_max_bytes == 262_144
