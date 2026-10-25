"""Unit tests for the amplifier-agent adapter and run.py's per-run ref: configure, command, pins."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import run
import yaml

from jobbench import agents
from jobbench.agents.amplifier_agent import (
    DRIVER_FILE,
    DRIVER_SOURCE,
    INSTALL_TIMEOUT_S,
    SHIM_PATH,
    AmplifierAgentAdapter,
)
from jobbench.dataset import Task
from jobbench.dtu import CommandResult

DEEP_SWE_DRIVER = (
    Path(__file__).resolve().parents[2]
    / "deep-swe"
    / "src"
    / "deepswe_agents"
    / "amplifier_agent_driver"
)
PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "agents" / "amplifier-agent.bake.yaml"
REPO = "https://github.com/microsoft/amplifier-agent"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "89abcdef0123456789abcdef0123456789abcdef"


class FakeDTU:
    """Records exec and push calls; `fail_on` makes the first exec containing it return 1."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.execs: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.pushes: list[tuple[Path, str]] = []
        self.fail_on = fail_on

    async def exec_cmd(self, command, *, timeout_s=600.0, stream_to_logfile=None):
        self.execs.append(list(command))
        self.timeouts.append(timeout_s)
        failed = self.fail_on is not None and self.fail_on in command[-1]
        return CommandResult(
            returncode=1 if failed else 0, stdout="", stderr="boom" if failed else "", elapsed_s=0.0
        )

    async def file_push(self, source, destination, *, timeout_s=300.0):
        self.pushes.append((Path(source), destination))


def _configure(model: str, dtu: Any, **kwargs: Any) -> AmplifierAgentAdapter:
    adapter = AmplifierAgentAdapter(ref=SHA, **kwargs)
    asyncio.run(adapter.configure(dtu, model=model))
    return adapter


def test_registered():
    assert agents.names() == [
        "amplifier-agent",
        "amplifier-foundation",
        "opencode-amplifier",
        "opencode-vanilla",
    ]
    adapter = agents.get("amplifier-agent", ref=SHA)
    assert type(adapter) is AmplifierAgentAdapter
    assert adapter.image_alias == "jobbench-amplifier-agent"
    assert adapter.session_dirs == ("/root/amplifier-agent-run",)
    assert adapter.config_files == ("/root/amplifier-agent-run/run-config.json",)
    assert adapter.metrics_source == "events"


def test_bake_profile_installs_binding_and_engine_from_the_default_branch():
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    (setup,) = profile["provision"]["setup_cmds"]
    engine = f"amplifier-agent-engine @ git+{REPO}#subdirectory=packages/engine"
    binding = f"amplifier-agent @ git+{REPO}#subdirectory=packages/python"
    assert "uv venv --python 3.13 /opt/amplifier-agent" in setup
    assert f"printf '%s\\n' \"{engine}\" > /tmp/aa-overrides.txt" in setup
    assert (
        "uv pip install --compile-bytecode --python /opt/amplifier-agent/bin/python "
        f'--overrides /tmp/aa-overrides.txt "{binding}"'
    ) in setup
    assert setup.count("git+https://") == 2
    (readiness,) = profile["readiness"]
    assert readiness["command"] == '/opt/amplifier-agent/bin/python -c "import amplifier_agent"'


