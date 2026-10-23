from pathlib import Path

import pytest

from personal_assistant.agent.approvals import PathPolicy


@pytest.fixture
def policy(tmp_path: Path) -> PathPolicy:
    (tmp_path / "home" / "memory").mkdir(parents=True)
    return PathPolicy(tmp_path / "home", frozenset({"remember", "forget", "set_reminder", "web_fetch"}))


@pytest.mark.parametrize(
    ("name", "arguments", "decision"),
    [
        ("read_file", {"file_path": "MEMORY.md"}, "allow"),
        ("read_file", {"file_path": "memory/2026-10-23.md"}, "allow"),
        ("read_file", {"file_path": "../.state/session_id"}, "deny"),
        ("read_file", {"file_path": "/etc/passwd"}, "deny"),
        ("glob", {"pattern": "memory/*.md"}, "allow"),
        ("glob", {"pattern": "../**/*"}, "deny"),
        ("glob", {"pattern": "*.md", "path": "/etc"}, "deny"),
        ("grep", {"pattern": "tea", "path": "memory"}, "allow"),
        ("grep", {"pattern": "tea", "glob": "../**"}, "deny"),
        ("write_file", {"file_path": "MEMORY.md", "content": "x"}, "deny"),
        ("bash", {"command": "ls"}, "deny"),
        ("remember", {"fact": "x"}, "allow"),
        ("web_fetch", {"url": "https://example.com"}, "allow"),
    ],
)
def test_decisions(policy: PathPolicy, name: str, arguments: dict, decision: str) -> None:
    response = policy.decide(name, arguments)

    assert response.decision == decision
    if decision == "deny":
        assert response.reason
