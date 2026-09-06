from types import SimpleNamespace

import pytest
from amplifier_agent import AgentOptions, SessionOptions, TextPart, Tool, TurnInput, create_agent


@pytest.mark.parametrize("with_tool", [False, True])
async def test_copilot_ecosystem_adapter_captures_effects_and_disables_sdk_authority(
    monkeypatch, with_tool
):
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
                    self.handler(
                        {"type": "assistant.message_delta", "data": {"delta_content": text}}
                    )
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
    async with await create_agent(
        AgentOptions(
            provider="github-copilot",
            model="gpt-5",
            tools=[tool] if with_tool else [],
            approvals="allow",
        )
    ) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
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
