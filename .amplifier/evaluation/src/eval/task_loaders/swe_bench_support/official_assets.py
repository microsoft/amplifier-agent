"""Pull the official per-instance assets from the scaleapi/SWE-bench_Pro-os repo.

For each instance the official repo ships:
  dockerfiles/base_dockerfile/<instance_id>/Dockerfile      (system deps + repo clone)
  dockerfiles/instance_dockerfile/<instance_id>/Dockerfile  (preprocess + build)
  run_scripts/<instance_id>/run_script.sh                   (how to run the tests)
  run_scripts/<instance_id>/parser.py                       (test stdout/err -> JSON)

These are the authoritative build + grading scripts. We fetch them on the fly by
shallow-cloning the public repo into a cache dir (no submodules) rather than
vendoring any of it into this repo.

Copy-adapted verbatim from the proven prior-art swe_bench_pro package.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

OFFICIAL_REPO = "https://github.com/scaleapi/SWE-bench_Pro-os.git"


@dataclass
class InstanceAssets:
    instance_id: str
    base_dockerfile: str
    instance_dockerfile: str
    run_script: str
    parser: str
    run_script_path: Path
    parser_path: Path


def ensure_repo(cache_dir: str | Path | None = None) -> Path:
    """Shallow-clone the official repo into a cache dir (idempotent)."""
    cache = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "swe-bench-pro-os"
    if (cache / ".git").exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--no-recurse-submodules",
            OFFICIAL_REPO,
            str(cache),
        ],
        check=True,
    )
    return cache


def fetch_assets(instance_id: str, cache_dir: str | Path | None = None) -> InstanceAssets:
    """Return the four official assets for ``instance_id``.

    Raises FileNotFoundError if any expected asset is missing so callers fail
    loudly rather than silently grading against an incomplete setup.
    """
    repo = ensure_repo(cache_dir)
    base = repo / "dockerfiles" / "base_dockerfile" / instance_id / "Dockerfile"
    inst = repo / "dockerfiles" / "instance_dockerfile" / instance_id / "Dockerfile"
    run_script = repo / "run_scripts" / instance_id / "run_script.sh"
    parser = repo / "run_scripts" / instance_id / "parser.py"

    missing = [str(p) for p in (base, inst, run_script, parser) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing official assets for {instance_id}: {missing}. "
            "The instance may use a different directory naming; inspect the repo."
        )

    return InstanceAssets(
        instance_id=instance_id,
        base_dockerfile=base.read_text(encoding="utf-8"),
        instance_dockerfile=inst.read_text(encoding="utf-8"),
        run_script=run_script.read_text(encoding="utf-8"),
        parser=parser.read_text(encoding="utf-8"),
        run_script_path=run_script,
        parser_path=parser,
    )


if __name__ == "__main__":
    import sys

    iid = sys.argv[1]
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    a = fetch_assets(iid, cache)
    print("instance:", a.instance_id)
    print("base_dockerfile lines:", len(a.base_dockerfile.splitlines()))
    print("instance_dockerfile lines:", len(a.instance_dockerfile.splitlines()))
    print("run_script bytes:", len(a.run_script))
    print("parser bytes:", len(a.parser))
    print(
        "base FROM:",
        next((ln for ln in a.base_dockerfile.splitlines() if ln.strip().startswith("FROM")), "?"),
    )
