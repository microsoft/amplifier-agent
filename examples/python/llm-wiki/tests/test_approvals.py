from pathlib import Path

from amplifier_agent import ApprovalRequest
import pytest

from llm_wiki.agent.approvals import PathPolicy, approval_handler


@pytest.fixture
def home(tmp_path: Path) -> Path:
    for directory in ("raw/acme__repo", "wiki/concepts"):
        (tmp_path / directory).mkdir(parents=True)
    return tmp_path


def reduce_policy(home: Path) -> PathPolicy:
    return PathPolicy(
        working_directory=home / "wiki",
        read_roots=(home / "wiki", home / "raw"),
        write_targets=frozenset({home / "wiki" / "concepts" / "bundle.md"}),
        caller_tools=frozenset({"search_wiki"}),
    )


@pytest.mark.parametrize(
    ("name", "arguments", "decision"),
    [
        ("read_file", {"file_path": "index.md"}, "allow"),
        ("read_file", {"file_path": "../raw/acme__repo/README.md"}, "allow"),
        ("read_file", {"file_path": "/etc/passwd"}, "deny"),
        ("glob", {"pattern": "../../**/*.md"}, "deny"),
        ("glob", {"pattern": "*/../../../*"}, "deny"),
        ("grep", {"pattern": "x", "glob": "../../**"}, "deny"),
        ("write_file", {"file_path": "concepts/bundle.md", "content": "x"}, "allow"),
        ("write_file", {"file_path": "concepts/hook.md", "content": "x"}, "deny"),
        ("bash", {"command": "ls"}, "deny"),
        ("search_wiki", {"query": "x"}, "allow"),
    ],
)
def test_decisions(home: Path, name: str, arguments: dict, decision: str) -> None:
    response = reduce_policy(home).decide(name, arguments)

    assert response.decision == decision
    if decision == "deny":
        assert response.reason


def request(name: str, arguments: dict | None) -> ApprovalRequest:
    return ApprovalRequest(request_id="r1", summary="preview", call_id="c1", name=name, arguments=arguments)


async def test_handler_decides_on_the_request_arguments(home: Path) -> None:
    approve = approval_handler(reduce_policy(home))

    allowed = await approve(request("write_file", {"file_path": "concepts/bundle.md", "content": "x"}))
    denied = await approve(request("write_file", {"file_path": "concepts/other.md", "content": "x"}))

    assert allowed.decision == "allow"
    assert denied.decision == "deny"
    assert denied.reason is not None
    assert "concepts/bundle.md" in denied.reason
    assert "Write only that page." in denied.reason