def test_configure_installs_at_the_ref_then_pushes_driver_and_checks_version(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    dtu = FakeDTU()
    _configure("claude-sonnet-5", dtu)

    assert all(command[:2] == ["bash", "-c"] for command in dtu.execs)
    scripts = [command[-1] for command in dtu.execs]
    url = f"git+{REPO}@{SHA}"
    engine = f"amplifier-agent-engine @ {url}#subdirectory=packages/engine"
    binding = f"amplifier-agent @ {url}#subdirectory=packages/python"
    assert scripts[0] == (
        'export PATH="$HOME/.local/bin:$PATH" && '
        f"printf '%s\\n' {shlex.quote(engine)} > /tmp/aa-overrides.txt && "
        "uv pip install --compile-bytecode --python /opt/amplifier-agent/bin/python "
        f"--overrides /tmp/aa-overrides.txt {shlex.quote(binding)}"
    )
    assert dtu.timeouts[0] == INSTALL_TIMEOUT_S == 600.0
    assert scripts[1] == "mkdir -p /opt/amplifier-agent-driver /root/.local/bin"
    assert dtu.pushes == [
        (DRIVER_SOURCE / DRIVER_FILE, f"/opt/amplifier-agent-driver/{DRIVER_FILE}")
    ]
    shim = (
        "#!/bin/sh\nexec /opt/amplifier-agent/bin/python "
        '/opt/amplifier-agent-driver/amplifier_agent_driver.py "$@"\n'
    )
    assert f"printf %s {shlex.quote(shim)} > {SHIM_PATH} && chmod +x {SHIM_PATH}" in scripts[2]
    profile = "printf 'export PATH=\"/root/.local/bin:$PATH\"\\n' > "
    assert f"{profile}/etc/profile.d/jobbench-amplifier-agent.sh" in scripts[2]
    assert scripts[-1] == f"{SHIM_PATH} --version"


def test_configure_fails_when_the_install_fails():
    dtu = FakeDTU(fail_on="uv pip install")
    with pytest.raises(agents.AdapterError, match="at install"):
        _configure("claude-sonnet-5", dtu)
    assert len(dtu.execs) == 1 and dtu.pushes == []


def test_configure_fails_when_driver_version_fails():
    with pytest.raises(agents.AdapterError, match="driver --version"):
        _configure("claude-sonnet-5", FakeDTU(fail_on="--version"))


def test_configure_without_a_ref_fails_before_touching_the_dtu():
    dtu = FakeDTU()
    with pytest.raises(agents.AdapterError, match="no ref to install"):
        asyncio.run(AmplifierAgentAdapter().configure(dtu, model="claude-sonnet-5"))
    assert dtu.execs == [] and dtu.pushes == []


def test_configure_openai_requires_host_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    dtu = FakeDTU()
    with pytest.raises(agents.AdapterError, match="OPENAI_BASE_URL"):
        _configure("gpt-5.6-terra", dtu)
    assert dtu.execs == [] and dtu.pushes == []


def test_configure_writes_no_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    dtu = FakeDTU()
    _configure("gpt-5.6-terra", dtu)
    assert not any("sk-test-secret" in " ".join(command) for command in dtu.execs)


def test_command_openai_pins_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    command = _configure("gpt-5.6-terra", FakeDTU()).command()
    assert command[:2] == ["bash", "-c"]
    assert shlex.split(command[2]) == [
        SHIM_PATH,
        "--prompt-file",
        "/workspace/prompt.txt",
        "--working-directory",
        "/workspace",
        "--provider",
        "openai",
        "--model",
        "gpt-5.6-terra",
        "--reasoning-effort",
        "medium",
        "--out",
        "/root/amplifier-agent-run",
    ]


def test_command_anthropic_has_no_reasoning_effort():
    argv = shlex.split(_configure("claude-sonnet-5", FakeDTU()).command()[2])
    assert argv[argv.index("--provider") + 1] == "anthropic"
    assert "--reasoning-effort" not in argv
    assert argv[-2:] == ["--out", "/root/amplifier-agent-run"]


def test_command_passes_raw_llm_payloads_when_enabled():
    adapter = _configure("claude-sonnet-5", FakeDTU(), raw_llm_payloads=True)
    assert shlex.split(adapter.command()[2])[-1] == "--raw-llm-payloads"
    assert "--raw-llm-payloads" not in _configure("claude-sonnet-5", FakeDTU()).command()[2]


def test_command_before_configure_raises():
    with pytest.raises(agents.AdapterError, match="before configure"):
        AmplifierAgentAdapter(ref=SHA).command()


def test_driver_matches_deep_swe_copy():
    assert (DRIVER_SOURCE / DRIVER_FILE).read_bytes() == (
        DEEP_SWE_DRIVER / DRIVER_FILE
    ).read_bytes()


# ---------------------------------------------------------------------------
# run.py: one ref per run
# ---------------------------------------------------------------------------


@pytest.fixture
def ls_remote(monkeypatch):
    """Replace `git ls-remote` with a canned answer; records every argv."""
    calls: list[list[str]] = []
    answer = SimpleNamespace(returncode=0, stdout=f"{SHA}\tHEAD\n", stderr="")

    def fake(argv, **kwargs):
        calls.append(argv)
        return answer

    monkeypatch.setattr(run.subprocess, "run", fake)
    return SimpleNamespace(calls=calls, answer=answer)


def test_default_ref_resolves_the_default_branch_head(ls_remote):
    assert run.resolve_amplifier_agent_ref(None) == SHA
    assert ls_remote.calls == [["git", "ls-remote", REPO, "HEAD"]]


def test_full_sha_is_used_as_given(ls_remote):
    assert run.resolve_amplifier_agent_ref(OTHER_SHA) == OTHER_SHA
    assert ls_remote.calls == []


def test_tag_resolves_to_its_peeled_commit(ls_remote):
    ls_remote.answer.stdout = f"{OTHER_SHA}\trefs/tags/v1\n{SHA}\trefs/tags/v1^{{}}\n"
    assert run.resolve_amplifier_agent_ref("v1") == SHA
    assert ls_remote.calls == [["git", "ls-remote", REPO, "v1"]]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (128, ""),
        (0, ""),
        (0, f"{SHA}\trefs/heads/x\n{OTHER_SHA}\trefs/tags/x\n"),
    ],
)
def test_unresolvable_ref_raises(ls_remote, returncode, stdout):
    ls_remote.answer.returncode, ls_remote.answer.stdout = returncode, stdout
    with pytest.raises(ValueError):
        run.resolve_amplifier_agent_ref("x")


