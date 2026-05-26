"""SC-C: spawn_agent() must reject approval.on_request loudly in v1.

Mirror of wrappers/typescript/test/spawn-rejects-approval.test.ts.

Per amendment §5.3, the Mode A wire has no mid-turn request channel.
Passing a non-null `on_request` must raise `AaaError` with code
`approval_not_supported_in_v1`, classification 'protocol', BEFORE any
subprocess work is done.

The earlier draft of the amendment had the wrapper accept the callback
and log a stderr warning; the SC-C adversarial review found that
warning-only acceptance ships silent auto-allow to a host author who
believed their callback was wired up. We reject loudly instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_agent_client import AaaError, spawn_agent


@pytest.mark.asyncio
async def test_spawn_agent_rejects_approval_on_request_loudly() -> None:
    """spawn_agent raises AaaError(approval_not_supported_in_v1) when approval.on_request is provided."""

    async def stub_on_request(_req: Any) -> dict[str, Any]:
        return {"decision": "allow"}

    with pytest.raises(AaaError) as exc_info:
        await spawn_agent(
            lifecycle="one-shot",
            session_id="sid",
            approval={"on_request": stub_on_request},
        )

    err = exc_info.value
    assert err.code == "approval_not_supported_in_v1"
    assert err.classification == "protocol"
