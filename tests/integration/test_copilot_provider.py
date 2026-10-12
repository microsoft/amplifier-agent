import sys
from types import SimpleNamespace

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, Tool, TurnInput, create_agent
import pytest


@pytest.mark.parametrize("with_tool", [False, True])
async def test_copilot_ecosystem_adapter_captures_effects_and_disables_sdk_authority(monkeypatch, with_tool):
    import copilot

    sessions, prompts, effects, clients = [], [], [], []

    class Session:
        session_id = "fixture-session"

        def __init__(self, config):
            self.config = config
            self.disconnected = False

        def on(self, handler):
            def adapted(event):
                handler(SimpleNamespace(type=event["type"], data=SimpleNamespace(**event["data"])))

            self.handler = adapted
            return lambda: None

        async def send(self, prompt, **kwargs):
            prompts.append(prompt)
            should_call = with_tool and "Effect recorded" not in prompt
            if should_call:
                self.handler(
                    {
                        "type": "assistant.message",
                        "data": {
                            "content": "",
                            "tool_requests": [
                                {
                                    "tool_call_id": "call_fixture",
                                    "name": "record",
                                    "arguments": {"value": "fixture"},
                                },
                            ],
                        },
                    }
                )
            else:
                for text in ("Wire ", "reply"):
                    self.handler({"type": "assistant.message_delta", "data": {"delta_content": text}})
                self.handler({"type": "assistant.message", "data": {"content": "Wire reply"}})
            self.handler(
                {
                    "type": "assistant.usage",
                    "data": {
                        "input_tokens": 25,
                        "output_tokens": 2,
                        "cache_read_tokens": 3,
                        "cache_write_tokens": 5,
                    },
                }
            )
            self.handler({"type": "session.idle", "data": {}})
            return "fixture-message"

        async def abort(self):
            return None

        async def disconnect(self):
            self.disconnected = True

    class Client:
        def __init__(self, **kwargs):
            self.settings = kwargs
            self.stopped = False
            clients.append(self)

        async def start(self):
            return None

        async def get_auth_status(self):
            return SimpleNamespace(isAuthenticated=True)

        async def create_session(self, **config):
            session = Session(config)
            sessions.append(session)
            return session

        async def stop(self):
            self.stopped = True

        async def list_models(self):
            return []

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "fixture-token")

    async def execute(arguments, context):
        effects.append(arguments)
        return "Effect recorded"

    tool = Tool(
        "record",
        "Record a value",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute,
    )
    async with (
        await create_agent(
            AgentOptions(
                provider="github-copilot",
                model="gpt-5",
                tools=[tool] if with_tool else [],
                approvals="allow",
            )
        ) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("First input")]))
        assert result.state == "success", result.error
        second = await session.run(TurnInput([TextPart("Continue")]))
        assert second.state == "success", second.error
    assert effects == ([{"value": "fixture"}] if with_tool else [])
    assert "First input" in prompts[-1]
    assert all(session.disconnected for session in sessions)
    assert all(client.stopped for client in clients)
    for session in sessions:
        assert session.config["model"] == "gpt-5"
        assert session.config["enable_session_store"] is False
        assert session.config["enable_host_git_operations"] is False
        assert session.config["enable_skills"] is False
        assert session.config["on_permission_request"] is not None
        assert (
            "record" in session.config["available_tools"]
            if with_tool
            else "record" not in session.config["available_tools"]
        )


@pytest.mark.parametrize("after_output", [False, True])
async def test_copilot_retries_a_dropped_stream_only_before_output(monkeypatch, after_output):
    import copilot

    prompts = []

    class Session:
        session_id = "fixture-session"

        def on(self, handler):
            def adapted(event):
                handler(SimpleNamespace(type=event["type"], data=SimpleNamespace(**event["data"])))

            self.handler = adapted
            return lambda: None

        async def send(self, prompt, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                if after_output:
                    self.handler({"type": "assistant.message_delta", "data": {"delta_content": "Wire "}})
                self.handler({"type": "session.error", "data": {"message": "connection reset by peer"}})
                return "fixture-message"
            for text in ("Wire ", "reply"):
                self.handler({"type": "assistant.message_delta", "data": {"delta_content": text}})
            self.handler({"type": "assistant.message", "data": {"content": "Wire reply"}})
            self.handler({"type": "session.idle", "data": {}})
            return "fixture-message"

        async def abort(self):
            return None

        async def disconnect(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

        async def get_auth_status(self):
            return SimpleNamespace(isAuthenticated=True)

        async def create_session(self, **config):
            return Session()

        async def stop(self):
            return None

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "fixture-token")
    async with (
        await create_agent(AgentOptions(provider="github-copilot", model="gpt-5")) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Hello")]))
        events = [event async for event in turn.events()]
    result = events[-1].payload
    deltas = ["".join(part.text for part in event.payload.content) for event in events if event.type == "output_delta"]
    if after_output:
        assert result.state == "failure"
        assert result.error is not None
        assert (result.error.code, result.error.retryable) == ("provider_failed", True)
        assert deltas == ["Wire "]
        assert len(prompts) == 1
    else:
        assert result.state == "success", result.error
        assert deltas == ["Wire ", "reply"]
        assert len(prompts) == 2


async def test_missing_copilot_extra_fails_at_construction(monkeypatch):
    monkeypatch.setitem(sys.modules, "amplifier_module_provider_github_copilot", None)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    with pytest.raises(AgentError) as caught:
        await create_agent(AgentOptions(provider="github-copilot", model="gpt-5"))
    assert caught.value.code == "engine_unavailable"
    assert caught.value.category == "lifecycle"
    assert caught.value.remedy == "Install amplifier-agent[github-copilot]."
    assert caught.value.details == {"provider": "github-copilot"}