@pytest.fixture
def fake_run(tmp_path, monkeypatch, ls_remote):
    """Drive run._run with every external dependency replaced; records the adapter kwargs."""
    task = Task(split="easy", occupation="lawyer", task_num=1, root=Path("/nonexistent"))
    kwargs_seen: dict[str, dict[str, Any]] = {}

    async def image_exists(alias):
        return True

    async def run_matrix(pairs, run_root, *, agent_kwargs_for, **kwargs):
        for pair in pairs:
            kwargs_seen[pair.agent] = agent_kwargs_for(pair.agent)
        return []

    monkeypatch.setattr(run.matrix, "resolve_tasks", lambda **kwargs: [task])
    monkeypatch.setattr(run.dataset, "revision", lambda split: "rev")
    monkeypatch.setattr(run.images, "image_exists", image_exists)
    monkeypatch.setattr(run.scheduler, "run_matrix", run_matrix)

    def invoke(*extra: str) -> tuple[int, dict | None]:
        argv = ["run", "--task", "lawyer/task1", "--split", "easy", "--no-grade"]
        argv += ["--no-orphan-sweep", "--output-dir", str(tmp_path), "--run-id", "r", *extra]
        args = run.build_parser().parse_args(argv)
        code = asyncio.run(run._run(args))
        manifest_path = tmp_path / "r" / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
        return code, manifest

    invoke.kwargs_seen = kwargs_seen
    return invoke


def test_run_resolves_once_and_records_the_ref(fake_run, ls_remote):
    code, manifest = fake_run("--agent", "amplifier-agent,opencode-vanilla")
    assert code == 0
    assert ls_remote.calls == [["git", "ls-remote", REPO, "HEAD"]]
    assert manifest["amplifier_agent_ref"] == SHA
    assert manifest["raw_llm_payloads"] is False
    assert fake_run.kwargs_seen == {
        "amplifier-agent": {"ref": SHA, "raw_llm_payloads": False},
        "opencode-vanilla": {},
    }


def test_run_passes_the_given_ref_and_raw_payloads(fake_run, ls_remote):
    code, manifest = fake_run(
        "--agent", "amplifier-agent", "--amplifier-agent-ref", OTHER_SHA, "--raw-llm-payloads"
    )
    assert code == 0
    assert ls_remote.calls == []
    assert manifest["amplifier_agent_ref"] == OTHER_SHA
    assert manifest["raw_llm_payloads"] is True
    assert fake_run.kwargs_seen == {
        "amplifier-agent": {"ref": OTHER_SHA, "raw_llm_payloads": True},
    }


def test_run_without_the_arm_records_no_ref(fake_run, ls_remote):
    code, manifest = fake_run("--agent", "opencode-vanilla")
    assert code == 0
    assert ls_remote.calls == []
    assert "amplifier_agent_ref" not in manifest


def test_run_exits_when_the_ref_cannot_be_resolved(fake_run, ls_remote, capsys):
    ls_remote.answer.returncode, ls_remote.answer.stderr = 128, "fatal: unable to access"
    code, manifest = fake_run("--agent", "amplifier-agent")
    assert code == 2
    assert manifest is None
    assert fake_run.kwargs_seen == {}
    assert "cannot resolve the amplifier-agent ref" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [("--raw-llm-payloads",), ("--amplifier-agent-ref", "main")])
def test_amplifier_agent_flags_need_the_arm(fake_run, ls_remote, flag):
    code, manifest = fake_run("--agent", "opencode-vanilla", *flag)
    assert code == 2
    assert manifest is None and ls_remote.calls == []
