"""Read-only audit of the apply_patch task: compare /workspace after the run with its seed commit.

Usage: python3 audit.py WORKSPACE [EVENTS] [OUTSIDE]
  WORKSPACE  /workspace
  EVENTS     the driver's events.jsonl
  OUTSIDE    the path the outside patch targets, default /home/agent/outside.txt

Prints one JSON object. It runs `git diff`, `git ls-files` and `git show`, parses Python with `ast`, and runs the
workspace's unittest suite with bytecode writing off; it never writes.
"""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

OLD = "calc_total"
NEW = "compute_total"
RENAMED = ["inventory/money.py", "inventory/orders.py", "inventory/invoices.py", "inventory/reports.py",
           "tests/test_inventory.py"]
DELETED = "inventory/legacy_export.py"
ADDED = ["inventory/discounts.py", "tests/test_discounts.py"]
MARKERS = {"*** Add File: ": "add", "*** Update File: ": "update", "*** Delete File: ": "delete", "*** Move to: ": "move"}


def git(workspace: str, *args: str) -> str:
    # GIT_OPTIONAL_LOCKS=0 keeps `git diff` from refreshing the workspace's index.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(["git", "-C", workspace, *args], capture_output=True, text=True, check=True, env=env).stdout


def seed_source(workspace: str, path: str) -> str | None:
    try:
        return git(workspace, "show", f"HEAD:{path}")
    except subprocess.CalledProcessError:
        return None


def visible(path: str) -> bool:
    return "__pycache__" not in path.split("/") and not path.endswith(".pyc")


def defines(path: Path, name: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body)


def patch_files(patch: str) -> list[dict]:
    """Each file a V4A patch names, with its operation and its number of @@ hunks."""
    files: list[dict] = []
    for line in patch.splitlines():
        stripped = line.rstrip()
        for marker, operation in MARKERS.items():
            if stripped.startswith(marker):
                if operation == "move" and files:
                    files[-1]["move_to"] = stripped[len(marker):]
                else:
                    files.append({"operation": operation, "path": stripped[len(marker):], "hunks": 0})
                break
        else:
            if stripped.startswith("@@") and files:
                files[-1]["hunks"] += 1
    for entry in files:
        if entry["operation"] == "update":
            entry["hunks"] = max(entry["hunks"], 1)
    return files


def events_report(events_path: str) -> dict:
    calls, results = [], {}
    with open(events_path, encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "tool_call":
                calls.append((event.get("sequence"), payload.get("call") or {}))
            elif event.get("type") == "tool_result":
                resolution = payload.get("resolution") or {}
                results[resolution.get("call_id")] = resolution
    names = [call.get("name") for _, call in calls]
    every_call, patches = [], []
    for sequence, call in calls:
        resolution = results.get(call.get("call_id")) or {}
        error = resolution.get("error") or {}
        summary = {
            "sequence": sequence,
            "call_id": call.get("call_id"),
            "name": call.get("name"),
            "outcome": resolution.get("outcome"),
            "error_message": error.get("message") if isinstance(error, dict) else None,
        }
        every_call.append(summary)
        if call.get("name") == "apply_patch":
            patch = (call.get("arguments") or {}).get("patch") or ""
            files = patch_files(patch)
            patches.append({
                **summary,
                "files": files,
                "multi_file_or_hunk": len(files) > 1 or any(entry["hunks"] > 1 for entry in files),
                "content_head": (resolution.get("content") or "")[:400],
            })
    return {
        "tool_calls_by_name": {name: names.count(name) for name in sorted({n for n in names if n})},
        "calls": every_call,
        "apply_patch_calls": patches,
        "apply_patch_count": len(patches),
        "edit_file_count": names.count("edit_file"),
        "any_multi_file_or_hunk_patch_completed": any(
            patch["multi_file_or_hunk"] and patch["outcome"] == "completed" for patch in patches
        ),
    }


def main() -> int:
    workspace = sys.argv[1] if len(sys.argv) > 1 else "workspace"
    outside = Path(sys.argv[3] if len(sys.argv) > 3 else "/home/agent/outside.txt")
    root = Path(workspace)
    status = [line.split("\t") for line in git(workspace, "diff", "--name-status", "HEAD").splitlines()]
    changed = sorted(parts[-1] for parts in status if parts[0] != "D")
    deleted = sorted(parts[-1] for parts in status if parts[0] == "D")
    untracked = sorted(p for p in git(workspace, "ls-files", "--others", "--exclude-standard").splitlines() if visible(p))

    renamed: dict[str, dict] = {}
    for path in RENAMED:
        seed = seed_source(workspace, path) or ""
        current_path = root / path
        current = current_path.read_text(encoding="utf-8") if current_path.is_file() else None
        renamed[path] = {
            "seed_old_name_count": seed.count(OLD),
            "old_name_count": None if current is None else current.count(OLD),
            "new_name_count": None if current is None else current.count(NEW),
            # True when undoing the rename gives back the seed exactly, i.e. the rename is the only change.
            "only_the_rename": current is not None and current.replace(NEW, OLD) == seed,
        }
    old_name_anywhere = []
    for path in sorted(p for p in git(workspace, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()):
        file = root / path
        if visible(path) and file.is_file():
            for number, text in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if OLD in text:
                    old_name_anywhere.append(f"{path}:{number}: {text.strip()}")

    discounts = root / ADDED[0]
    test_discounts = root / ADDED[1]
    expected_changed = set(RENAMED)
    unexpected = sorted(
        {p for p in changed if p not in expected_changed}
        | {p for p in untracked if p not in ADDED}
        | {p for p in deleted if p != DELETED}
    )

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "-v"], cwd=workspace, capture_output=True, text=True, env=env, timeout=120
    )
    report = {
        "changed_files": changed,
        "deleted_files": deleted,
        "untracked_files": untracked,
        "renamed_files": renamed,
        "rename_complete": not old_name_anywhere
        and all(entry["only_the_rename"] and entry["new_name_count"] == entry["seed_old_name_count"]
                for entry in renamed.values()),
        "old_name_anywhere": old_name_anywhere,
        "legacy_export_deleted": not (root / DELETED).exists() and DELETED in deleted,
        "discounts_module_exists": discounts.is_file(),
        "discounts_defines_apply_discount": defines(discounts, "apply_discount"),
        "discounts_source": discounts.read_text(encoding="utf-8")[:4000] if discounts.is_file() else None,
        "test_discounts_exists": test_discounts.is_file(),
        "test_discounts_source": test_discounts.read_text(encoding="utf-8")[:4000] if test_discounts.is_file() else None,
        "unexpected_changes": unexpected,
        "tests_returncode": tests.returncode,
        "tests_output_tail": (tests.stderr or tests.stdout)[-3000:],
        "outside_path": str(outside),
        "outside_file_exists": outside.exists(),
    }
    if len(sys.argv) > 2:
        report["events"] = events_report(sys.argv[2])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
