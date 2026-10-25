import ast
from pathlib import Path

import amplifier_agent

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "llm_wiki"
AGENT = PACKAGE / "agent"
FILES = sorted([*PACKAGE.rglob("*.py"), *(ROOT / "tests").glob("*.py")])


def amplifier_imports(path: Path) -> list[tuple[str, list[str]]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0].startswith("amplifier"):
            found.append((node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names if alias.name.split(".")[0].startswith("amplifier")]
    return found


def test_only_public_amplifier_agent_exports_are_imported() -> None:
    public = set(amplifier_agent.__all__)
    problems = []
    for path in FILES:
        for module, names in amplifier_imports(path):
            if module != "amplifier_agent":
                problems.append(f"{path.relative_to(ROOT)} imports {module}")
            problems += [f"{path.relative_to(ROOT)} imports private {name}" for name in names if name not in public]

    assert problems == []


def test_only_the_agent_package_imports_the_library() -> None:
    outside = [
        str(path.relative_to(ROOT))
        for path in PACKAGE.rglob("*.py")
        if not path.is_relative_to(AGENT) and amplifier_imports(path)
    ]

    assert outside == []
