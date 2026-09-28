from amplifier_agent_engine._engine.effects import bounded_result

MARKER = "...[tool output reached limit: kept {kept} of {total} bytes]"


def test_a_character_straddling_the_cut_is_dropped_and_the_marker_counts_kept_bytes():
    content, original = bounded_result("a" + "\u00e9" * 4, 4)
    assert original == 9
    assert content == "a\u00e9" + "\n" + MARKER.format(kept=3, total=9)
    assert len(content.encode()) == 3 + 1 + len(MARKER.format(kept=3, total=9))
