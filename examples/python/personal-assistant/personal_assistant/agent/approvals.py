"""Approvals: an `ApprovalHandler` that decides each tool call on `ApprovalRequest.arguments`, the exact values the
tool will receive.

The assistant may use its own tools and fetch web pages, read only inside the home directory, and never write files
or run a shell. A denial's reason goes back to the model and the turn continues, so each reason names what is allowed
instead.
"""

from dataclasses import dataclass
from pathlib import Path

from amplifier_agent import ApprovalHandler, ApprovalRequest, ApprovalResponse

from personal_assistant.agent.tools import READ_TOOLS


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
    home: Path
    allowed: frozenset[str]

    def resolve(self, value: object) -> Path:
        path = Path(str(value)).expanduser() if value else self.home
        if not path.is_absolute():
            path = self.home / path
        return path.resolve()

    def decide(self, name: str | None, arguments: dict | None) -> ApprovalResponse:
        if name in self.allowed:
            return ApprovalResponse("allow")
        if name not in READ_TOOLS:
            return ApprovalResponse(
                "deny",
                f"{name} is not permitted. Use {', '.join(sorted(self.allowed))}, and {', '.join(READ_TOOLS)} inside "
                f"{self.home}.",
            )
        if arguments is None:
            return ApprovalResponse("deny", "This request carries no tool arguments to check, so it is not permitted.")
        pattern = arguments.get("pattern") if name == "glob" else arguments.get("glob")
        if pattern and escapes(str(pattern)):
            return ApprovalResponse(
                "deny",
                f"{name} glob patterns may not be absolute or contain '..', not {pattern}. Put the directory in path "
                "and keep the pattern relative to it.",
            )
        path = self.resolve(arguments.get("file_path") if name == "read_file" else arguments.get("path"))
        if name == "glob":
            path = glob_base(path, str(arguments.get("pattern", "")))
        if path.is_relative_to(self.home.resolve()):
            return ApprovalResponse("allow")
        return ApprovalResponse("deny", f"{name} may only read inside {self.home}, not {path}. Use a path inside it.")


def approval_handler(policy: PathPolicy) -> ApprovalHandler:
    async def approve(request: ApprovalRequest) -> ApprovalResponse:
        return policy.decide(request.name, request.arguments)

    return approve
