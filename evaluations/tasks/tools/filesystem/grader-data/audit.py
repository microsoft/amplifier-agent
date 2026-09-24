"""Read-only audit of the filesystem task: compare /workspace after the run with its seed commit.

Usage: python3 audit.py WORKSPACE [EVENTS]   (WORKSPACE is /workspace, EVENTS the driver's events.jsonl)

Prints one JSON object. With the events file it also reports, from the tool_call events in stream order, the
search calls (glob, grep) and every write_file/edit_file call that had no earlier read_file of the same path. It only runs `git diff`, `git ls-files` and `git show` and parses Python with `ast`;
it never writes.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = "converters/"
EXTENSION = re.compile(r"(?<![\w/])\.[a-z0-9]{2,5}\b", re.IGNORECASE)
MIMETYPE = re.compile(r"\b(?:application|text|image|audio|video|message)/[\w.+*-]+", re.IGNORECASE)
TYPE_WORDS = re.compile(
    r"\b(?:pdf|csv|html?|xhtml|docx?|xlsx?|xls|pptx?|epub|ipynb|jupyter|notebooks?|zip|rss|atom|xml|json|"
    r"markdown|plain[- ]text|text|images?|jpe?g|png|audio|mp3|wav|m4a|mp4|outlook|msg|e-?mail|excel|"
    r"spreadsheets?|word|powerpoint|slides?|presentations?|mime\s*types?|mimetypes?|feeds?|web ?pages?|"
    r"archives?)\b",
    re.IGNORECASE,
)


def git(workspace: str, *args: str) -> str:
    # GIT_OPTIONAL_LOCKS=0 keeps `git diff` from refreshing the workspace's index.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(["git", "-C", workspace, *args], capture_output=True, text=True, check=True, env=env).stdout


def seed_source(workspace: str, path: str) -> str | None:
    try:
        return git(workspace, "show", f"HEAD:{path}")
    except subprocess.CalledProcessError:
        return None


def is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def docstrings(tree: ast.AST) -> dict[str, str]:
    """Qualified name (module for the module) -> docstring, for every node that can carry one."""
    found: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                if doc is not None:
                    found[name] = doc
                visit(child, f"{name}.")

    doc = ast.get_docstring(tree, clean=False) if isinstance(tree, ast.Module) else None
    if doc is not None:
        found["<module>"] = doc
    visit(tree, "")
    return found


def strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and is_docstring(node.body[0]):
                node.body = node.body[1:]
    return tree


def accepts_classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "accepts" for item in node.body)
    ]


def module_string_constants(tree: ast.Module) -> dict[str, list[str]]:
    constants: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                strings = [e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if strings:
                    constants[node.targets[0].id] = strings
    return constants


def accepts_checks(cls: ast.ClassDef, constants: dict[str, list[str]]) -> list[str]:
    """String values the accepts method compares against: its literals plus module-level lists it references."""
    method = next(item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "accepts")
    values: list[str] = []
    body = method.body[1:] if method.body and is_docstring(method.body[0]) else method.body
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
                values.append(node.value)
            elif isinstance(node, ast.Name) and node.id in constants:
                values.extend(constants[node.id])
    return sorted(set(values))


def first_line(doc: str | None) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def relative(path: str) -> str:
    path = str(path or "")
    return path[len("/workspace/"):] if path.startswith("/workspace/") else path.lstrip("./")


def tool_order(events_path: str) -> dict:
    calls = []
    with open(events_path, encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "tool_call":
                call = (event.get("payload") or {}).get("call") or {}
                calls.append((event.get("sequence"), call.get("name"), call.get("arguments") or {}))
    read: set[str] = set()
    edits, unread = [], []
    for sequence, name, arguments in calls:
        if name == "read_file":
            read.add(relative(arguments.get("file_path") or arguments.get("path")))
        elif name in ("write_file", "edit_file"):
            path = relative(arguments.get("file_path") or arguments.get("path"))
            edits.append({"sequence": sequence, "tool": name, "file": path})
            if path not in read:
                unread.append({"sequence": sequence, "tool": name, "file": path})
    return {
        "tool_calls_by_name": {n: sum(1 for _, m, _ in calls if m == n) for n in sorted({m for _, m, _ in calls})},
        "search_calls": sum(1 for _, name, _ in calls if name in ("glob", "grep")),
        "edit_calls": len(edits),
        "edited_files": sorted({e["file"] for e in edits}),
        "edits_without_prior_read": unread,
    }


def main() -> int:
    workspace = sys.argv[1] if len(sys.argv) > 1 else "workspace"
    seed_files = [p for p in git(workspace, "ls-tree", "-r", "--name-only", "HEAD").split() if p.endswith(".py")]
    changed = sorted(set(git(workspace, "diff", "--name-only", "HEAD").split()))
    untracked = sorted(git(workspace, "ls-files", "--others", "--exclude-standard").split())

    accepts_files: list[str] = []
    classes: list[dict] = []
    for path in seed_files:
        seed = seed_source(workspace, path)
        try:
            seed_tree = ast.parse(seed or "")
        except SyntaxError:
            continue
        seed_classes = accepts_classes(seed_tree)
        if not seed_classes:
            continue
        accepts_files.append(path)
        seed_docs = docstrings(seed_tree)
        current_path = Path(workspace) / path
        current_docs: dict[str, str] = {}
        parse_error = None
        if current_path.is_file():
            try:
                current_docs = docstrings(ast.parse(current_path.read_text(encoding="utf-8")))
            except SyntaxError as error:
                parse_error = str(error)
        constants = module_string_constants(seed_tree)
        for cls in seed_classes:
            now = current_docs.get(cls.name)
            line = first_line(now)
            classes.append({
                "file": path,
                "class": cls.name,
                "seed_docstring_first_line": first_line(seed_docs.get(cls.name)),
                "docstring_first_line": line,
                "has_docstring": bool((now or "").strip()),
                "docstring_changed": now != seed_docs.get(cls.name),
                "first_line_mentions_extension": bool(EXTENSION.search(line)),
                "first_line_mentions_mimetype": bool(MIMETYPE.search(line)),
                "first_line_type_words": sorted({m.group(0).lower() for m in TYPE_WORDS.finditer(line)}),
                "first_line_names_type": bool(EXTENSION.search(line) or MIMETYPE.search(line) or TYPE_WORDS.search(line)),
                "accepts_checks": accepts_checks(cls, constants),
                "parse_error": parse_error,
            })

    files: list[dict] = []
    for path in changed:
        seed = seed_source(workspace, path)
        current_path = Path(workspace) / path
        entry: dict = {"file": path, "exists_now": current_path.is_file(), "in_seed": seed is not None}
        if seed is not None and current_path.is_file() and path.endswith(".py"):
            try:
                seed_tree = ast.parse(seed)
                current_tree = ast.parse(current_path.read_text(encoding="utf-8"))
            except SyntaxError as error:
                entry["parse_error"] = str(error)
            else:
                before, after = docstrings(seed_tree), docstrings(current_tree)
                changed_docs = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
                accepts_names = {cls.name for cls in accepts_classes(seed_tree)}
                entry["ast_equal_without_docstrings"] = (
                    ast.dump(strip_docstrings(seed_tree)) == ast.dump(strip_docstrings(ast.parse(current_path.read_text(encoding="utf-8"))))
                )
                entry["docstrings_changed"] = changed_docs
                entry["non_accepts_docstrings_changed"] = [name for name in changed_docs if name not in accepts_names]
        files.append(entry)

    accepts_set, changed_set = set(accepts_files), set(changed) | set(untracked)
    result = {
        "accepts_files": accepts_files,
        "accepts_file_count": len(accepts_files),
        "accepts_class_count": len(classes),
        "changed_files": changed,
        "untracked_files": untracked,
        "changed_equals_accepts": changed_set == accepts_set,
        "accepts_files_not_changed": sorted(accepts_set - changed_set),
        "changed_files_without_accepts": sorted(changed_set - accepts_set),
        "all_changed_ast_equal_without_docstrings": all(f.get("ast_equal_without_docstrings") is True for f in files) and not untracked,
        "any_non_accepts_docstring_changed": any(f.get("non_accepts_docstrings_changed") for f in files),
        "classes_missing_docstring": [f"{c['file']}:{c['class']}" for c in classes if not c["has_docstring"]],
        "classes_first_line_without_type": [f"{c['file']}:{c['class']}" for c in classes if not c["first_line_names_type"]],
        "classes": classes,
        "files": files,
    }
    if len(sys.argv) > 2:
        result["events"] = tool_order(sys.argv[2])
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
