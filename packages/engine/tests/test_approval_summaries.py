import copy
import json

import pytest
from amplifier_agent_engine._engine.approval_summaries import SUMMARY_LIMIT, approval_summary


@pytest.mark.parametrize("source,name,arguments,expected", [
    ("built-in", "bash", {"command": "printf hello > result.txt"}, "printf hello > result.txt"),
    ("built-in", "write_file", {"file_path": "report.txt", "content": "result"}, "report.txt"),
    ("built-in", "edit_file", {"file_path": "report.txt", "old_string": "before",
                               "new_string": "after"}, '"new_string": "after"'),
    ("built-in", "web_fetch", {"url": "https://example.test/report"}, "example.test/report"),
    ("built-in", "grep", {"path": "src", "pattern": "needle"}, '"pattern": "needle"'),
    ("built-in", "delegate", {"instruction": "Review the billing report."}, "billing report"),
    ("built-in", "load_skill", {"name": "report", "arguments": "billing"}, '"name": "report"'),
    ("caller", "deploy", {"target": "staging", "release": {"version": 7}}, '"version": 7'),
    ("mcp", "mcp_ledger_record", {"value": "receipt"}, '"value": "receipt"'),
])
def test_approval_preview_contains_action_target_and_payload(source, name, arguments, expected):
    original = copy.deepcopy(arguments)
    summary = approval_summary(source, name, arguments)
    assert f'Run {source} tool "{name}".' in summary
    assert expected in summary
    assert arguments == original


def test_secret_fields_are_redacted_recursively_without_hiding_other_payloads():
    arguments = {
        "target": "staging", "headers": {"Authorization": "Bearer secret-value",
                                          "X-Api-Key": "secret-key", "Accept": "text/plain"},
        "environment": {"DATABASE_PASSWORD": "secret-password", "AWS_SECRET_ACCESS_KEY": "aws-key"},
        "payload": [{"clientSecret": "secret-client", "refresh_token": "secret-refresh"}],
        "max_tokens": 123, "private-key": "secret-private", "cookie": "secret-cookie",
        "auth": "secret-auth", "secret_key": "secret-value", "passphrase": "secret-phrase",
    }
    original = copy.deepcopy(arguments)
    summary = approval_summary("caller", "send", arguments)
    assert summary.count("[redacted]") == 11
    assert "secret-" not in summary and "aws-key" not in summary
    assert '"target": "staging"' in summary
    assert '"Accept": "text/plain"' in summary
    assert '"max_tokens": 123' in summary
    assert arguments == original


def test_argument_and_name_control_characters_cannot_add_lines_or_terminal_controls():
    summary = approval_summary("caller", "send\x1b[2J\n", {
        "payload\r\n": "line\n\r\t\x00\x1b[31m\u202e\u2066\u2028\u0085",
    })
    assert summary.isascii()
    assert all(32 <= ord(char) < 127 for char in summary)
    assert r"\n" in summary and r"\u001b" in summary and r"\u202e" in summary


@pytest.mark.parametrize("arguments", [
    {"command": "x" * 100_000},
    {"content": "\x00" * 100_000},
    {"items": ["item"] * 100_000},
    {f"field-{index}": "value" for index in range(10_000)},
    {"one": {"two": {"three": {"four": {"five": "buried"}}}}},
    {"nested": [[[[["buried"]]]]]},
    {"key" * 1000: "omitted-value"},
    {"integer": 2 ** 100_000},
    {"items": [{f"key-{index}": "value" for index in range(12)}] * 12},
])
def test_large_arguments_have_a_bounded_explicitly_truncated_preview(arguments):
    summary = approval_summary("caller", "large-tool", arguments)
    assert len(summary) <= SUMMARY_LIMIT
    assert "[truncated" in summary


def test_builtin_targets_are_shown_before_large_payloads():
    summary = approval_summary("built-in", "write_file", {
        **{f"field-{index}": "large" * 1000 for index in range(20)},
        "file_path": "target.txt", "content": "write this",
    }, {"working_directory": "/work/project"})
    assert '"file_path": "target.txt"' in summary
    assert '"content": "write this"' in summary
    assert '"working_directory": "/work/project"' in summary
    assert len(summary) <= SUMMARY_LIMIT


def test_small_arguments_are_a_complete_json_preview():
    arguments = {"value": [None, False, True, 17, 2.5, "\u2603"]}
    summary = approval_summary("caller", "record", arguments)
    assert json.loads(summary.split(" Arguments: ", 1)[1]) == arguments


def test_large_hook_context_reserves_room_for_the_command_and_working_directory():
    context = {
        "tool_input": {"payload": [{"content": "\x00" * 100_000}] * 100},
        "working_directory": "/work/skills/review",
        "hook_event": "PreToolUse",
    }
    arguments = {"command": "python review.py --target receipt.txt", "timeout": 30}
    original_context, original_arguments = copy.deepcopy(context), copy.deepcopy(arguments)
    summary = approval_summary("built-in", "bash", arguments, context)
    assert '"working_directory": "/work/skills/review"' in summary
    assert '"command": "python review.py --target receipt.txt"' in summary
    assert '"timeout": 30' in summary
    assert "[truncated]" in summary
    assert len(summary) <= SUMMARY_LIMIT
    assert context == original_context and arguments == original_arguments
