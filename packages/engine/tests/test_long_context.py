import copy
from types import SimpleNamespace
from typing import Any

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.adapters import (
    AmplifierRuntime,
    ProviderAdapter,
    StructuredContext,
)
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._records import (
    AgentOptions,
    ConversationMessage,
    Progress,
    SessionOptions,
    TextPart,
    TurnInput,
)
from amplifier_core.llm_errors import ContextLengthError
from amplifier_core.message_models import ChatResponse, TextBlock, Usage

FIRST = "Project codename is HERON; remember it."


def chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(message)) for message in messages)


def long_history() -> list[ConversationMessage]:
    history = [ConversationMessage("user", [TextPart(FIRST)])]
    for index in range(8):
        history.append(ConversationMessage("assistant", [TextPart(f"reply {index} " + "a" * 10_000)]))
        history.append(ConversationMessage("user", [TextPart(f"follow-up {index} " + "u" * 10_000)]))
    return history


async def filled_context() -> StructuredContext:
    context = StructuredContext(131_072, hooks=None)
    await context.add_message({"role": "system", "content": "You are careful."})
    await context.add_message({"role": "user", "content": FIRST})
    for index in range(12):
        await context.add_message({
            "role": "assistant",
            "content": f"Reading file {index}.",
            "tool_calls": [{"id": f"call-{index}", "tool": "read_file", "arguments": {}}],
        })
        await context.add_message({
            "role": "tool", "name": "read_file", "tool_call_id": f"call-{index}",
            "content": f"contents {index} " + "x" * 4_000,
        })
        await context.add_message({"role": "user", "content": f"Next step {index}."})
    return context


async def test_request_view_compacts_while_the_transcript_is_unchanged():
    context = await filled_context()
    stored = copy.deepcopy(await context.get_messages())
    view = await context.get_messages_for_request(token_budget=4_000)
    assert chars(view) < chars(stored)
    assert await context.get_messages() == stored
    assert any(message.get("role") == "user" and message.get("content") == FIRST for message in view)
    assert context.compact_threshold == 0.8


async def test_skill_context_follows_the_compacted_view():
    context = await filled_context()
    context.skill_context = ["extra"]
    view = await context.get_messages_for_request(token_budget=4_000)
    assert view[-1] == {"role": "user", "content": "extra"}
    assert chars(view) < chars(await context.get_messages())


def test_opaque_reasoning_envelopes_do_not_count_toward_the_estimate():
    context = StructuredContext(131_072, hooks=None)
    envelope = "x" * 400_000
    visible = {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "Considering."},
        {"type": "reasoning", "content": [{"type": "text"}]},
        {"type": "text", "text": "Reply"},
    ], "thinking_block": {"type": "thinking", "thinking": "Considering."}}
    sealed = copy.deepcopy(visible)
    sealed["content"][0]["signature"] = envelope
    sealed["content"][1]["content"][0]["encrypted_content"] = envelope
    sealed["thinking_block"]["signature"] = envelope
    assert context._estimate_tokens([sealed]) == context._estimate_tokens([visible])
    quoted = {"role": "user", "content": [{"type": "text", "text": "q", "signature": envelope}]}
    assert context._estimate_tokens([quoted]) > 100_000
    assert sealed["content"][0]["signature"] == envelope


class Capable:
    def get_model_info(self) -> str:
        return "info"

    def request_budget(self, request: Any) -> str:
        return "budget"

    def recover_context_overflow(self, request: Any, error: Any) -> str:
        return "recovered"

    def stream(self, request: Any) -> str:
        return "stream"


def adapter(provider: Any) -> ProviderAdapter:
    runtime = SimpleNamespace(config=SimpleNamespace(provider="anthropic", model="claude-sonnet-5"))
    return ProviderAdapter(runtime, provider)  # type: ignore[arg-type]


def test_provider_adapter_forwards_only_budget_and_overflow_capabilities():
    present = adapter(Capable())
    assert present.get_model_info() == "info"
    assert present.request_budget(None) == "budget"
    assert present.recover_context_overflow(None, None) == "recovered"
    assert not hasattr(present, "stream")
    absent = adapter(object())
    for name in ("get_model_info", "request_budget", "recover_context_overflow", "stream"):
        assert not hasattr(absent, name)
        assert getattr(absent, name, None) is None


class Provider:
    name = "anthropic"

    def __init__(self, model: str, factory: "Factory") -> None:
        self.model, self.factory = model, factory

    def get_info(self) -> Any:
        return SimpleNamespace(defaults={"model": self.model}, capabilities=["tools"])

    def parse_tool_calls(self, response: ChatResponse) -> list[Any]:
        return response.tool_calls or []

    async def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        self.factory.requests.append(request.model_dump(mode="json"))
        if len(self.factory.requests) <= self.factory.overflows:
            raise ContextLengthError("too long")
        return ChatResponse(
            content=[TextBlock(text="Done")],
            usage=Usage(input_tokens=7, output_tokens=2, total_tokens=9),
        )


