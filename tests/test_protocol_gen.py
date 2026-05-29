"""Tests for protocol/_gen.py — the wire-spec generator."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner


def test_gen_cli_runs_and_creates_output_dirs(tmp_path: Path) -> None:
    """The generator CLI runs cleanly and prepares the schemas/ subdirectory."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])

    assert result.exit_code == 0, f"stdout={result.output!r} exc={result.exception!r}"
    assert (tmp_path / "schemas").is_dir(), "schemas/ subdirectory should be created"


def test_typed_dict_to_schema_initialize_params() -> None:
    """Converts InitializeParams to a Draft 2020-12 JSON Schema."""
    from amplifier_agent_lib.protocol._gen import typed_dict_to_schema
    from amplifier_agent_lib.protocol.methods import InitializeParams

    schema = typed_dict_to_schema(InitializeParams)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "InitializeParams"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False

    # Required fields per methods.py:41-50
    assert set(schema["required"]) == {"protocolVersion", "clientInfo", "capabilities"}

    # NotRequired fields appear in properties but NOT in required
    props = schema["properties"]
    for opt_field in ("sessionId", "resume", "providerOverride", "cwd"):
        assert opt_field in props, f"{opt_field} missing from properties"

    # Scalar type mapping
    assert props["protocolVersion"] == {"type": "string"}
    assert props["resume"] == {"type": "boolean"}

    # Nested TypedDict reference
    assert props["clientInfo"] == {"$ref": "ClientInfo.schema.json"}


def test_typed_dict_to_schema_turn_submit_result_handles_optional_union() -> None:
    """``reply: str | None`` should become an anyOf union."""
    from amplifier_agent_lib.protocol._gen import typed_dict_to_schema
    from amplifier_agent_lib.protocol.methods import TurnSubmitResult

    schema = typed_dict_to_schema(TurnSubmitResult)
    reply_schema = schema["properties"]["reply"]
    assert "anyOf" in reply_schema
    types = {sub.get("type") for sub in reply_schema["anyOf"]}
    assert types == {"string", "null"}


def test_gen_emits_schema_for_every_typeddict(tmp_path: Path) -> None:
    """All TypedDicts across the four protocol modules become schema files."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    result = runner.invoke(main, ["--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    schemas_dir = tmp_path / "schemas"
    # Spot-check: known TypedDicts from each module must have schema files
    expected = {
        "InitializeParams.schema.json",
        "InitializeResult.schema.json",
        "TurnSubmitParams.schema.json",
        "TurnSubmitResult.schema.json",
        "ResultDeltaNotification.schema.json",
        "ResultFinalNotification.schema.json",
        "ApprovalRequestNotification.schema.json",
        "ClientCapabilities.schema.json",
        "ServerCapabilities.schema.json",
        "McpServerConfig.schema.json",
        "HostCapabilities.schema.json",
        "InitializeHostParams.schema.json",
        "error_codes.schema.json",
    }
    actual = {p.name for p in schemas_dir.iterdir()}
    missing = expected - actual
    assert not missing, f"missing schema files: {missing}\nfound: {sorted(actual)}"


def test_gen_error_codes_schema_is_string_enum(tmp_path: Path) -> None:
    """error_codes.schema.json enumerates the ErrorCode StrEnum values."""
    import json

    from amplifier_agent_lib.protocol._gen import main
    from amplifier_agent_lib.protocol.errors import ErrorCode

    runner = CliRunner()
    runner.invoke(main, ["--output-dir", str(tmp_path)])

    schema = json.loads((tmp_path / "schemas" / "error_codes.schema.json").read_text())
    assert schema["type"] == "string"
    assert set(schema["enum"]) == {ec.value for ec in ErrorCode}


def test_gen_emits_spec_md_with_required_sections(tmp_path: Path) -> None:
    """spec.md contains all required top-level sections and the DO NOT EDIT banner."""
    from amplifier_agent_lib.protocol._gen import main

    runner = CliRunner()
    runner.invoke(main, ["--output-dir", str(tmp_path)])

    spec = (tmp_path / "spec.md").read_text()
    assert "DO NOT HAND-EDIT" in spec
    assert "0.2.0" in spec, "PROTOCOL_VERSION must appear"
    for required_section in ("## Methods", "## Notifications", "## Errors", "## Capabilities"):
        assert required_section in spec, f"missing section: {required_section}"
    # Schema links must point at the schemas/ subdir
    assert "schemas/InitializeParams.schema.json" in spec
    assert "schemas/error_codes.schema.json" in spec
