# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "amplifier-bundle-digital-twin-universe @ git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@755a1439438ba652afcfddb7f5cefe02f2834695",
#   "amplifier-bundle-gitea @ git+https://github.com/microsoft/amplifier-bundle-gitea@261953d5372ff487d0a9872d914724ef6115e5a0",
# ]
# ///
"""Serve an existing Git commit through disposable Gitea and verify a DTU install."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from amplifier_bundle_digital_twin_universe import engine

ROOT = Path(__file__).resolve().parents[2]


def command(arguments: list[str]) -> str:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=120)
    return result.stdout.strip()


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def destroy(state: dict, path: Path) -> None:
    errors = []
    for kind in ("consumer", "gitea"):
        resource = state.get(kind)
        if not resource:
            continue
        try:
            if kind == "consumer":
                try:
                    engine.destroy(resource["id"])
                except RuntimeError as error:
                    if str(error) != f"Environment not found: {resource['id']}":
                        raise
            else:
                try:
                    command(["amplifier-gitea", "destroy", resource["id"]])
                except subprocess.CalledProcessError as error:
                    if (
                        error.stderr or ""
                    ).strip() != f"Error: Environment not found: {resource['id']}":
                        raise
            del state[kind]
        except Exception as error:
            errors.append(error)
        try:
            save(path, state)
        except OSError as error:
            errors.append(error)
    if errors:
        raise ExceptionGroup(f"Resource cleanup failed; retain {path} to retry.", errors)
    path.unlink(missing_ok=True)


def prepare(args: argparse.Namespace) -> None:
    if args.state.exists():
        raise ValueError(
            "Choose a new resource-state path; an existing environment must not be overwritten."
        )
    source = args.repository.resolve()
    if args.state.resolve().is_relative_to(source) or args.state.resolve().is_relative_to(ROOT):
        raise ValueError(
            "Store resource state outside the checkout because it contains credentials."
        )
    sha = command(["git", "-C", str(source), "rev-parse", f"{args.ref}^{{commit}}"])
    state = {"revision": sha, "source_repository": str(source)}
    try:
        gitea = json.loads(
            command(
                [
                    "amplifier-gitea",
                    "create",
                    "--port",
                    str(args.port),
                    "--name",
                    "amplifier-agent-source-" + uuid.uuid4().hex[:8],
                ]
            )
        )
        state["gitea"] = gitea
        save(args.state, state)
        request = Request(
            gitea["gitea_url"] + "/api/v1/user/repos",
            data=json.dumps(
                {
                    "name": "amplifier-agent",
                    "auto_init": False,
                    "default_branch": "v1",
                    "private": True,
                }
            ).encode(),
            headers={
                "Authorization": "token " + gitea["token"],
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=30):
            pass
        # Copy existing objects only. This does not stage files, create commits, or push refs.
        with tempfile.TemporaryDirectory(prefix="amplifier-source-") as temporary:
            bundle = Path(temporary) / "candidate.bundle"
            command(["git", "-C", str(source), "bundle", "create", str(bundle), args.ref])
            command(
                ["docker", "cp", str(bundle), f"{gitea['container_name']}:/tmp/candidate.bundle"]
            )
            git_dir = "/data/git/repositories/admin/amplifier-agent.git"
            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "git",
                    gitea["container_name"],
                    "git",
                    "--git-dir",
                    git_dir,
                    "fetch",
                    "/tmp/candidate.bundle",
                    f"{args.ref}:refs/heads/v1",
                ]
            )
            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "git",
                    gitea["container_name"],
                    "git",
                    "--git-dir",
                    git_dir,
                    "symbolic-ref",
                    "HEAD",
                    "refs/heads/v1",
                ]
            )
        consumer = engine.launch(
            str(ROOT / ".amplifier/digital-twin-universe/profiles/source-install.yaml"),
            {"GITEA_URL": gitea["gitea_url"], "GITEA_TOKEN": gitea["token"]},
            name="aa-v1-source-" + uuid.uuid4().hex[:8],
        )
        state["consumer"] = consumer
        save(args.state, state)
        print(json.dumps({"consumer": consumer["id"], "revision": sha, "state": str(args.state)}))
    except BaseException as error:
        try:
            destroy(state, args.state)
        except Exception as cleanup_error:
            error.add_note(f"Cleanup needs attention: {cleanup_error}")
        raise


def verify(args: argparse.Namespace) -> None:
    state = json.loads(args.state.read_text())
    environment = state["consumer"]["id"]
    expected = state["revision"]

    def execute(operation, timeout=120):
        result = engine.exec_command(environment, operation, timeout=timeout)
        if result["exit_code"]:
            raise RuntimeError(
                f"Consumer command failed: {shlex.join(operation)}\n{result['stderr']}"
            )
        return result

    execute(["uv", "init", "--bare", "--python", "3.12", "/opt/consumer"])
    program = Path(__file__).with_name("source_install.py").read_text()
    for requirement, packages in [
        (
            "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/python",
            ["amplifier-agent", "amplifier-agent-engine"],
        ),
        (
            "amplifier-agent-http @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/http",
            ["amplifier-agent", "amplifier-agent-engine", "amplifier-agent-http"],
        ),
    ]:
        execute(["uv", "--no-cache", "--directory", "/opt/consumer", "add", requirement])
        execute(["uv", "--directory", "/opt/consumer", "sync", "--locked"])
        result = execute(
            [
                "uv",
                "--directory",
                "/opt/consumer",
                "run",
                "--no-sync",
                "python",
                "-c",
                program,
                expected,
                *packages,
            ],
            timeout=60,
        )
        print(result["stdout"].strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "destroy"))
    parser.add_argument(
        "--state", type=Path, required=True, help="Private state file outside the checkout."
    )
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--ref", default="HEAD", help="An existing named Git ref to serve.")
    parser.add_argument("--port", type=int, default=11175)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args)
    elif args.action == "verify":
        verify(args)
    else:
        destroy(json.loads(args.state.read_text()), args.state)


if __name__ == "__main__":
    main()
