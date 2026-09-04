import argparse
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tests.e2e import source_install


@pytest.fixture
def forge(monkeypatch):
    dependency = ModuleType("amplifier_bundle_digital_twin_universe")
    dependency.engine = SimpleNamespace()
    monkeypatch.setitem(sys.modules, dependency.__name__, dependency)
    spec = importlib.util.spec_from_file_location(
        "local_forge_under_test", Path(__file__).with_name("local_forge.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resources():
    return {
        "revision": "abc",
        "consumer": {"id": "consumer"},
        "gitea": {"id": "gitea", "token": "private"},
    }


def test_cleanup_attempts_all_resources_and_retains_only_failures(forge, tmp_path):
    attempts = []
    state, path = resources(), tmp_path / "state.json"

    def failed_consumer(identifier):
        attempts.append(identifier)
        raise RuntimeError("DTU unavailable")

    forge.engine.destroy = failed_consumer
    forge.command = lambda arguments: attempts.append(arguments[-1])
    with pytest.raises(ExceptionGroup):
        forge.destroy(state, path)
    assert attempts == ["consumer", "gitea"]
    assert json.loads(path.read_text()) == {"revision": "abc", "consumer": {"id": "consumer"}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    forge.engine.destroy = lambda identifier: attempts.append(identifier)
    forge.destroy(state, path)
    assert attempts == ["consumer", "gitea", "consumer"]
    assert not path.exists()


def test_cleanup_tolerates_already_removed_owned_resources(forge, tmp_path):
    def missing_consumer(identifier):
        raise RuntimeError(f"Environment not found: {identifier}")

    def missing_gitea(arguments):
        raise subprocess.CalledProcessError(
            1, arguments, stderr="Error: Environment not found: gitea\n"
        )

    forge.engine.destroy = missing_consumer
    forge.command = missing_gitea
    state, path = resources(), tmp_path / "state.json"
    forge.save(path, state)
    forge.destroy(state, path)
    assert not path.exists()
    assert "consumer" not in state and "gitea" not in state


def test_atomic_state_save_preserves_previous_state_on_failure(forge, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    forge.save(path, {"retained": True})
    with pytest.raises(TypeError):
        forge.save(path, {"unserializable": object()})
    assert json.loads(path.read_text()) == {"retained": True}
    assert list(tmp_path.iterdir()) == [path]


def test_prepare_preserves_original_failure_when_cleanup_fails(forge, tmp_path):
    original = RuntimeError("original API failure")

    def command(arguments):
        if arguments[0] == "git":
            return "abc"
        if arguments[1] == "create":
            return json.dumps({"id": "gitea", "gitea_url": "http://127.0.0.1", "token": "private"})
        raise RuntimeError("cleanup failure")

    def failed_request(*args, **kwargs):
        raise original

    forge.command = command
    forge.urlopen = failed_request
    args = argparse.Namespace(
        state=tmp_path / "state.json", repository=tmp_path / "repo", ref="HEAD", port=11175
    )
    with pytest.raises(RuntimeError) as caught:
        forge.prepare(args)
    assert caught.value is original
    assert "Cleanup needs attention" in caught.value.__notes__[0]
    assert json.loads(args.state.read_text())["gitea"]["id"] == "gitea"


def test_prepare_refuses_credentials_inside_checkout_before_commands(forge, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    forge.command = lambda args: pytest.fail("No command should run for an unsafe state path")
    args = argparse.Namespace(state=repo / "state.json", repository=repo, ref="HEAD", port=11175)
    with pytest.raises(ValueError, match="credentials"):
        forge.prepare(args)


def test_verify_checks_transitive_engine_before_adding_http(forge, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(resources()))
    commands = []

    def execute(environment, arguments, **kwargs):
        assert environment == "consumer"
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "verified", "stderr": ""}

    forge.engine.exec_command = execute
    forge.verify(argparse.Namespace(state=state))
    assert len(commands) == 7
    assert commands[0] == ["uv", "init", "--bare", "--python", "3.12", "/opt/consumer"]
    assert commands[1][5:] == [
        "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/python"
    ]
    assert commands[4][5:] == [
        "amplifier-agent-http @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/http"
    ]
    assert commands[2] == commands[5] == ["uv", "--directory", "/opt/consumer", "sync", "--locked"]
    assert commands[3][-3:] == ["abc", "amplifier-agent", "amplifier-agent-engine"]
    assert commands[6][-4:] == [
        "abc",
        "amplifier-agent",
        "amplifier-agent-engine",
        "amplifier-agent-http",
    ]
    assert "direct_url.json" in commands[3][7]
    assert commands[3][7] == commands[6][7]
    assert not any(
        requirement.startswith("amplifier-agent-engine @")
        for command in commands
        if "add" in command
        for requirement in command
    )


def test_verify_stops_before_http_when_transitive_engine_is_missing(forge, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(resources()))
    commands = []

    def execute(environment, arguments, **kwargs):
        commands.append(arguments)
        if "run" in arguments:
            return {"exit_code": 1, "stdout": "", "stderr": "Missing amplifier-agent-engine"}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    forge.engine.exec_command = execute
    with pytest.raises(RuntimeError, match="Missing amplifier-agent-engine"):
        forge.verify(argparse.Namespace(state=state))
    assert len(commands) == 4
    assert not any(
        requirement.startswith("amplifier-agent-http @")
        for command in commands
        for requirement in command
    )


@pytest.mark.parametrize("package", source_install.PACKAGES)
@pytest.mark.parametrize(
    "corruption",
    [None, "installed_revision", "locked_revision", "installed_subdir", "locked_subdir"],
)
def test_source_install_verifies_every_distribution(package, corruption):
    direct_urls, locked = {}, {}
    for name, (subdirectory, _) in source_install.PACKAGES.items():
        direct_urls[name] = {"vcs_info": {"commit_id": "abc"}, "subdirectory": subdirectory}
        locked[name] = (
            f"https://github.com/microsoft/amplifier-agent?subdirectory={subdirectory}&rev=v1#abc"
        )
    if corruption == "installed_revision":
        direct_urls[package]["vcs_info"]["commit_id"] = "wrong"
    elif corruption == "installed_subdir":
        direct_urls[package]["subdirectory"] = "wrong"
    elif corruption == "locked_revision":
        locked[package] = locked[package].replace("#abc", "#wrong")
    elif corruption == "locked_subdir":
        locked[package] = locked[package].replace("subdirectory=packages/", "subdirectory=wrong/")
    if corruption:
        with pytest.raises(AssertionError, match=package):
            source_install.validate("abc", direct_urls, locked)
    else:
        source_install.validate("abc", direct_urls, locked)


@pytest.mark.parametrize("missing", [None, "installed", "locked"])
def test_sdk_source_install_requires_its_transitive_engine_without_http(missing):
    packages = ["amplifier-agent", "amplifier-agent-engine"]
    direct_urls, locked = {}, {}
    for name in packages:
        subdirectory, _ = source_install.PACKAGES[name]
        direct_urls[name] = {"vcs_info": {"commit_id": "abc"}, "subdirectory": subdirectory}
        locked[name] = (
            f"https://github.com/microsoft/amplifier-agent?subdirectory={subdirectory}#abc"
        )
    if missing == "installed":
        del direct_urls["amplifier-agent-engine"]
    elif missing == "locked":
        del locked["amplifier-agent-engine"]
    if missing:
        with pytest.raises(AssertionError, match="amplifier-agent-engine"):
            source_install.validate("abc", direct_urls, locked, packages)
    else:
        source_install.validate("abc", direct_urls, locked, packages)
