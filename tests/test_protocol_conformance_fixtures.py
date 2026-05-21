"""Tests for the YAML wire-sequence fixture loader."""

from __future__ import annotations

from pathlib import Path

import pytest

_VALID_FIXTURE = """\
name: smoke
description: Loader smoke test fixture.
setup:
  protocolVersion: "2026-05-aaa-v0"
  clientCapabilities: {}
script:
  - direction: client_to_server
    method: initialize
    id: 1
    params: {}
assertions:
  - kind: response_matches
    id: 1
    result: {}
"""


def test_load_fixture_accepts_valid_shape(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol.conformance.loader import load_fixture

    p = tmp_path / "smoke.yaml"
    p.write_text(_VALID_FIXTURE)
    fixture = load_fixture(p)

    assert fixture.name == "smoke"
    assert fixture.setup["protocolVersion"] == "2026-05-aaa-v0"
    assert len(fixture.script) == 1
    assert fixture.script[0]["method"] == "initialize"
    assert fixture.assertions[0]["kind"] == "response_matches"


@pytest.mark.parametrize(
    "missing_key",
    ["name", "setup", "script", "assertions"],
)
def test_load_fixture_rejects_missing_top_level_key(tmp_path: Path, missing_key: str) -> None:
    import yaml

    from amplifier_agent_lib.protocol.conformance.loader import (
        FixtureValidationError,
        load_fixture,
    )

    data = yaml.safe_load(_VALID_FIXTURE)
    data.pop(missing_key)
    p = tmp_path / "broken.yaml"
    p.write_text(__import__("yaml").safe_dump(data))

    with pytest.raises(FixtureValidationError, match=missing_key):
        load_fixture(p)


def test_load_fixture_rejects_unknown_assertion_kind(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol.conformance.loader import (
        FixtureValidationError,
        load_fixture,
    )

    bad = _VALID_FIXTURE.replace("kind: response_matches", "kind: bogus_kind")
    p = tmp_path / "broken.yaml"
    p.write_text(bad)
    with pytest.raises(FixtureValidationError, match="bogus_kind"):
        load_fixture(p)


def _all_fixtures() -> list[Path]:
    base = (
        Path(__file__).resolve().parent.parent / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
    )
    return sorted(base.glob("*.yaml"))


@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: p.name)
def test_every_fixture_loads_structurally(fixture_path: Path) -> None:
    """Every YAML file under conformance/fixtures/ parses and structure-validates."""
    from amplifier_agent_lib.protocol.conformance.loader import load_fixture

    fixture = load_fixture(fixture_path)
    assert fixture.name
    assert fixture.script
    assert fixture.assertions


def test_expected_fixture_set_is_complete() -> None:
    """Exactly the five D7 contracts must be present — no more, no fewer."""
    names = {p.stem for p in _all_fixtures()}
    expected = {
        "l14_synthesis",
        "capability_negotiation",
        "subagent_lineage",
        "version_skew",
        "resume_continuity",
    }
    assert names == expected, f"unexpected fixture set: {names ^ expected}"
