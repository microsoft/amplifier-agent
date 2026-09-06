"""Require installed acceptance to exercise artifacts from the tested source tree."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.build_runtime import inventory, source_inventory

INPUTS = (
    "AMPLIFIER_AGENT_PYTHON_EXECUTABLE", "AMPLIFIER_AGENT_PYTHON_PROJECT",
    "AMPLIFIER_AGENT_NODE_EXECUTABLE", "AMPLIFIER_AGENT_NODE_PROJECT",
    "AMPLIFIER_AGENT_FACE_EXECUTABLE",
)


def file_hashes(directory: Path, pattern: str) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob(pattern)) if path.is_file()}


def package_sources(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".mjs"}}


def runtime_manifest(directory: Path, root: Path, variant: str) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("variant") != variant:
        raise ValueError(f"{directory}: require the {variant} runtime variant")
    if manifest.get("sources") != source_inventory(root, variant):
        raise ValueError(f"{directory}: runtime sources differ; rebuild from the tested source tree")
    if manifest.get("files") != inventory(directory):
        raise ValueError(f"{directory}: runtime files differ from their manifest; rebuild the artifact")
    return manifest


def inspect_installed(root: Path, execute) -> dict:
    missing = [name for name in INPUTS if not os.environ.get(name)]
    if missing:
        raise ValueError("Installed acceptance requires " + ", ".join(missing)
                         + "; build and install matching artifacts using docs_v1/development/checks.md")
    python_project = Path(os.environ[INPUTS[1]]).resolve()
    node_project = Path(os.environ[INPUTS[3]]).resolve()
    if any(path.is_relative_to(root.resolve()) for path in (python_project, node_project)):
        raise ValueError("Installed consumer projects must be outside the producer checkout")
    probe = (
        "import importlib.util, json; "
        "print(json.dumps({name: importlib.util.find_spec(name).origin "
        "for name in ('amplifier_agent', 'amplifier_agent_engine', 'amplifier_agent_http')}))"
    )
    completed = execute([os.environ[INPUTS[0]], "-I", "-c", probe], cwd=python_project, timeout=10)
    if completed.returncode:
        raise ValueError("Cannot inspect installed Python packages: " + completed.stderr.strip())
    origins = json.loads(completed.stdout)
    python_sources = {}
    for module, package in (("amplifier_agent", "python"), ("amplifier_agent_engine", "engine"),
                            ("amplifier_agent_http", "http")):
        installed = Path(origins[module]).resolve().parent
        if installed.is_relative_to(root.resolve()):
            raise ValueError(f"{module}: acceptance requires an installed package outside the checkout")
        actual = package_sources(installed)
        expected = package_sources(root / "packages" / package / "src" / module)
        if not actual or actual != expected:
            raise ValueError(f"{module}: installed sources differ; rebuild and reinstall the tested source")
        python_sources[module] = actual
    completed = execute([
        os.environ[INPUTS[2]], "--input-type=module", "--eval",
        "console.log(import.meta.resolve('@microsoft/amplifier-agent'))",
    ], cwd=node_project, timeout=10)
    if completed.returncode:
        raise ValueError("Cannot locate installed TypeScript package: " + completed.stderr.strip())
    from urllib.parse import unquote, urlparse

    location = Path(unquote(urlparse(completed.stdout.strip()).path)).resolve()
    package = location.parent.parent
    if package.is_relative_to(root.resolve()):
        raise ValueError("TypeScript acceptance requires a copied installed package outside the checkout")
    actual = file_hashes(package / "dist", "*")
    if not actual or actual != file_hashes(root / "packages/typescript/dist", "*"):
        raise ValueError("Installed TypeScript output differs; rebuild and reinstall the tested library")
    node_runtime = runtime_manifest(package / "runtime/linux-x64", root, "engine")
    face = Path(os.environ[INPUTS[4]]).resolve()
    face_runtime = runtime_manifest(face.parent, root, "face")
    if face.name not in face_runtime["files"]:
        raise ValueError("The HTTP executable is not included in its runtime manifest")
    return {"python": python_sources, "typescript": actual,
            "node_runtime": node_runtime, "http_runtime": face_runtime}
