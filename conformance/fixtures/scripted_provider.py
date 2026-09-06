"""Deterministic model responses and independent provider-work observations."""

import asyncio
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage

SCENARIOS = json.loads((Path(__file__).parents[1] / "scenarios" / "turns.json").read_text())


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return ""


class ScriptedFactory:
    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []
        self.active = 0
        self.entered = asyncio.Event()
        self.settled = asyncio.Event()
        self.settled.set()

    async def __call__(self, config: Any, coordinator: Any) -> "ScriptedProvider":
        observed_config = {
            name: copy.deepcopy(getattr(config, name))
            for name in ("provider", "model", "instructions", "workspace", "extra_request_params")
        }
        observed_config["storage"] = str(config.storage)
        return ScriptedProvider(self, config.model, coordinator, observed_config)


class ScriptedProvider:
    name = "anthropic"

    def __init__(
        self, factory: ScriptedFactory, model: str, coordinator: Any, config: dict[str, Any]
    ) -> None:
        self.factory = factory
        self.model = model
        self.coordinator = coordinator
        self.index = 0
        self.script = factory.script
        self.config = config

    def get_info(self) -> Any:
        return SimpleNamespace(defaults={"model": self.model}, capabilities=["tools"])

    def parse_tool_calls(self, response: ChatResponse) -> list[ToolCall]:
        return response.tool_calls or []

    async def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        payload = request.model_dump(mode="json")
        self.factory.requests.append(copy.deepcopy(payload))
        if ledger := os.environ.get("CONFORMANCE_PROVIDER_REQUEST_LOG"):
            with Path(ledger).open("a") as output:
                output.write(json.dumps(payload) + "\n")
        if (
            self.factory.script is None
            and self.script is not None
            and self.index >= len(self.script)
        ):
            self.script = None
            self.index = 0
        if self.script is None:
            message_text = [_text(message.get("content")) for message in payload["messages"]]
            embedded = next(
                (
                    text.removeprefix("conformance-script:")
                    for text in reversed(message_text)
                    if text.startswith("conformance-script:")
                ),
                None,
            )
            if embedded is not None:
                self.script = json.loads(embedded)
                if not isinstance(self.script, list) or not self.script:
                    raise AssertionError("A conformance script must contain response steps.")
            else:
                scenario = next(
                    (
                        case
                        for text in reversed(message_text)
                        for case in SCENARIOS
                        if _text(case["input"].get("content")) == text
                        and case["input"].get("content")
                    ),
                    SCENARIOS[0],
                )
                if "Preserve this history" in message_text:
                    scenario = next(case for case in SCENARIOS if case["id"] == "history")
                self.script = scenario["provider"]
        if self.index >= len(self.script):
            raise AssertionError("The engine requested an unscripted provider completion.")
        step = self.script[self.index]
        self.index += 1
        self.factory.active += 1
        self.factory.entered.set()
        self.factory.settled.clear()
        try:
            if step.get("observe_request"):
                await self.coordinator.hooks.emit("org.example.request", {"payload": payload})
            if step.get("observe_config"):
                await self.coordinator.hooks.emit(
                    "org.example.config", {"payload": copy.deepcopy(self.config)}
                )
            for event in step.get("events", []):
                await self.coordinator.hooks.emit(event["type"], event["data"])
            for chunk in step.get("chunks", []):
                await self.coordinator.hooks.emit(
                    "llm:stream_block_delta", {"block_type": "text", "text": chunk}
                )
                await asyncio.sleep(0)
            if step.get("block"):
                await asyncio.Event().wait()
            if step.get("failure"):
                failure = RuntimeError("Scripted provider failure")
                failure.retryable = bool(step.get("failure_retryable", False))
                raise failure
            tool = step.get("tool")
            tools = step.get("tools")
            usage = dict(
                step.get("usage", {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9})
            )
            if "cost_usd" in usage:
                usage["cost_usd"] = Decimal(usage["cost_usd"])
            return ChatResponse(
                content=[TextBlock(text=step.get("text", ""))],
                tool_calls=[
                    ToolCall(id=f"call-{self.index}-{index}", name=item["name"],
                             arguments=item.get("arguments", {}))
                    for index, item in enumerate(tools)
                ] if tools else [
                    ToolCall(
                        id=f"call-{self.index}", name=tool["name"], arguments=tool["arguments"]
                    )
                ]
                if tool
                else None,
                usage=Usage(**usage),
            )
        finally:
            self.factory.active -= 1
            if self.factory.active == 0:
                self.factory.settled.set()


def install(script: list[dict[str, Any]] | None = None) -> ScriptedFactory:
    from amplifier_agent_engine._engine import assembly

    factory = ScriptedFactory(script)
    assembly._provider_factory = factory
    return factory
