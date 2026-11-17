"""Project the public engine-authored scope into the optional Instance profile."""

import re
from typing import Any
import uuid

from amplifier_agent import ApprovalRequest, ReusableApproval

NAMESPACE = "com.microsoft.amplifier.approval-policy"
PRODUCER = "com.microsoft.amplifier-agent.approvals/1"


def instance_owner(metadata: dict[str, Any]) -> str | None:
    profile = metadata.get(NAMESPACE)
    if not isinstance(profile, dict) or type(profile.get("version")) is not int or profile["version"] != 1:
        return None
    if profile.get("owner") != "instance" or not isinstance(profile.get("instanceId"), str):
        return None
    try:
        identifier = str(uuid.UUID(profile["instanceId"]))
    except ValueError:
        return None
    return identifier if identifier == profile["instanceId"] else None


def negotiated() -> dict[str, Any]:
    return {NAMESPACE: {"version": 1, "owner": "instance", "producer": PRODUCER, "reusableRequests": True}}


def metadata(request: ApprovalRequest) -> dict[str, Any] | None:
    scope = request.reusable_approval
    try:
        description_bytes = (
            len(scope.description.encode("utf-8"))
            if type(scope) is ReusableApproval and isinstance(scope.description, str)
            else 0
        )
    except UnicodeEncodeError:
        return None
    if (
        type(scope) is not ReusableApproval
        or set(vars(scope)) != {"version", "producer", "key", "description", "authority_lifetime"}
        or type(scope.version) is not int
        or scope.version != 1
        or scope.producer != PRODUCER
        or not isinstance(scope.key, str)
        or re.fullmatch("[0-9a-f]{64}", scope.key) is None
        or not isinstance(scope.description, str)
        or not 1 <= description_bytes <= 512
        or any(not char.isprintable() for char in scope.description)
        or scope.authority_lifetime != "request"
        or not request.call_id
    ):
        return None
    return {
        NAMESPACE: {
            "version": 1,
            "producer": scope.producer,
            "key": scope.key,
            "description": scope.description,
            "allowOptionId": "allow_once",
            "authorityLifetime": scope.authority_lifetime,
        }
    }
