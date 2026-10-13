"""The system message: the agent's own instructions, which configured instructions replace, and AGENTS.md files."""

from __future__ import annotations

import datetime
from pathlib import Path
import platform
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_agent_engine._engine.configuration import ResolvedConfig

# Tool names are not mentioned: callers choose the tool set, so any named tool may be absent.
BASE_INSTRUCTIONS = """\
You are Amplifier Agent, an autonomous agent that carries out tasks in a computer environment with the tools you are \
given.

Carry each task through to completion. When something is unclear, make a reasonable decision, state it, and continue. \
Ask the user only when you cannot continue without their answer. Do not stop at a plan or a partial result unless the \
user asked for one.

# Principles

- Investigate before acting. Read the relevant files, trace how things work, and base conclusions on evidence, not \
assumptions.
- Make the minimum change that does what the task asks. Add nothing speculative and nothing unrelated.
- Verify your work. Run the relevant tests, builds, or checks, and re-read what you produce. If a check fails, fix the \
cause and check again.
- Report honestly. Never claim success without evidence, and say when something is blocked or not verified.
- Do defensive security work only. Refuse malicious work and protect secrets.

# Conventions

The `AGENTS.md` files that apply to the working directory, if any, are at the end of these instructions. An \
`AGENTS.md` in a subdirectory you work in also applies to the files under it. The most specific file wins. Follow the \
existing style of the code and documents you change.

# Tools

- Prefer dedicated file tools over shell commands to read, search, and edit files.
- Make independent tool calls in parallel.
- Plan multi-step work before you start, and keep track of what remains.
- Delegate only clearly independent subtasks that benefit from a separate context.

When you finish, reply with a short summary of what you did and how you verified it."""


AGENTS_PREAMBLE = """\
# AGENTS.md

Instructions from AGENTS.md files, from the repository root to the working directory. A more specific file overrides \
a less specific one."""


def _in_git_repository(directory: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (directory, *directory.parents))


def _agents_directories(working_directory: Path) -> list[Path]:
    """The directories searched for AGENTS.md, repository root first and the working directory last."""
    lineage = (working_directory, *working_directory.parents)
    for depth, candidate in enumerate(lineage):
        if (candidate / ".git").exists():
            return list(reversed(lineage[: depth + 1]))
    return [working_directory]


def agents_files(working_directory: Path) -> list[tuple[Path, str]]:
    """Each non-empty AGENTS.md that applies to ``working_directory``, with its text, outermost first."""
    found = []
    for directory in _agents_directories(working_directory):
        path = directory / "AGENTS.md"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").rstrip()
        except OSError:
            continue
        if text.strip():
            found.append((path, text))
    return found


def _environment(config: ResolvedConfig) -> str:
    lines = [
        "# Environment",
        "",
        f"Working directory: {config.working_directory}",
    ]
    if config.additional_directories:
        lines.append(f"Additional directories: {', '.join(str(path) for path in config.additional_directories)}")
    lines += [
        f"Git repository: {'yes' if _in_git_repository(config.working_directory) else 'no'}",
        f"Platform: {platform.system().lower()} ({platform.platform()})",
        f"Date: {datetime.date.today().isoformat()}",
    ]
    return "\n".join(lines)


def system_instructions(config: ResolvedConfig) -> str:
    """Configured instructions, or else the agent's own with the environment; ``system_message`` adds AGENTS.md."""
    if config.instructions is not None:
        return config.instructions
    return f"{BASE_INSTRUCTIONS}\n\n{_environment(config)}"


def system_message(config: ResolvedConfig) -> str:
    """The system message a session starts with: ``system_instructions`` followed by the AGENTS.md files.

    Delegated children receive ``system_instructions`` as their configured instructions and build their own system
    message, so the AGENTS.md block appears once in each session.
    """
    instructions = system_instructions(config)
    files = agents_files(config.working_directory)
    if not files:
        return instructions
    sections = [instructions.rstrip(), AGENTS_PREAMBLE, *(f"Instructions from: {path}\n{text}" for path, text in files)]
    return "\n\n".join(section for section in sections if section)
