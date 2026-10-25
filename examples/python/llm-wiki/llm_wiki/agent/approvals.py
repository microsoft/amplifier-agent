"""Approvals: an `ApprovalHandler` that decides each tool call on `ApprovalRequest.arguments`, the exact values the
tool will receive.

A session may read inside the project data, write only the page it owns, and never run a shell. A denial's reason goes
back to the model and the turn continues, so each reason names what is allowed instead. The checks cover the tool
arguments, not what glob and grep walk into, such as a symlink inside a cloned repository.
"""

from dataclasses import dataclass
from pathlib import Path

from amplifier_agent import ApprovalHandler, ApprovalRequest, ApprovalResponse

from llm_wiki.agent.tools import READ_TOOLS, WRITE_TOOLS

ALWAYS_ALLOWED = frozenset({"load_skill"})


def glob_base(path: Path, pattern: str) -> Path:
    """The directory a glob pattern can reach: its path joined with the pattern's parts before the first wildcard."""
    fixed = []
    for part in Path(pattern).parts:
        if any(character in part for character in "*?[{"):
            break
        fixed.append(part)
    return (path.joinpath(*fixed) if fixed else path).resolve()


def escapes(pattern: str) -> bool:
    """Whether a glob pattern can match outside its base: an absolute pattern, or one with a `..` part."""
    return Path(pattern).is_absolute() or ".." in Path(pattern).parts


@dataclass(frozen=True)
class PathPolicy:
    working_directory: Path
    read_roots: tuple[Path, ...]
    write_targets: frozenset[Path] = frozenset()
    caller_tools: frozenset[str] = frozenset()

    def resolve(self, value: object) -> Path:
        path = Path(str(value)).expanduser() if value else self.working_directory
        if not path.is_absolute():
            path = self.working_directory / path
        return path.resolve()

    def readable(self, path: Path) -> bool:
        return any(path.is_relative_to(root.resolve()) for root in self.read_roots)

    def decide(self, name: str | None, arguments: dict | None) -> ApprovalResponse:
        if name in self.caller_tools or name in ALWAYS_ALLOWED:
            return ApprovalResponse("allow")
        if arguments is None:
            return ApprovalResponse("deny", "This request carries no tool arguments to check, so it is not permitted.")
        if name in READ_TOOLS:
            pattern = arguments.get("pattern") if name == "glob" else arguments.get("glob")
            if pattern and escapes(str(pattern)):
                return ApprovalResponse(
                    "deny",
                    f"{name} glob patterns may not be absolute or contain '..', not {pattern}. Put the directory in "
                    "path and keep the pattern relative to it.",
                )
            path = self.resolve(arguments.get("file_path") if name == "read_file" else arguments.get("path"))
            if name == "glob":
                path = glob_base(path, str(arguments.get("pattern", "")))
            if self.readable(path):
                return ApprovalResponse("allow")
            return ApprovalResponse(
                "deny", f"{name} may only read inside {self.describe_roots()}, not {path}. Use a path inside them."
            )
        if name in WRITE_TOOLS:
            path = self.resolve(arguments.get("file_path"))
            if path in {target.resolve() for target in self.write_targets}:
                return ApprovalResponse("allow")
            return ApprovalResponse(
                "deny", f"{name} may only write {self.describe_targets()}, not {path}. Write only that page."
            )
        return ApprovalResponse(
            "deny", f"{name} is not permitted in this session. Use read_file, glob, grep, and the session's own tools."
        )

    def describe_roots(self) -> str:
        return ", ".join(sorted(str(root) for root in self.read_roots))

    def describe_targets(self) -> str:
        return ", ".join(sorted(str(target) for target in self.write_targets)) or "nothing"


def approval_handler(policy: PathPolicy) -> ApprovalHandler:
    async def approve(request: ApprovalRequest) -> ApprovalResponse:
        return policy.decide(request.name, request.arguments)

    return approve
