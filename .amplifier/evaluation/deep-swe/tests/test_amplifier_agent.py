"""Shape of the amplifier-agent arm: run command, install steps, embedded driver."""

from __future__ import annotations

import importlib.util
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepswe_agents import AGENTS, LOCAL_SOURCE_AGENTS
from deepswe_agents.amplifier_agent import DRIVER_DIR, DRIVER_FILE, SHIM_PATH, AmplifierAgent
from deepswe_agents.base import DEFAULT_AMPLIFIER_AGENT_REF, AmplifierBaseAgent

PACKAGE_DRIVER_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "deepswe_agents" / "amplifier_agent_driver"
)
SHA = "0123456789abcdef0123456789abcdef01234567"
PINNED_REF = f"git+https://github.com/microsoft/amplifier-agent@{SHA}"


def _agent(model: str, ref: str = DEFAULT_AMPLIFIER_AGENT_REF) -> AmplifierAgent:
    """An arm with only the attributes these methods read; pier's __init__ is not needed."""
    agent = AmplifierAgent.__new__(AmplifierAgent)
    agent._parsed_model_name = model
    agent._amplifier_agent_ref = ref
    agent._raw_llm_payloads = False
    return agent


def _install_script(agent: AmplifierAgent) -> str:
    return "\n".join(step.run for step in agent.agent_install_steps())


def test_registered_with_local_source():
    assert AGENTS["amplifier-agent"] == "deepswe_agents.amplifier_agent:AmplifierAgent"
    assert "amplifier-agent" in LOCAL_SOURCE_AGENTS
    assert AmplifierAgent.name() == "amplifier-agent"


def test_run_command_openai_pins_reasoning_effort():
    command = _agent("gpt-5.6-terra").run_command("/tmp/pier-instruction.txt")
    assert shlex.split(command) == [
        "amplifier-agent-driver",
        "--prompt-file",
        "/tmp/pier-instruction.txt",
        "--working-directory",
        "/app",
        "--provider",
        "openai",
        "--model",
        "gpt-5.6-terra",
        "--reasoning-effort",
        "medium",
        "--out",
        "/root/amplifier-agent-run",
    ]


def test_run_command_anthropic_has_no_reasoning_effort():
    argv = shlex.split(_agent("claude-sonnet-5").run_command("/tmp/pier-instruction.txt"))
    assert argv[argv.index("--provider") + 1] == "anthropic"
    assert "--reasoning-effort" not in argv
    assert "$(cat" not in " ".join(argv)


@pytest.mark.parametrize("ref", [DEFAULT_AMPLIFIER_AGENT_REF, PINNED_REF])
def test_install_pins_binding_and_engine_to_the_same_ref(ref):
    script = _install_script(_agent("claude-sonnet-5", ref))
    engine = f"amplifier-agent-engine @ {ref}#subdirectory=packages/engine"
    binding = f"amplifier-agent @ {ref}#subdirectory=packages/python"
    assert "uv venv --python 3.13 /opt/amplifier-agent" in script
    assert f"printf '%s\\n' {shlex.quote(engine)} > /tmp/aa-overrides.txt" in script
    assert (
        "uv pip install --compile-bytecode --python /opt/amplifier-agent/bin/python "
        f"--overrides /tmp/aa-overrides.txt {shlex.quote(binding)}"
    ) in script
    assert script.rstrip().endswith("amplifier-agent-driver --version")


def test_run_command_passes_raw_llm_payloads_when_enabled():
    agent = _agent("gpt-5.6-terra")
    assert "--raw-llm-payloads" not in agent.run_command("/tmp/pier-instruction.txt")
    agent._raw_llm_payloads = True
    argv = shlex.split(agent.run_command("/tmp/pier-instruction.txt"))
    assert argv[-1] == "--raw-llm-payloads"


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False), (False, False)])
def test_raw_llm_payloads_kwarg_is_coerced(monkeypatch, value, expected):
    monkeypatch.setattr(AmplifierBaseAgent, "__init__", lambda self, *a, **k: None)
    assert AmplifierAgent(raw_llm_payloads=value)._raw_llm_payloads is expected


def test_ref_kwarg_defaults_to_the_default_branch_and_can_be_overridden(monkeypatch):
    monkeypatch.setattr(AmplifierBaseAgent, "__init__", lambda self, *a, **k: None)
    assert AmplifierAgent()._amplifier_agent_ref == DEFAULT_AMPLIFIER_AGENT_REF
    assert AmplifierAgent(amplifier_agent_ref=PINNED_REF)._amplifier_agent_ref == PINNED_REF


def test_install_writes_the_shim():
    script = _install_script(_agent("claude-sonnet-5"))
    shim = (
        "#!/bin/sh\nexec /opt/amplifier-agent/bin/python "
        '/opt/amplifier-agent-driver/amplifier_agent_driver.py "$@"\n'
    )
    assert f"printf %s {shlex.quote(shim)} > {SHIM_PATH} && chmod +x {SHIM_PATH}" in script


def test_install_embeds_the_driver_byte_identical():
    expected = (PACKAGE_DRIVER_DIR / DRIVER_FILE).read_bytes()
    prefix = "printf %s "
    suffix = f" > {DRIVER_DIR}/{DRIVER_FILE}"
    steps = [s.run for s in _agent("claude-sonnet-5").agent_install_steps()]
    matching = [run for run in steps if run.startswith(prefix) and run.endswith(suffix)]
    assert len(matching) == 1
    (written,) = shlex.split(matching[0][len(prefix) : -len(suffix)])
    assert written.encode("utf-8") == expected


def test_run_pins_the_default_branch_to_a_commit_in_the_ref_kwarg_form(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "deepswe_run", Path(__file__).parents[1] / "run.py"
    )
    assert spec is not None and spec.loader is not None
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    calls: list[list[str]] = []

    def ls_remote(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=f"{SHA}\tHEAD\n", stderr="")

    monkeypatch.setattr(run.subprocess, "run", ls_remote)
    pins = run.resolve_pins(["amplifier-agent"], enabled=True)
    assert calls == [["git", "ls-remote", "https://github.com/microsoft/amplifier-agent", "HEAD"]]
    assert pins == {"amplifier-agent": {"amplifier_agent_ref": PINNED_REF}}
    assert PINNED_REF.startswith(f"{DEFAULT_AMPLIFIER_AGENT_REF}@")