class Recovering(Provider):
    def recover_context_overflow(self, request: Any, error: Any, **kwargs: Any) -> dict[str, int]:
        estimate = kwargs["context_estimate"]
        return {
            "estimated_input_tokens": estimate,
            "input_limit_tokens": estimate // 2,
            "context_token_budget": estimate * 3 // 4,
        }


class SmallWindow(Provider):
    def get_model_info(self) -> Any:
        return SimpleNamespace(context_window=24_000, max_output_tokens=2_000)


class Factory:
    def __init__(self, kind: type[Provider], overflows: int = 0) -> None:
        self.kind, self.overflows = kind, overflows
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, config: Any, coordinator: Any) -> Provider:
        return self.kind(config.model, self)


async def run_turn(monkeypatch, factory: Factory, history: list[ConversationMessage] | None = None):
    monkeypatch.setattr(assembly, "_provider_factory", factory)
    agent = await assembly.create_engine(AgentOptions(provider="anthropic", model="claude-sonnet-5"))
    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        factory.requests.clear()
        turn = await session.start_turn(TurnInput([TextPart("Continue.")], history=history))
        events = [event async for event in turn.events()]
        messages = await session._messages()
        return events, messages
    finally:
        await agent.close()


async def test_unrecoverable_overflow_ends_the_turn_as_context_exceeded(monkeypatch):
    factory = Factory(Provider, overflows=10)
    events, _ = await run_turn(monkeypatch, factory)
    terminal = events[-1]
    assert terminal.type == "terminal"
    assert terminal.payload.state == "failure"
    error = terminal.payload.error
    assert (error.code, error.category, error.retryable) == ("context_exceeded", "provider", False)
    assert error.details == {"provider": "anthropic", "model": "claude-sonnet-5"}
    assert len(factory.requests) == 1


async def test_recoverable_overflow_retries_once_with_a_compacted_request(monkeypatch):
    factory = Factory(Recovering, overflows=1)
    events, messages = await run_turn(monkeypatch, factory, long_history())
    assert events[-1].type == "terminal"
    assert events[-1].payload.state == "success"
    assert len(factory.requests) == 2
    assert chars(factory.requests[1]["messages"]) < chars(factory.requests[0]["messages"])
    assert any(FIRST in str(message) for message in factory.requests[1]["messages"])
    assert sum(1 for message in messages if message["role"] == "assistant") == 9


async def test_compaction_near_the_model_window_is_reported_as_progress(monkeypatch):
    factory = Factory(SmallWindow)
    events, messages = await run_turn(monkeypatch, factory, long_history())
    assert events[-1].payload.state == "success"
    progress = [event for event in events if event.type == "progress"]
    assert len(progress) == 1
    assert isinstance(progress[0].payload, Progress)
    context = progress[0].payload.data["context"]
    assert context["compacted"] is True
    assert set(context) == {"compacted", "estimated_tokens_before", "estimated_tokens_after", "budget"}
    assert all(type(context[key]) is int for key in set(context) - {"compacted"})
    assert context["estimated_tokens_after"] < context["estimated_tokens_before"]
    assert chars(factory.requests[0]["messages"]) < chars(messages)
    assert len([message for message in messages if message["role"] == "user"]) == 10


class RecordingObserver:
    model = "claude-sonnet-5"
    cancelled = False
    inspection_only = False

    def __init__(self) -> None:
        self.reports: list[Any] = []
        self.pending: set[Any] = set()

    def progress(self, data: Any) -> None:
        self.reports.append(data)


def bare_runtime() -> AmplifierRuntime:
    runtime = AmplifierRuntime(resolve(AgentOptions()), session_id="bare", capture=False)
    return runtime


async def test_compaction_report_carries_only_the_contracted_integers():
    runtime = bare_runtime()
    observer = RecordingObserver()
    runtime.observer = observer  # type: ignore[assignment]
    await runtime._compaction("context:compaction", {
        "before_tokens": 900, "after_tokens": 400, "budget": 1_000, "strategy_level": 2,
    })
    await runtime._compaction("context:compaction", {"before_tokens": None, "budget": True})
    runtime.observer = None
    await runtime._compaction("context:compaction", {"before_tokens": 1})
    assert observer.reports == [
        {"context": {"compacted": True, "estimated_tokens_before": 900,
                     "estimated_tokens_after": 400, "budget": 1_000}},
        {"context": {"compacted": True}},
    ]


def test_the_runtime_context_carries_the_session_hooks():
    runtime = AmplifierRuntime(resolve(AgentOptions()), session_id="hooks", capture=False)
    assert runtime.context._hooks is runtime.core.coordinator.hooks
