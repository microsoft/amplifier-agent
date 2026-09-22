"""Build private engine executables and the standalone HTTP service."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import math
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path, PurePosixPath


def inventory(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.name != "manifest.json"
    }


def source_inventory(project: Path, variant: str) -> dict[str, str]:
    paths = set((project / "packages/engine/src").rglob("*.py"))
    paths.update((project / "packages/engine/src").rglob("*.mjs"))
    paths.update(project / name for name in (
        "packages/engine/pyproject.toml", "uv.lock", "scripts/build_runtime.py",
    ))
    if variant in {"fixture", "replacement"}:
        paths.update((project / "conformance/fixtures").rglob("*.py"))
        paths.update((project / "conformance/fixtures").rglob("*.mjs"))
        paths.update((project / "conformance/scenarios").glob("*.json"))
    if variant == "face":
        paths.update((project / "packages/python/src").rglob("*.py"))
        paths.update((project / "packages/http/src").rglob("*.py"))
    return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def module_names(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        return set().union(*(module_names(item) for item in value))
    return set()


def artifact_data(entries: list[tuple]) -> list[tuple]:
    """Keep runtime data and notices without local install origins or provider tests."""
    retained = []
    for entry in entries:
        path = PurePosixPath(entry[0])
        if path.name == "direct_url.json" and path.parent.name.endswith(".dist-info"):
            continue
        if path.parts[:3] == ("google", "genai", "tests"):
            continue
        retained.append(entry)
    return retained


def prepare_spec(path: Path) -> None:
    source = path.read_text()
    tree = ast.parse(source)
    collections = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "collect_all" and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "google.genai"
    ]
    analyses = [
        node for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "a"
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Analysis"
    ]
    if len(collections) != 1 or len(analyses) != 1:
        raise RuntimeError("The generated runtime spec lacks the expected Google collection or Analysis.")
    collection, analysis = collections[0], analyses[0]
    if collection.keywords or collection.lineno != collection.end_lineno:
        raise RuntimeError("The generated Google collection has an unsupported shape.")
    if analysis.end_lineno is None or collection.end_col_offset is None:
        raise RuntimeError("The generated runtime spec has incomplete source positions.")
    lines = source.splitlines(keepends=True)
    line = lines[collection.lineno - 1]
    lines[collection.lineno - 1] = (
        line[:collection.col_offset]
        + "collect_all('google.genai', exclude_datas=['tests'], "
        + "filter_submodules=lambda name: name != 'google.genai.tests' "
        + "and not name.startswith('google.genai.tests.'))"
        + line[collection.end_col_offset:]
    )
    lines.insert(analysis.end_lineno,
                 "\nfrom scripts.build_runtime import artifact_data\na.datas = artifact_data(a.datas)\n")
    path.write_text("".join(lines))


def prepare_sysconfig(work: Path, name: str, values: dict, origin: str) -> Path:
    """Relocate interpreter paths without freezing the build machine's install prefix."""
    if not isinstance(name, str) or not re.fullmatch(r"_sysconfigdata_[A-Za-z0-9_-]+", name):
        raise RuntimeError("The interpreter has an unsupported sysconfig module name.")
    if not isinstance(origin, str) or not origin or not isinstance(values, dict):
        raise RuntimeError("The interpreter has unsupported sysconfig metadata.")
    entries = []
    for key, value in values.items():
        if not isinstance(key, str) or origin in key or type(value) not in {str, int, float, bool, type(None)}:
            raise RuntimeError("The interpreter has unsupported sysconfig metadata.")
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError("The interpreter has nonfinite sysconfig metadata.")
        expression = (
            f"_runtime_prefix.join({value.split(origin)!r})"
            if isinstance(value, str) and origin in value else repr(value)
        )
        entries.append(f"    {key!r}: {expression},\n")
    source = (
        "import sys\n\n_runtime_prefix = getattr(sys, '_MEIPASS', sys.base_prefix)\n"
        "build_time_vars = {\n" + "".join(entries) + "}\n"
    )
    if origin in source:
        raise RuntimeError("The generated sysconfig metadata contains the build interpreter prefix.")
    module = work / "stdlib" / f"{name}.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source)
    hooks = work / "hooks"
    hook = hooks / "pre_find_module_path" / f"hook-{name}.py"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "from pathlib import Path\n\n"
        "def pre_find_module_path(api):\n"
        "    api.search_dirs = [str(Path(__file__).resolve().parents[2] / 'stdlib')]\n"
    )
    return hooks


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
    sources = source_inventory(project, variant)
    work = project / "build" / "runtime-work" / variant
    if output == work or output.is_relative_to(work) or work.is_relative_to(output):
        parser.error(f"The output must not overlap the build work directory {work}.")
    work.mkdir(parents=True, exist_ok=True)
    config_name = sysconfig._get_sysconfigdata_name()
    hooks = prepare_sysconfig(
        work, config_name, importlib.import_module(config_name).build_time_vars, sys.base_prefix,
    )
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
        "PyInstaller.utils.cliutils.makespec",
        "--onefile" if args.face else "--onedir",
        "--name",
        name,
        "--specpath",
        str(work),
        "--paths",
        str(project),
        "--additional-hooks-dir",
        str(hooks),
        "--collect-submodules",
        "amplifier_agent_engine",
        "--copy-metadata",
        "amplifier-agent-engine",
        "--exclude-module",
        "google.genai.tests",
    ]
    if args.face:
        for package, distribution in (
            ("amplifier_agent", "amplifier-agent"),
            ("amplifier_agent_http", "amplifier-agent-http"),
        ):
            command += ["--collect-submodules", package, "--copy-metadata", distribution]
    ecosystem = (
        ("amplifier_core", "amplifier-core"),
        ("amplifier_module_loop_streaming", "amplifier-module-loop-streaming"),
        ("amplifier_module_context_simple", "amplifier-module-context-simple"),
        ("amplifier_foundation", "amplifier-foundation"),
        ("amplifier_module_hooks_routing", "amplifier-module-hooks-routing"),
        ("google.genai", "google-genai"),
        ("copilot", "github-copilot-sdk"),
        ("primp", "primp"),
        ("ddgs", "ddgs"),
    )
    for kind, names in (
        (
            "provider",
            (
                "anthropic",
                "openai",
                "azure-openai",
                "ollama",
                "github-copilot",
                "openai-chatgpt",
                "chat-completions",
                "gemini",
                "vllm",
            ),
        ),
        ("tool", ("filesystem", "bash", "web", "search", "mcp", "skills", "delegate")),
    ):
        ecosystem += tuple(
            (f"amplifier_module_{kind}_{item.replace('-', '_')}", f"amplifier-module-{kind}-{item}")
            for item in names
        )
    for package, distribution in ecosystem:
        command += ["--collect-all", package, "--copy-metadata", distribution]
    for package in ("mcp.client", "mcp.shared", "mcp.types"):
        command += ["--collect-submodules", package]
    command += ["--copy-metadata", "mcp"]
    from copilot._cli_download import get_cached_cli_path

    copilot_runtime = get_cached_cli_path()
    if copilot_runtime is None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from copilot._cli_download import download_cli; download_cli()",
            ],
            check=True,
            timeout=120,
        )
        copilot_runtime = get_cached_cli_path()
    if copilot_runtime is None:
        raise RuntimeError("The pinned Copilot runtime could not be prepared for the build.")
    command += ["--add-binary", f"{copilot_runtime}:copilot_runtime"]
    if args.fixture or args.replacement:
        command += ["--collect-all", "conformance.fixtures"]
        scenarios = project / "conformance" / "scenarios"
        command += ["--add-data", f"{scenarios}:conformance/scenarios"]
    command.append(str(entry))
    subprocess.run(command, cwd=project, check=True, timeout=30)
    spec = work / f"{name}.spec"
    prepare_spec(spec)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(work / "dist"), "--workpath", str(work / "work"), str(spec),
    ], cwd=project, check=True, timeout=120)
    graph = ast.literal_eval((work / "work" / name / "PYZ-00.toc").read_text())
    included = module_names(graph)
    if any(module == "google.genai.tests" or module.startswith("google.genai.tests.")
           for module in included):
        raise RuntimeError("The runtime executable includes Google SDK test modules.")
    if not args.face:
        leaked = sorted(
            module
            for module in included
            if module in {"amplifier_agent", "amplifier_agent_http"}
            or module.startswith(("amplifier_agent.", "amplifier_agent_http."))
        )
        if leaked:
            raise RuntimeError(f"Engine executable includes SDK or HTTP modules: {leaked}")
    if source_inventory(project, variant) != sources:
        raise RuntimeError("Runtime sources changed during the build; rebuild from a stable source tree.")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    source = work / "dist" / name
    if args.face:
        shutil.copy2(source, output / name)
    else:
        shutil.copytree(source, output, dirs_exist_ok=True)
        host = output / "node-host"
        if args.replacement:
            host.mkdir()
            shutil.copy2(project / "conformance/fixtures/replacement_host.mjs", host / "index.mjs")
        else:
            shutil.copytree(project / "packages/engine/src/amplifier_agent_engine/_node_host", host)
    (output / "LICENSE").write_bytes((project / "LICENSE").read_bytes())
    manifest = {
        "platform": "linux-x64", "variant": variant, "files": inventory(output),
        "sources": sources,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps({"artifact": str(output), "variant": variant, "files": len(manifest["files"])})
    )


if __name__ == "__main__":
    main()
