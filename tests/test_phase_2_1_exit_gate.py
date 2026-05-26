"""Phase 2.1 exit gate — end-to-end smoke of the wire-spec hardening surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_lib.protocol._gen import main as gen_main
from amplifier_agent_lib.protocol.conformance.loader import load_fixture
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROTOCOL_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol"
_FIXTURE_DIR = _PROTOCOL_DIR / "conformance" / "fixtures"


def test_phase_2_1_exit_gate(tmp_path: Path) -> None:
    """End-to-end: generate, validate a payload, load all fixtures, version coherent."""
    pytest.importorskip("jsonschema")
    import jsonschema

    # 1. Generator runs cleanly into a clean directory
    runner = CliRunner()
    result = runner.invoke(gen_main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # 2. JSON Schema for TurnSubmitParams is well-formed Draft 2020-12
    schema_path = tmp_path / "schemas" / "TurnSubmitParams.schema.json"
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)

    # 3. A valid payload passes; a missing required field fails
    valid_payload = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "prompt": "hi",
    }
    jsonschema.validate(valid_payload, schema)

    invalid_payload = {"sessionId": "sess-1", "prompt": "hi"}  # missing turnId
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_payload, schema)

    # 4. All nine conformance fixtures load (5 D7 baseline + 4 A8 wire-shape fixtures)
    fixture_names = sorted(p.stem for p in _FIXTURE_DIR.glob("*.yaml"))
    assert fixture_names == [
        "approval-shim-three-error-codes",
        "capability_negotiation",
        "initialize-with-host-capabilities",
        "initialize-with-mcpservers",
        "l14_synthesis",
        "resume-with-session-store",
        "resume_continuity",
        "subagent_lineage",
        "version_skew",
    ]
    for path in _FIXTURE_DIR.glob("*.yaml"):
        fixture = load_fixture(path)
        assert fixture.setup.get("protocolVersion") in (PROTOCOL_VERSION, "2099-12-future-vN"), (
            f"{path.name}: protocolVersion in setup must be current ({PROTOCOL_VERSION}) "
            f"or the deliberate version-skew value"
        )

    # 5. Version coherence across spec.md and methods.py constant
    spec_md = (_PROTOCOL_DIR / "spec.md").read_text()
    assert PROTOCOL_VERSION in spec_md, "PROTOCOL_VERSION must appear in checked-in spec.md"
