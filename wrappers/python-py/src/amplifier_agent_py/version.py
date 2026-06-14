"""Protocol version check for the Amplifier Agent wrapper.

Mirrors wrappers/typescript/src/version.ts.

``check_protocol_version()`` compares the wrapper's compiled protocol version
constant against the version reported by the engine binary.  On mismatch it
returns ``VersionCheckFail`` with a remediation hint.  The check can be
bypassed with ``allow_skew=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, kw_only=True)
class VersionCheckOk:
    """Result when protocol versions match (or skew is allowed)."""

    ok: Literal[True] = True


@dataclass(frozen=True, kw_only=True)
class VersionCheckFail:
    """Result when protocol versions mismatch and skew is not allowed."""

    code: Literal["protocol_version_mismatch"]
    remediation: str
    ok: Literal[False] = False


VersionCheckResult = VersionCheckOk | VersionCheckFail


def check_protocol_version(
    *,
    wrapper: str,
    engine: str,
    allow_skew: bool = False,
) -> VersionCheckResult:
    """Compare wrapper and engine protocol versions.

    Args:
        wrapper:    The protocol version compiled into the wrapper
                    (e.g. ``"0.3.0"``).
        engine:     The protocol version reported by the engine binary.
        allow_skew: If True, bypass the version check and always return ok=True.

    Returns:
        ``VersionCheckOk`` when ``wrapper == engine`` or ``allow_skew=True``.
        Otherwise ``VersionCheckFail`` with a remediation hint identical in
        shape to the TS wrapper's response.
    """
    if allow_skew or wrapper == engine:
        return VersionCheckOk()

    remediation = (
        f"Protocol version mismatch: wrapper expects '{wrapper}' but engine reports '{engine}'. "
        f"Install a compatible engine version or set allow_protocol_skew=True / "
        f"AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1 to allow-protocol-skew."
    )
    return VersionCheckFail(code="protocol_version_mismatch", remediation=remediation)
