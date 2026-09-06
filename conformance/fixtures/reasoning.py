"""Opaque native reasoning envelopes with independently enforced continuation requirements."""

import base64
import json

from conformance.fixtures.provider_services import provider_service


def reasoning_service(provider, requests, mode, *, tool=None, tool_arguments=None):
    signatures = []
    original = {
        "anthropic": "fixture-signature",
        "openai": "opaque-fixture-reasoning",
        "gemini": "c2lnbmF0dXJl",
    }[provider]

    def transform(frame, request_number):
        while len(signatures) < request_number:
            signature = f"reasoning-envelope-{len(signatures) + 1}:"
            if mode in {"size", "tool"} and not signatures:
                signature += "x" * (2 * 1024 * 1024)
            if provider == "gemini":
                signature = base64.b64encode(signature.encode()).decode()
            signatures.append(signature)
        return json.loads(json.dumps(frame).replace(original, signatures[request_number - 1]))

    def validate(body):
        if mode != "tool" or not signatures:
            return None
        expected = signatures[0]
        if provider == "anthropic":
            messages = body.get("messages", [])
            last = messages[-1].get("content", [])
            if not isinstance(last, list) or not any(
                part.get("type") == "tool_result" for part in last
            ):
                return None
            assistant = messages[-2].get("content", [])
            valid = (
                assistant
                and assistant[0].get("type") == "thinking"
                and assistant[0].get("thinking") == "Considering."
                and assistant[0].get("signature") == expected
                and any(part.get("type") == "tool_use" for part in assistant)
            )
        elif provider == "openai":
            items = body.get("input", [])
            if not items or items[-1].get("type") != "function_call_output":
                return None
            reasoning = next(
                (
                    index
                    for index, item in enumerate(items)
                    if item.get("type") == "reasoning" and item.get("encrypted_content") == expected
                ),
                None,
            )
            call = next(
                (index for index, item in enumerate(items) if item.get("type") == "function_call"),
                None,
            )
            valid = reasoning is not None and call is not None and reasoning < call
        else:
            messages = body.get("contents", [])
            if not any("functionResponse" in part for part in messages[-1].get("parts", [])):
                return None
            parts = messages[-2].get("parts", [])
            valid = any(
                part.get("thought") and part.get("thoughtSignature") == expected for part in parts
            )
        return (
            None
            if valid
            else "The active tool round requires its original complete signed reasoning."
        )

    app = provider_service(
        provider,
        requests,
        reasoning=True,
        tool=tool,
        tool_arguments=tool_arguments,
        request_validator=validate,
        frame_transform=transform,
    )
    return app, signatures, validate
