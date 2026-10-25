"""Unit tests for the amplifier-agent binding driver, against a scripted fake binding.

The driver runs inside the trial container under the binding's interpreter. Here it is loaded by
path with a fake `amplifier_agent` module in sys.modules, whose turns replay event shapes recorded
from amplifier-agent.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import sys
import types
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

DRIVER_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "jobbench" / "agents" / "amplifier_agent_driver"
)
SECRET = "sk-test-secret-value"


class FakeAgentError(Exception):
    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(message)
        self.code, self.message, self.remedy = code, message, remedy
        self.category, self.retryable, self.details = "provider", False, None


def _ns(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _ns(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_ns(item) for item in value]
    return value


def _event(kind: str, payload) -> SimpleNamespace:
    return SimpleNamespace(
        contract_version="turn-events/1",
        session_id="s-1",
        turn_id="t-1",
        sequence=0,
        at=None,
        type=kind,
        payload=payload,
    )


def _tool(call_id: str, outcome: str) -> list[SimpleNamespace]:
    error = None if outcome == "completed" else FakeAgentError("tool_completion_unknown", "x", "y")
    resolution = {"call_id": call_id, "outcome": outcome, "content": "{}", "error": error}
    return [
        _event("tool_call", _ns({"call": {"call_id": call_id, "name": "bash", "arguments": {}}})),
        _event("tool_result", _ns({"resolution": resolution})),
    ]


def _terminal(state: str, error=None) -> SimpleNamespace:
    entry = {"provider": "openai", "model": "gpt-x", "tokens_in": 10, "tokens_out": 2}
    entry["cost"] = {"USD": Decimal("0.0115952")}
    payload = SimpleNamespace(
        state=state,
        content=[SimpleNamespace(text="Done.")],
        error=error,
        usage=_ns({"entries": [entry]}),
    )
    return _event("terminal", payload)


def _turn(*outcomes: str, state: str = "success", error=None) -> list[SimpleNamespace]:
    events = [_event("turn_started", _ns({"continuation": "fresh"}))]
    events.append(_event("usage", _ns({"snapshot": {"entries": [{"tokens_in": None}]}})))
    for index, outcome in enumerate(outcomes):
        events += _tool(f"call_{index}", outcome)
    events.append(_event("output_delta", _ns({"content": [{"type": "text", "text": "Done."}]})))
    events.append(_terminal(state, error))
    return events


class FakeTurn:
    def __init__(self, events) -> None:
        self._events = events
        self.cancelled = asyncio.Event()

    async def events(self):
        for event in self._events:
            if event == "SIGTERM":
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.wait_for(self.cancelled.wait(), 5)
                continue
            yield event

    async def cancel(self) -> None:
        self.cancelled.set()


class FakeBinding:
    """Records what the driver asks for and replays one scripted turn per start_turn."""

    def __init__(self, turns, create_error: Exception | None = None) -> None:
        self.turns = list(turns)
        self.create_error = create_error
        self.options = None
        self.session_options = None
        self.prompts: list[str] = []

    def module(self) -> types.ModuleType:
        binding = self

        class Context:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

        class Session(Context):
            async def start_turn(self, turn_input):
                binding.prompts.append(turn_input.content[0].text)
                return FakeTurn(binding.turns.pop(0))

        class Agent(Context):
            async def create_session(self, options):
                binding.session_options = options
                return Session()

        async def create_agent(options):
            binding.options = options
            if binding.create_error is not None:
                raise binding.create_error
            return Agent()

        module = types.ModuleType("amplifier_agent")
        module.AgentOptions = SimpleNamespace
        module.SessionOptions = SimpleNamespace
        module.TurnInput = SimpleNamespace
        module.TextPart = lambda text: SimpleNamespace(text=text)
        module.create_agent = create_agent
        return module


def _load_driver(monkeypatch, binding: FakeBinding) -> types.ModuleType:
    monkeypatch.setitem(sys.modules, "amplifier_agent", binding.module())
    path = DRIVER_DIR / "amplifier_agent_driver.py"
    spec = importlib.util.spec_from_file_location("amplifier_agent_driver_under_test", path)
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


@pytest.fixture
def run_driver(tmp_path, monkeypatch):
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    # Set, not deleted, so monkeypatch restores it after the driver writes it.
    monkeypatch.setenv("AMPLIFIER_AGENT_ENGINE_RAW_LLM_PAYLOADS", "")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Fix the bug.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "out"

    def run(binding: FakeBinding, *extra: str) -> int:
        driver = _load_driver(monkeypatch, binding)
        argv = ["--prompt-file", str(prompt), "--working-directory", str(work)]
        argv += ["--provider", "openai", "--model", "gpt-x", "--out", str(out), *extra]
        return driver.main(argv)

    run.out = out
    run.work = work
    return run


def _result(out: Path) -> dict:
    return json.loads((out / "result.json").read_text(encoding="utf-8"))


def test_version_prints_package_versions(monkeypatch, capsys):
    driver = _load_driver(monkeypatch, FakeBinding([]))
    assert driver.main(["--version"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed.keys() == {"amplifier-agent", "amplifier-agent-engine"}


def test_missing_arguments_exit_3(monkeypatch):
    driver = _load_driver(monkeypatch, FakeBinding([]))
    with pytest.raises(SystemExit) as exc:
        driver.main(["--provider", "openai"])
    assert exc.value.code == 3


def test_success_writes_config_stream_and_result(run_driver, capsys):
    binding = FakeBinding([_turn("completed")])
    assert run_driver(binding, "--reasoning-effort", "medium") == 0
    stdout = capsys.readouterr().out
    assert "[tool] bash\n" in stdout
    assert "Done." in stdout
    assert "[tool completed]" not in stdout

    out = run_driver.out
    result = _result(out)
    assert result["state"] == "success"
    assert (result["turns"], result["recovery_turns"]) == (1, 0)
    assert result["per_turn"] == [["success", 0]]
    assert result["usage"][0]["entries"][0]["cost"] == {"USD": "0.0115952"}

    lines = (out / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(_turn("completed"))
    assert json.loads(lines[-1])["payload"]["usage"]["entries"][0]["cost"]["USD"] == "0.0115952"
    assert not (out / "events.jsonl").exists()

    config_text = (out / "run-config.json").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert SECRET not in config_text
    assert config["provider"] == "openai"
    assert config["reasoning_effort"] == "medium"
    assert config["tools"] == "built-in defaults"
    assert config["max_recovery_turns"] == 2
    assert "instructions" not in config
    assert (out / "host-config.json").read_text(encoding="utf-8") == "{}"

    options = binding.options
    assert options.reasoning_effort == "medium"
    assert options.approvals == "allow"
    assert options.tool_error_policy == "continue"
    assert options.sessions_directory == str(out / "sessions")
    assert not hasattr(options, "tools")
    assert options.working_directory == str(run_driver.work)
    assert not hasattr(options, "instructions")
    assert binding.session_options.persistence == "durable"
    assert binding.prompts == ["Fix the bug."]


def test_raw_llm_payloads_off_by_default(run_driver):
    assert run_driver(FakeBinding([_turn()])) == 0
    config = json.loads((run_driver.out / "run-config.json").read_text(encoding="utf-8"))
    assert config["raw_llm_payloads"] is False
    assert os.environ["AMPLIFIER_AGENT_ENGINE_RAW_LLM_PAYLOADS"] == ""


def test_raw_llm_payloads_flag_sets_the_engine_variable(run_driver):
    assert run_driver(FakeBinding([_turn()]), "--raw-llm-payloads") == 0
    config = json.loads((run_driver.out / "run-config.json").read_text(encoding="utf-8"))
    assert config["raw_llm_payloads"] is True
    assert os.environ["AMPLIFIER_AGENT_ENGINE_RAW_LLM_PAYLOADS"] == "1"


def test_reasoning_effort_omitted_when_not_given(run_driver):
    binding = FakeBinding([_turn()])
    assert run_driver(binding) == 0
    assert not hasattr(binding.options, "reasoning_effort")


def test_unknown_outcome_triggers_a_recovery_turn(run_driver, capsys):
    binding = FakeBinding([_turn("unknown"), _turn("completed")])
    assert run_driver(binding) == 0
    assert "[tool unknown] bash\n" in capsys.readouterr().out

    result = _result(run_driver.out)
    assert (result["turns"], result["recovery_turns"]) == (2, 1)
    assert result["per_turn"] == [["success", 1], ["success", 0]]
    assert len(binding.prompts) == 2
    assert binding.prompts[1].startswith("A tool call in the previous turn ended with an uncertain")


def test_recovery_turns_are_capped(run_driver):
    binding = FakeBinding([_turn("unknown"), _turn("unknown"), _turn("unknown")])
    assert run_driver(binding, "--max-recovery-turns", "1") == 0

    result = _result(run_driver.out)
    assert (result["turns"], result["recovery_turns"]) == (2, 1)
    assert len(binding.turns) == 1


def test_failure_terminal_exits_1(run_driver):
    error = FakeAgentError("provider_unavailable", "upstream 503", "Retry later.")
    binding = FakeBinding([_turn("unknown", state="failure", error=error)])
    assert run_driver(binding) == 1

    result = _result(run_driver.out)
    assert result["state"] == "failure"
    assert result["error"]["code"] == "provider_unavailable"
    assert result["turns"] == 1


def test_retryable_failure_triggers_a_retry_turn(run_driver, monkeypatch):
    delays: list[float] = []

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("asyncio.sleep", no_wait)
    error = FakeAgentError("provider_failed", "The provider request failed.", "Retry the turn.")
    error.retryable = True
    binding = FakeBinding([_turn("completed", state="failure", error=error), _turn("completed")])
    assert run_driver(binding) == 0

    result = _result(run_driver.out)
    assert (result["state"], result["turns"], result["recovery_turns"]) == ("success", 2, 1)
    assert result["per_turn"] == [["failure", 0], ["success", 0]]
    assert binding.prompts[1].startswith(
        "The previous turn stopped because a model request failed."
    )
    assert delays == [15]


def test_agent_error_at_create_agent_exits_3(run_driver):
    error = FakeAgentError("credentials_missing", "OPENAI_API_KEY is not set", "Set it.")
    assert run_driver(FakeBinding([], create_error=error)) == 3

    result = _result(run_driver.out)
    assert result["state"] == "driver_error"
    assert result["error"]["code"] == "credentials_missing"
    assert result["error"]["message"] == "OPENAI_API_KEY is not set"
    assert result["turns"] == 0


def test_sigterm_cancels_the_turn_and_exits_2(run_driver):
    events = _turn()
    events[-1:] = ["SIGTERM", _terminal("cancelled")]
    binding = FakeBinding([events, _turn()])
    assert run_driver(binding) == 2

    result = _result(run_driver.out)
    assert result["state"] == "cancelled"
    assert result["signal"] == "SIGTERM"
    assert result["turns"] == 1
    assert len(binding.turns) == 1
