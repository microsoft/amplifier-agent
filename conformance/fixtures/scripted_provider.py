"""Deterministic model responses and independent provider-work observations."""

import asyncio
import copy
import json
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
        return ScriptedProvider(self, config.model, coordinator)


class ScriptedProvider:
    name = "anthropic"

    def __init__(self, factory: ScriptedFactory, model: str, coordinator: Any) -> None:
        self.factory = factory
        self.model = model
        self.coordinator = coordinator
        self.index = 0
        self.script = factory.script

    def get_info(self) -> Any:
        return SimpleNamespace(defaults={"model": self.model}, capabilities=["tools"])

    def parse_tool_calls(self, response: ChatResponse) -> list[ToolCall]:
        return response.tool_calls or []

    async def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        payload = request.model_dump(mode="json")
        self.factory.requests.append(copy.deepcopy(payload))
        if self.script is None:
            message_text = [_text(message.get("content")) for message in payload["messages"]]
            scenario = next(
                (
                    case
                    for case in SCENARIOS
                    if _text(case["input"].get("content")) in message_text
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
                raise RuntimeError("Scripted provider failure")
            tool = step.get("tool")
            usage = dict(
                step.get("usage", {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9})
            )
            if "cost_usd" in usage:
                usage["cost_usd"] = Decimal(usage["cost_usd"])
            return ChatResponse(
                content=[TextBlock(text=step.get("text", ""))],
                tool_calls=[
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
