"""Prompt spill-to-file for oversized prompts.

Companion to :mod:`mcp_spill` and structured identically.  Large prompts cannot
ride on argv: the prompt travels as the final positional element, and a single
argv element is capped at ``MAX_ARG_STRLEN`` (131072 bytes) on Linux while the
whole command line is capped at 32767 chars on Windows.  Past those ceilings the
spawn fails with ``E2BIG`` before the engine even boots.  So the wrapper spills
the prompt to a 0600 tmpfile under
``${XDG_RUNTIME_DIR || tempfile.gettempdir()}/amplifier-agent/<session_id>/prompt.txt``
and passes ``--prompt-file <path>`` instead of the positional argument.

This mirrors the treatment the MCP server map already gets — see
docs/spec/wrapper-contract.md: "MCP server configuration is always spilled to a
file, never passed on argv, so a large server map cannot overflow the OS argv
limit."

``cleanup_spill_file()`` is the matching teardown — idempotent unlink that
swallows ``FileNotFoundError`` so callers can call it unconditionally on every
exit path.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Spill any prompt whose UTF-8 encoded length reaches this many bytes.
#
# The threshold must sit safely under the SMALLEST platform ceiling.  That is
# NOT Linux's 131072-byte per-element cap — it is Windows' 32767-CHARACTER cap
# on the ENTIRE command line, which the prompt shares with the binary path and
# every other flag (--session-id, --config, --cwd, --workspace, ...).  16384
# leaves roughly half of that budget as headroom for the rest of the argv, and
# still keeps ordinary prompts on the fast in-memory path.
#
# The comparison is made against the UTF-8 ENCODED byte length
# (``len(prompt.encode("utf-8"))``), never ``len(prompt)``: a multibyte prompt
# occupies more bytes on the wire than it has characters, and the OS limit is
# denominated in bytes.
PROMPT_SPILL_THRESHOLD_BYTES = 16384


@dataclass(frozen=True, kw_only=True)
class PromptSpillResult:
    """Result of deciding whether to spill the prompt to a tmpfile.

    When the prompt fits on argv: ``prompt_file`` is ``None`` and the caller
    passes the prompt positionally as before.
    Otherwise: ``prompt_file`` points at the 0600 spill file containing the
    prompt text verbatim.
    """

    prompt_file: str | None


def _spill_base_dir() -> Path:
    """Compute the base directory for spill files.

    Prefers ``$XDG_RUNTIME_DIR/amplifier-agent`` (typically a tmpfs on Linux)
    and falls back to ``tempfile.gettempdir()/amplifier-agent`` otherwise.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "amplifier-agent"
    return Path(tempfile.gettempdir()) / "amplifier-agent"


def resolve_prompt_file_path(prompt: str, session_id: str) -> PromptSpillResult:
    """Spill *prompt* to a 0600 tmpfile when it is too large for argv.

    Args:
        prompt:     The caller's prompt text.
        session_id: Session identifier; used as the per-session subdirectory
                    under the spill base so concurrent sessions never clash.

    Returns:
        ``PromptSpillResult`` with the on-disk prompt path, or ``None`` when the
        prompt is small enough to travel as a positional argv element.
    """
    if len(prompt.encode("utf-8")) < PROMPT_SPILL_THRESHOLD_BYTES:
        return PromptSpillResult(prompt_file=None)

    base = _spill_base_dir()
    session_dir = base / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # Tighten the per-session directory to 0700.
    try:
        session_dir.chmod(0o700)
    except PermissionError:
        # Best effort; if we cannot tighten perms, still proceed with the file write.
        pass

    file_path = session_dir / "prompt.txt"

    # Write with restrictive perms.  We write to the final path with 0600 using
    # os.open() so the prompt -- which is caller data and may carry secrets --
    # is never world-readable.
    #
    # encoding="utf-8" is REQUIRED, not stylistic.  Text mode without it inherits
    # locale.getencoding(), which is cp1252 (or another ANSI codepage) on Windows
    # unless PEP 540 UTF-8 mode is active, and the engine reads this file as
    # strict "utf-8".  newline="" is equally load-bearing: the default newline
    # translation rewrites every "\n" to "\r\n" on Windows, which would silently
    # corrupt the caller's prompt in transit.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(file_path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(prompt)
    except Exception:
        # If write fails, ensure the partial file does not linger.
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        raise

    return PromptSpillResult(prompt_file=str(file_path))


def cleanup_prompt_spill_file(prompt_file: str | None) -> None:
    """Idempotently remove a prompt spill file.

    Safe to call with ``None`` (no-op) and safe to call when the file is
    already gone (``FileNotFoundError`` swallowed).  Other I/O errors
    propagate.
    """
    if not prompt_file:
        return
    try:
        os.unlink(prompt_file)
    except FileNotFoundError:
        return
