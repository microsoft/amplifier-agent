import json

import pytest

from conformance.artifacts import INPUTS, inspect_installed, runtime_manifest
from scripts.build_runtime import inventory, source_inventory


def source_tree(path):
    for name in ("packages/engine/src/amplifier_agent_engine/__init__.py",
                 "packages/engine/pyproject.toml", "uv.lock", "scripts/build_runtime.py"):
        source = path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("source\n")
    return path


@pytest.mark.parametrize("source_name", ["__init__.py", "_node_host/participant.mjs"])
def test_runtime_evidence_rejects_changed_files_sources_and_wrong_variant(tmp_path, source_name):
    root = source_tree(tmp_path / "source")
    source = root / "packages/engine/src/amplifier_agent_engine" / source_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source\n")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    executable = artifact / "amplifier-agent-engine"
    executable.write_bytes(b"built-runtime")
    manifest = {"variant": "fixture", "files": inventory(artifact),
                "sources": source_inventory(root, "fixture")}
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    assert runtime_manifest(artifact, root, "fixture") == manifest
    with pytest.raises(ValueError, match="variant"):
        runtime_manifest(artifact, root, "engine")
    executable.write_bytes(b"different-runtime")
    with pytest.raises(ValueError, match="files differ"):
        runtime_manifest(artifact, root, "fixture")
    executable.write_bytes(b"built-runtime")
    source.write_text("changed\n")
    with pytest.raises(ValueError, match="sources differ"):
        runtime_manifest(artifact, root, "fixture")


def test_missing_installed_inputs_fail_before_launching_a_process(tmp_path, monkeypatch):
    for name in INPUTS:
        monkeypatch.delenv(name, raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("Missing artifacts must not launch a consumer")

    with pytest.raises(ValueError, match="AMPLIFIER_AGENT_PYTHON_EXECUTABLE"):
        inspect_installed(tmp_path, unexpected)


def test_checkout_cannot_substitute_for_an_installed_consumer(tmp_path, monkeypatch):
    for name in INPUTS:
        monkeypatch.setenv(name, str(tmp_path / name))
    with pytest.raises(ValueError, match="outside the producer checkout"):
        inspect_installed(tmp_path, lambda *args, **kwargs: None)
