"""Build private engine executables and the standalone HTTP service."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def inventory(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.name != "manifest.json"
    }


def module_names(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        return set().union(*(module_names(item) for item in value))
    return set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", action="store_true", help="Build the conformance provider variant."
    )
    parser.add_argument(
        "--replacement", action="store_true", help="Build the replacement acceptance variant."
    )
    parser.add_argument("--face", action="store_true", help="Build the standalone HTTP service.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        parser.error("This build target requires Linux x86_64.")
    if sum((args.face, args.fixture, args.replacement)) > 1:
        parser.error("Use separate production and conformance build targets.")
    project = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if args.output.is_symlink():
        parser.error("The output must be an owned directory, not a symbolic link.")
    if output.exists() and any(output.iterdir()):
        previous_path = output / "manifest.json"
        if not previous_path.is_file():
            parser.error("Refusing to replace an output directory without an artifact manifest.")
        previous = json.loads(previous_path.read_text())
        if previous.get("platform") != "linux-x64" or previous.get("variant") not in {
            "engine",
            "fixture",
            "replacement",
            "face",
        }:
            parser.error("The output directory does not belong to this runtime builder.")
        unexpected = set(inventory(output)) - set(previous.get("files", {}))
        if unexpected:
            parser.error(
                f"The output contains files not owned by its manifest: {sorted(unexpected)}"
            )
    name = "amplifier-agent-face" if args.face else "amplifier-agent-engine"
    variant = (
        "face"
        if args.face
        else "fixture"
        if args.fixture
        else "replacement"
        if args.replacement
        else "engine"
    )
    work = project / "build" / variant
    work.mkdir(parents=True, exist_ok=True)
    entry = work / "entry.py"
    module = (
        "amplifier_agent_http.__main__"
        if args.face
        else "conformance.fixtures.runtime"
        if args.fixture
        else "conformance.fixtures.replacement_runtime"
        if args.replacement
        else "amplifier_agent_engine._runtime.__main__"
    )
    entry.write_text(f"from {module} import main\n\nif __name__ == '__main__':\n    main()\n")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if args.face else "--onedir",
        "--name",
        name,
        "--distpath",
        str(work / "dist"),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work),
        "--paths",
        str(project),
        "--collect-submodules",
        "amplifier_agent_engine",
        "--copy-metadata",
        "amplifier-agent-engine",
    ]
    if args.face:
        for package, distribution in (
            ("amplifier_agent", "amplifier-agent"),
            ("amplifier_agent_http", "amplifier-agent-http"),
        ):
            command += ["--collect-submodules", package, "--copy-metadata", distribution]
    for package, distribution in (
        ("amplifier_core", "amplifier-core"),
        ("amplifier_module_loop_streaming", "amplifier-module-loop-streaming"),
        ("amplifier_module_context_simple", "amplifier-module-context-simple"),
        ("amplifier_module_provider_anthropic", "amplifier-module-provider-anthropic"),
    ):
        command += ["--collect-all", package, "--copy-metadata", distribution]
    if args.fixture or args.replacement:
        command += ["--collect-all", "conformance.fixtures"]
        scenarios = project / "conformance" / "scenarios"
        command += ["--add-data", f"{scenarios}:conformance/scenarios"]
    command.append(str(entry))
    subprocess.run(command, cwd=project, check=True, timeout=120)
    if not args.face:
        graph = ast.literal_eval((work / "work" / name / "PYZ-00.toc").read_text())
        included = module_names(graph)
        leaked = sorted(
            module
            for module in included
            if module in {"amplifier_agent", "amplifier_agent_http"}
            or module.startswith(("amplifier_agent.", "amplifier_agent_http."))
        )
        if leaked:
            raise RuntimeError(f"Engine executable includes SDK or HTTP modules: {leaked}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    source = work / "dist" / name
    if args.face:
        shutil.copy2(source, output / name)
    else:
        shutil.copytree(source, output, dirs_exist_ok=True)
    (output / "LICENSE").write_bytes((project / "LICENSE").read_bytes())
    manifest = {"platform": "linux-x64", "variant": variant, "files": inventory(output)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps({"artifact": str(output), "variant": variant, "files": len(manifest["files"])})
    )


if __name__ == "__main__":
    main()
