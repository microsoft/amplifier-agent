# tests/test_protocol_gen_staleness.py
"""CI gate: checked-in spec.md + schemas/ must match what _gen.py emits.

Per design §8 D1, the Python TypedDicts are the source of truth.  PRs that
edit the generated artifacts without re-running the generator are blocked
by this test.

Regenerate via:
    uv run python -m amplifier_agent_lib.protocol._gen \\
        --output-dir src/amplifier_agent_lib/protocol
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

_REGEN_CMD = "uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol"

_PROTOCOL_DIR = Path(__file__).resolve().parent.parent / "src" / "amplifier_agent_lib" / "protocol"


def _generate_to(tmp_path: Path) -> None:
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_spec_md_is_up_to_date(tmp_path: Path) -> None:
    """The checked-in spec.md matches generator output byte-for-byte."""
    _generate_to(tmp_path)
    actual = (_PROTOCOL_DIR / "spec.md").read_text()
    expected = (tmp_path / "spec.md").read_text()
    assert actual == expected, "spec.md is stale. Regenerate with:\n  " + _REGEN_CMD


@pytest.mark.parametrize(
    "schema_name",
    sorted(p.name for p in (_PROTOCOL_DIR / "schemas").iterdir() if p.suffix == ".json"),
)
def test_schema_is_up_to_date(tmp_path: Path, schema_name: str) -> None:
    """Each checked-in schemas/*.schema.json matches generator output."""
    _generate_to(tmp_path)
    actual = (_PROTOCOL_DIR / "schemas" / schema_name).read_text()
    expected = (tmp_path / "schemas" / schema_name).read_text()
    assert actual == expected, f"{schema_name} is stale. Regenerate with:\n  " + _REGEN_CMD


def test_no_extra_schemas_checked_in(tmp_path: Path) -> None:
    """Checked-in schemas/ directory contains no orphans."""
    _generate_to(tmp_path)
    actual = {p.name for p in (_PROTOCOL_DIR / "schemas").iterdir() if p.suffix == ".json"}
    expected = {p.name for p in (tmp_path / "schemas").iterdir() if p.suffix == ".json"}
    extras = actual - expected
    assert not extras, f"Extra schema files checked in: {extras}. Delete them or regenerate."
