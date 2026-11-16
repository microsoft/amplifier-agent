"""Replace the bundled amplifier-smart-tools skill with the copy at a ref of microsoft/amplifier-smart-tools."""

import argparse
import contextlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
from types import ModuleType
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "packages/engine/src/amplifier_agent_engine/_engine/smart_tools.py"


def engine_module() -> ModuleType:
    """The engine's smart tools module, loaded by path so the script needs only the standard library."""
    spec = importlib.util.spec_from_file_location("smart_tools", MODULE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load {MODULE}. Run this script from a checkout of amplifier-agent.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def swap(staging: Path, target: Path) -> None:
    """Put the complete copy at ``staging`` in place of ``target``."""
    retired = target.with_name(f".retired-{uuid.uuid4().hex}")
    with contextlib.suppress(FileNotFoundError):
        target.rename(retired)
    try:
        staging.rename(target)
    finally:
        shutil.rmtree(retired, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main", help="the branch, tag, or commit to copy (default: main)")
    args = parser.parse_args()
    smart_tools = engine_module()
    bundled: Path = smart_tools.BUNDLED
    source_file = bundled / smart_tools.SOURCE_FILE
    stop = threading.Event()
    remedy = (
        "Check access to api.github.com and raw.githubusercontent.com, and that --ref names a branch, tag, or "
        f"commit of {smart_tools.REPOSITORY}."
    )
    try:
        commit = smart_tools.latest_commit(args.ref, stop)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot resolve {args.ref!r}: {exc}\n{remedy}") from exc
    with contextlib.suppress(OSError, ValueError):
        if json.loads(source_file.read_text())["commit"] == commit and smart_tools.complete(bundled):
            print(f"The bundled {smart_tools.SKILL_NAME} is already at {commit}.")
            return
    bundled.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=bundled.parent))
    try:
        try:
            smart_tools.fetch(commit, staging, stop)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Cannot copy {smart_tools.SKILL_NAME} at {commit}: {exc}\n{remedy}") from exc
        source = {"repository": smart_tools.REPOSITORY, "commit": commit}
        (staging / smart_tools.SOURCE_FILE).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
        swap(staging, bundled)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Bundled {smart_tools.SKILL_NAME} at {commit} in {bundled.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
