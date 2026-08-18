"""Unit tests for run.py's `_record_judge_attribution` -- run-level provenance.

A top-level `judge_model` in the run manifest is a claim that this judge
produced EVERY score in the run. The failure mode being pinned is the same
one grading.py guards at the per-trial level, one layer up: a run graded by
judge A, then partially re-graded by judge B, must not end up with judge A's
run-level claim still standing over a mix of scores neither judge fully
produced.

The helper is pure dict surgery -- no filesystem, no judge, no API calls, no
benchmark content. The manifests here are synthetic.
"""

from __future__ import annotations

import run
from run import _JUDGE_ATTRIBUTION_KEYS, _record_judge_attribution


def test_complete_pass_claims_the_judge():
    manifest: dict = {"run_id": "synthetic"}
    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=0)

    assert manifest["grading_complete"] is True
    assert manifest["judge_model"] == "judge-b"
    assert manifest["judge_reasoning_effort"] == run.JUDGE_REASONING_EFFORT
    # A complete pass makes no partial-pass claims.
    assert "attempted_judge_model" not in manifest
    assert "ungraded_trials" not in manifest


def test_partial_pass_records_attempt_not_a_claim():
    manifest: dict = {"run_id": "synthetic"}
    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=2)

    assert manifest["grading_complete"] is False
    assert manifest["attempted_judge_model"] == "judge-b"
    assert manifest["attempted_judge_reasoning_effort"] == run.JUDGE_REASONING_EFFORT
    assert manifest["ungraded_trials"] == 2
    # The bare key is the whole-run claim; a partial pass has not earned it.
    assert "judge_model" not in manifest


def test_failed_regrade_removes_the_previous_judges_claim():
    """The important one: judge A graded the run completely, judge B then
    re-grades and misses 3 trials. Judge A's top-level `judge_model` must be
    REMOVED, not left standing -- otherwise the manifest asserts judge A
    graded every score in a run that judge B has since partially rewritten.
    """
    manifest: dict = {"run_id": "synthetic"}
    _record_judge_attribution(manifest, judge_model="judge-a", ungraded=0)
    assert manifest["judge_model"] == "judge-a"

    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=3)

    assert "judge_model" not in manifest, "judge A's whole-run claim survived a partial re-grade"
    assert "judge_reasoning_effort" not in manifest
    assert manifest["grading_complete"] is False
    assert manifest["attempted_judge_model"] == "judge-b"
    assert manifest["ungraded_trials"] == 3


def test_recovered_regrade_clears_the_partial_markers():
    """The reverse direction: a partial pass followed by a complete one must
    not leave `ungraded_trials`/`attempted_judge_model` behind contradicting
    the new complete claim.
    """
    manifest: dict = {"run_id": "synthetic"}
    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=3)
    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=0)

    assert manifest["judge_model"] == "judge-b"
    assert manifest["grading_complete"] is True
    assert "attempted_judge_model" not in manifest
    assert "attempted_judge_reasoning_effort" not in manifest
    assert "ungraded_trials" not in manifest


def test_every_owned_key_is_rewritten_wholesale():
    """Whatever a previous pass wrote, none of the owned keys may survive
    untouched into the next pass. Pinned against the key list itself so a
    newly-added attribution key cannot be forgotten in the reset loop.
    """
    manifest: dict = {key: "stale-value-from-a-previous-pass" for key in _JUDGE_ATTRIBUTION_KEYS}
    manifest["run_id"] = "synthetic"

    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=1)

    for key, value in manifest.items():
        if key == "run_id":
            continue
        assert value != "stale-value-from-a-previous-pass", f"{key} was not rewritten"


def test_unrelated_manifest_fields_are_untouched():
    manifest: dict = {
        "run_id": "synthetic",
        "agents": ["synthetic-agent"],
        "split": "easy",
        "trials": 4,
    }
    _record_judge_attribution(manifest, judge_model="judge-b", ungraded=0)

    assert manifest["run_id"] == "synthetic"
    assert manifest["agents"] == ["synthetic-agent"]
    assert manifest["split"] == "easy"
    assert manifest["trials"] == 4


def test_grading_complete_is_always_written():
    """Present on both branches, so 'was this run fully graded' is never
    answered by the absence of a key.
    """
    for ungraded in (0, 1, 99):
        manifest: dict = {}
        _record_judge_attribution(manifest, judge_model="judge-b", ungraded=ungraded)
        assert "grading_complete" in manifest
        assert manifest["grading_complete"] is (ungraded == 0)
