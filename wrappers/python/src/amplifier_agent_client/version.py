"""Protocol version check for the Amplifier Agent Python wrapper.

check_protocol_version() compares the wrapper's compiled protocol version
constant against the version reported by the engine binary.

On mismatch it returns a VersionCheck with ok=False and a remediation hint.
The check can be bypassed with allow_skew=True.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VersionCheck:
    """Result of a protocol version compatibility check."""

    ok: bool
    """True if the versions match or skew is allowed."""

    code: str | None = None
    """Error code when ok=False; 'protocol_version_mismatch' on mismatch."""

    remediation: str | None = None
    """Human-readable remediation hint when ok=False."""


def check_protocol_version(
    *,
    wrapper: str,
    engine: str,
    allow_skew: bool = False,
) -> VersionCheck:
    """Compare wrapper and engine protocol versions.

    Args:
        wrapper:    Protocol version compiled into the wrapper.
        engine:     Protocol version reported by the engine binary.
        allow_skew: If True, bypass the check and always return ok=True.

    Returns:
        VersionCheck with ok=True if versions match or allow_skew is True.
        VersionCheck with ok=False, code, and remediation on mismatch.
    """
    if allow_skew or wrapper == engine:
        return VersionCheck(ok=True)

    remediation = (
        f"Protocol version mismatch: wrapper expects '{wrapper}' but engine reports '{engine}'. "
        "Install a compatible engine version or set allowProtocolSkew=True / "
        "AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1 to allow-protocol-skew."
    )
    return VersionCheck(
        ok=False,
        code="protocol_version_mismatch",
        remediation=remediation,
    )
