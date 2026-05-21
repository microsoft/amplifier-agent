"""Wheel packaging gate: spec.md, schemas, and fixtures ship in the wheel."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
def test_wheel_includes_phase_2_1_artifacts(tmp_path: Path) -> None:
    """Built wheel contains spec.md + schemas + fixtures."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_REPO_ROOT,
        check=True,
    )
    wheel = next(tmp_path.glob("amplifier_agent-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())

    # spec.md
    assert "amplifier_agent_lib/protocol/spec.md" in names

    # at least one schema
    assert any(n.startswith("amplifier_agent_lib/protocol/schemas/") and n.endswith(".schema.json") for n in names), (
        f"no schema files in wheel; wheel contents: {sorted(names)[:30]}"
    )

    # all five fixtures
    required_fixtures = {
        f"amplifier_agent_lib/protocol/conformance/fixtures/{stem}.yaml"
        for stem in (
            "l14_synthesis",
            "capability_negotiation",
            "subagent_lineage",
            "version_skew",
            "resume_continuity",
        )
    }
    missing = required_fixtures - names
    assert not missing, f"missing fixtures in wheel: {missing}"
