"""Session configuration through the official ACP connection and real binding."""

from acp import PROTOCOL_VERSION
from acp.schema import TextContentBlock
from amplifier_agent import ModelRecord, ProviderRecord
from amplifier_agent_acp import _agent

from tests.support.engine import provision


async def test_model_and_reasoning_selection_survive_a_new_connection(connect, monkeypatch, tmp_path):
    monkeypatch.setenv("AMPLIFIER_AGENT_SESSIONS_DIRECTORY", str(tmp_path / "sessions"))
    monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", "claude-sonnet-5")
    provision(monkeypatch, [{"text": "Saved"}])

    async def discover():
        provider = ProviderRecord("anthropic", "Anthropic", True, "found", ["ANTHROPIC_API_KEY"])
        return [
            (provider, ModelRecord("claude-sonnet-5", "Sonnet", reasoning_efforts=["low", "high"])),
            (provider, ModelRecord("claude-opus-5", "Opus", reasoning_efforts=["low", "high"])),
        ]

    monkeypatch.setattr(_agent, "discover", discover)
    async with connect() as (connection, recorder):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        session = (await connection.new_session(cwd=str(tmp_path))).session_id
        await recorder.until(
            lambda message: message.get("params", {}).get("update", {}).get("sessionUpdate") == "config_option_update"
        )
        result = await connection.set_config_option(
            config_id="model", session_id=session, value="anthropic/claude-opus-5"
        )
        assert (
            next(option for option in result.config_options if option.id == "model").current_value
            == "anthropic/claude-opus-5"
        )
        result = await connection.set_config_option(config_id="reasoning_effort", session_id=session, value="low")
        assert (
            next(option for option in result.config_options if option.id == "reasoning_effort").current_value == "low"
        )
        await connection.prompt(session_id=session, prompt=[TextContentBlock(type="text", text="Keep this history")])

    async with connect() as (connection, recorder):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        loaded = await connection.load_session(cwd=str(tmp_path), session_id=session)
        await recorder.until(
            lambda message: message.get("params", {}).get("update", {}).get("sessionUpdate") == "config_option_update"
        )
        options = {option.id: option.current_value for option in loaded.config_options}
        assert options["model"] == "anthropic/claude-opus-5"
        assert options["reasoning_effort"] == "low"
        assert any(
            message.get("params", {}).get("update", {}).get("sessionUpdate") == "user_message_chunk"
            for message in recorder.wire
        )


async def test_config_option_approvals_are_available_without_a_host_policy(connect, tmp_path):
    async with connect() as (connection, _):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        created = await connection.new_session(cwd=str(tmp_path))
        approvals = next(option for option in created.config_options if option.id == "approvals")
        assert approvals.current_value == "allow-reads"
        result = await connection.set_config_option(config_id="approvals", session_id=created.session_id, value="ask")
        assert next(option for option in result.config_options if option.id == "approvals").current_value == "ask"
