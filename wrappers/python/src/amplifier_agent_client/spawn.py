"""Binary discovery, environment allowlist, and engine version probe.

resolve_binary_path() — find the amplifier-agent binary
build_env()           — filter subprocess env to a safe allowlist
probe_engine_version() — run `amplifier-agent version --json` and parse result
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

#: Variables always passed through to subprocess (exact name match).
DEFAULT_ALLOWLIST: list[str] = [
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TERM",
    "TMPDIR",
]


def resolve_binary_path(
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the path to the amplifier-agent binary.

    Resolution order:
    1. ``AMPLIFIER_AGENT_BIN`` env var (if set)
    2. ``shutil.which('amplifier-agent')`` via PATH lookup

    Args:
        env: Environment dict to check for ``AMPLIFIER_AGENT_BIN``.
             Defaults to ``os.environ`` (via shutil.which).

    Returns:
        Absolute path string to the binary.

    Raises:
        RuntimeError: With code ``'binary_not_found'`` if neither resolves.
    """
    import os

    lookup_env = env if env is not None else dict(os.environ)

    env_bin = lookup_env.get("AMPLIFIER_AGENT_BIN")
    if env_bin:
        return env_bin

    # Fall back to PATH lookup
    found = shutil.which("amplifier-agent")
    if found:
        return found

    raise RuntimeError(
        "amplifier-agent binary not found. "
        "Install amplifier-agent: pip install amplifier-agent, "
        "or set AMPLIFIER_AGENT_BIN to the binary path."
    )


def build_env(
    *,
    process_env: dict[str, str],
    allowlist: list[str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the subprocess environment from the caller's process environment.

    Only variables whose name is in ``allowlist``, starts with ``AMPLIFIER_``,
    or starts with ``LC_`` are included.  ``extra`` entries are merged last.

    Args:
        process_env: The caller's current environment (e.g. ``dict(os.environ)``).
        allowlist:   List of exact variable names to pass through.
        extra:       Additional variables merged on top (override everything).

    Returns:
        Filtered environment dict safe to pass to ``subprocess``.
    """
    allow_set = set(allowlist)
    result: dict[str, str] = {}

    for key, value in process_env.items():
        if key in allow_set or key.startswith("AMPLIFIER_") or key.startswith("LC_"):
            result[key] = value

    if extra:
        result.update(extra)

    return result


def probe_engine_version(
    bin_path: str,
    env: dict[str, str],
    timeout: int = 5,
) -> dict[str, Any]:
    """Run ``<bin_path> version --json`` and return the parsed JSON payload.

    Args:
        bin_path: Absolute path to the amplifier-agent binary.
        env:      Environment to pass to the subprocess.
        timeout:  Timeout in seconds (default: 5).

    Returns:
        Parsed JSON dict with at least ``version`` and ``protocolVersion`` keys.

    Raises:
        subprocess.CalledProcessError: If the process exits non-zero.
        subprocess.TimeoutExpired:     If the process exceeds the timeout.
        json.JSONDecodeError:          If stdout is not valid JSON.
    """
    result = subprocess.run(
        [bin_path, "version", "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=True,
    )
    return json.loads(result.stdout.strip())  # type: ignore[no-any-return]
