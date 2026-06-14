"""Unit tests for argv_builder.

Mirrors the TS argv-builder golden expectations. Same input must produce the
same argv list as the TS wrapper would emit — this is the symmetry contract.
"""

from __future__ import annotations

from amplifier_agent_py import AssembleArgvInput, assemble_argv


def _base(**kwargs: object) -> AssembleArgvInput:
    """Build an AssembleArgvInput with safe defaults, override via kwargs."""
    defaults: dict[str, object] = {
        "session_id": "s-1",
        "prompt": "hi",
        "protocol_version": "0.3.0",
        "resume": False,
    }
    defaults.update(kwargs)
    return AssembleArgvInput(**defaults)  # type: ignore[arg-type]


def test_minimal_fresh_session_emits_canonical_argv() -> None:
    argv = assemble_argv(_base())
    assert argv == [
        "run",
        "--session-id",
        "s-1",
        "--fresh",
        "--output",
        "json",
        "--protocol-version",
        "0.3.0",
        "-y",
        "hi",
    ]


def test_resume_flag_replaces_fresh() -> None:
    argv = assemble_argv(_base(resume=True))
    assert "--resume" in argv
    assert "--fresh" not in argv


def test_cwd_emits_cwd_flag_in_expected_position() -> None:
    argv = assemble_argv(_base(cwd="/work"))
    idx = argv.index("--cwd")
    assert argv[idx + 1] == "/work"


def test_config_path_emits_config_flag() -> None:
    argv = assemble_argv(_base(config_path="/etc/host.json"))
    idx = argv.index("--config")
    assert argv[idx + 1] == "/etc/host.json"


def test_display_mode_emits_display_flag_only_when_set() -> None:
    argv = assemble_argv(_base())
    assert "--display" not in argv

    argv = assemble_argv(_base(display_mode="ndjson"))
    idx = argv.index("--display")
    assert argv[idx + 1] == "ndjson"


def test_workspace_emits_workspace_flag_only_when_non_empty() -> None:
    argv = assemble_argv(_base(workspace=""))
    assert "--workspace" not in argv

    argv = assemble_argv(_base(workspace="pc-acme-ceo"))
    idx = argv.index("--workspace")
    assert argv[idx + 1] == "pc-acme-ceo"


def test_approval_mode_yes_emits_minus_y() -> None:
    argv = assemble_argv(_base(approval_mode="yes"))
    assert "-y" in argv
    assert "-n" not in argv


def test_approval_mode_no_emits_minus_n() -> None:
    argv = assemble_argv(_base(approval_mode="no"))
    assert "-n" in argv
    assert "-y" not in argv


def test_approval_mode_prompt_emits_neither_flag() -> None:
    argv = assemble_argv(_base(approval_mode="prompt"))
    assert "-y" not in argv
    assert "-n" not in argv


def test_approval_mode_none_preserves_historical_minus_y_default() -> None:
    argv = assemble_argv(_base(approval_mode=None))
    assert "-y" in argv
    assert "-n" not in argv


def test_prompt_is_final_positional_argument() -> None:
    argv = assemble_argv(_base(prompt="say hello"))
    assert argv[-1] == "say hello"


def test_all_flags_together_produce_stable_order() -> None:
    argv = assemble_argv(
        _base(
            resume=True,
            cwd="/work",
            config_path="/etc/host.json",
            display_mode="ndjson",
            workspace="proj-1",
            approval_mode="no",
            prompt="full",
        )
    )
    assert argv == [
        "run",
        "--session-id",
        "s-1",
        "--resume",
        "--cwd",
        "/work",
        "--config",
        "/etc/host.json",
        "--output",
        "json",
        "--protocol-version",
        "0.3.0",
        "--display",
        "ndjson",
        "--workspace",
        "proj-1",
        "-n",
        "full",
    ]
