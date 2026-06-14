"""Binary discovery, environment allowlist, and engine version probe.

Mirrors wrappers/typescript/src/spawn.ts.

- ``resolve_binary_path()``  — find the ``amplifier-agent`` binary
- ``build_env()``            — filter subprocess env to a safe allowlist
- ``probe_engine_version()`` — run ``amplifier-agent version --json`` and parse result
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass

from .errors import AaaError

# ---------------------------------------------------------------------------
# Allowlists and blocklists (mirror TS spawn.ts)
# ---------------------------------------------------------------------------

#: Variables always passed through to subprocess (exact name match).
DEFAULT_ALLOWLIST: list[str] = [
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TERM",
    "TMPDIR",
]

#: Environment variable names that callers MUST NOT inject via ``env.extra``.
#:
#: These names are well-known dynamic-loader / interpreter hooks that can
#: hijack subprocess execution (preload libraries, prepend import paths,
#: override startup files).  Per design §4.12.1, the wrapper rejects any
#: ``env.extra`` entry whose key appears in this set.
BLOCKED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONSTARTUP",
        "PATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
    }
)


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def resolve_binary_path(env: dict[str, str] | None = None) -> str:
    """Resolve the path to the amplifier-agent binary.

    Resolution order:

    1. ``AMPLIFIER_AGENT_BIN`` env var (if set)
    2. ``shutil.which('amplifier-agent')`` via PATH lookup

    Args:
        env: Environment dict to check for ``AMPLIFIER_AGENT_BIN``.
             Defaults to ``os.environ``.

    Returns:
        Path string to the binary.  Returns the ``AMPLIFIER_AGENT_BIN`` value
        verbatim even when the file does not exist (callers handle errors at
        spawn time, matching TS behaviour).

    Raises:
        AaaError: With code ``"binary_not_found"`` when neither resolves.
    """
    lookup_env = env if env is not None else dict(os.environ)

    env_bin = lookup_env.get("AMPLIFIER_AGENT_BIN")
    if env_bin:
        return env_bin

    found = shutil.which("amplifier-agent")
    if found:
        return found

    raise AaaError(
        "binary_not_found",
        "amplifier-agent binary not found. "
        "Install amplifier-agent (e.g. `uv tool install amplifier-agent` "
        "or `pipx install amplifier-agent`), "
        "or set AMPLIFIER_AGENT_BIN to the binary path.",
        classification="transport",
        severity="error",
    )


# ---------------------------------------------------------------------------
# Subprocess environment construction
# ---------------------------------------------------------------------------


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

    Raises:
        AaaError: With ``code='env_injection_rejected'`` if ``extra`` contains
                  any key in :data:`BLOCKED_ENV_KEYS` (design §4.12.1).
    """
    if extra:
        for key in extra:
            if key in BLOCKED_ENV_KEYS:
                raise AaaError(
                    "env_injection_rejected",
                    f"env.extra key {key!r} is blocked for security reasons "
                    "(design §4.12.1). Remove it from env.extra.",
                    classification="protocol",
                    severity="error",
                )

    allow_set = set(allowlist)
    result: dict[str, str] = {}

    for key, value in process_env.items():
        if key in allow_set or key.startswith("AMPLIFIER_") or key.startswith("LC_"):
            result[key] = value

    if extra:
        result.update(extra)

    return result


# ---------------------------------------------------------------------------
# Engine version probe
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EngineVersionPayload:
    """Parsed shape of ``amplifier-agent version --json`` output."""

    version: str
    protocol_version: str
    bundle_digest: str | None = None


async def probe_engine_version(
    bin_path: str,
    env: dict[str, str],
    timeout_s: float = 5.0,
) -> EngineVersionPayload:
    """Run ``<bin_path> version --json`` and return the parsed payload.

    Async — uses ``asyncio.create_subprocess_exec`` so the version probe does
    not block the event loop.

    Args:
        bin_path:  Path to the amplifier-agent binary.
        env:       Environment to pass to the subprocess.
        timeout_s: Timeout in seconds (default: 5).

    Returns:
        ``EngineVersionPayload`` with version, protocol_version, and optional
        bundle_digest.

    Raises:
        AaaError: With ``code='engine_probe_failed'`` for any probe failure
                  (timeout, non-zero exit, unparseable JSON, missing fields).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "version",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, PermissionError) as e:
        raise AaaError(
            "engine_probe_failed",
            f"Could not start engine binary at {bin_path!r}: {e}",
            classification="transport",
            severity="error",
        ) from e

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as e:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise AaaError(
            "engine_probe_failed",
            f"Engine version probe timed out after {timeout_s}s",
            classification="transport",
            severity="error",
        ) from e

    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace").strip()
        raise AaaError(
            "engine_probe_failed",
            f"amplifier-agent version --json exited with code {proc.returncode}: {tail}",
            classification="transport",
            severity="error",
            stderr_tail=tail or None,
        )

    text = stdout_bytes.decode().strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise AaaError(
            "engine_probe_failed",
            f"Engine version probe returned non-JSON stdout: {text!r}",
            classification="protocol",
            severity="error",
        ) from e

    if not isinstance(parsed, dict):
        raise AaaError(
            "engine_probe_failed",
            f"Engine version probe returned non-object JSON: {parsed!r}",
            classification="protocol",
            severity="error",
        )

    version = parsed.get("version")
    protocol_version = parsed.get("protocolVersion")
    if not isinstance(version, str) or not isinstance(protocol_version, str):
        raise AaaError(
            "engine_probe_failed",
            f"Engine version probe missing required fields (version, protocolVersion): {parsed!r}",
            classification="protocol",
            severity="error",
        )

    bundle_digest = parsed.get("bundleDigest")
    bundle_digest_str = bundle_digest if isinstance(bundle_digest, str) else None

    return EngineVersionPayload(
        version=version,
        protocol_version=protocol_version,
        bundle_digest=bundle_digest_str,
    )
