"""Agent images baked once per installed code and container profile, so trials reuse an install instead of repeating it.

A bake launches the surface's container profile as a trial would, so install.sh still installs through the
Digital Twin Universe gateway the way a user does, checks provenance, then commits the twin container to an image.
Trials launch a generated profile running that image.
"""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from typing import Any

import yaml

from amplifier_agent_evaluations import EVAL_ROOT, provenance, trial, universe

# Raised whenever what a bake does to an image changes, so images baked the old way are not reused.
BAKE_FORMAT_VERSION = 1
REPOSITORY = "amplifier-agent-eval"
CACHE = EVAL_ROOT / ".cache"
GRADER_CACHES = CACHE / "grader"
LABEL = "amplifier-agent-eval"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
DOCKER_SECONDS = 1800
KEEP_IMAGES = 3


class BakeError(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def profile_files(surface: str, install: str, profiles: Path = trial.PROFILES) -> list[Path]:
    """The files that decide what a bake of `surface` from `install` installs and how."""
    return [
        profiles / surface / "Dockerfile",
        profiles / surface / "install.sh",
        profiles / surface / install / "compose.yaml",
    ]


def image_key(surface: str, install: str, identity: str, profiles: Path = trial.PROFILES) -> str:
    """sha256 over the bake format, install, surface, expected installed identity and the profile files' contents."""
    parts = [f"format={BAKE_FORMAT_VERSION}", f"install={install}", f"surface={surface}", f"identity={identity}"]
    for path in profile_files(surface, install, profiles):
        parts.append(f"{path.relative_to(profiles).as_posix()}={_sha256(path.read_bytes())}")
    return _sha256("\n".join(parts).encode())


def image_tag(surface: str, install: str, key: str) -> str:
    return f"{REPOSITORY}/{surface}-{install}:{key[:16]}"


def grader_key(grader: Path = trial.GRADER) -> str:
    """sha256 over the grader files a trial pushes, skipping bytecode caches."""
    digest = hashlib.sha256()
    for name in trial.GRADER_FILES:
        root = grader / name
        files = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            if "__pycache__" in path.parts:
                continue
            digest.update(f"{path.relative_to(grader).as_posix()}\0{_sha256(path.read_bytes())}\n".encode())
    return digest.hexdigest()


def grader_cache_path(grader: Path = trial.GRADER, root: Path = GRADER_CACHES) -> Path:
    """The packed uv cache for the grader's current files."""
    return root / grader_key(grader) / "uv-cache.tar"


def baked_profile(compose: Path, tag: str, destination: Path) -> Path:
    """Write `compose` with its twin running the baked image `tag` instead of building and installing.

    Repository paths become absolute because `destination` lives elsewhere. The install finished in the image, so the
    healthcheck passes at once; `start_interval` makes the first probe come after a second rather than an interval.
    """
    config = yaml.safe_load(compose.read_text())
    x_dtu = config.get("x-dtu") or {}
    service = config["services"][x_dtu.get("twin_machine") or "twin"]
    service.pop("build", None)
    service["image"] = tag
    service["pull_policy"] = "never"
    service["command"] = ["sleep", "infinity"]
    service["healthcheck"]["start_interval"] = "1s"
    for repository in x_dtu.get("repositories") or []:
        repository["path"] = str((compose.parent / repository["path"]).resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config, sort_keys=False))
    return destination


def _docker(*args: str, timeout_seconds: int = 120) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout_seconds)
    if done.returncode != 0:
        raise BakeError(f"docker {' '.join(args[:2])} failed: {done.stderr.strip()[-1000:]}")
    return done.stdout


def inspect_image(tag: str) -> dict[str, Any] | None:
    done = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, text=True, timeout=120)
    return json.loads(done.stdout)[0] if done.returncode == 0 else None


def injected_env(container_env: list[str], image_env: list[str]) -> list[str]:
    """The names the container's environment sets that its image's does not set to the same value.

    These are what Compose and the universe overlay added: credentials, the gateway proxy and its CA paths.
    """
    base = set(image_env)
    return sorted({entry.partition("=")[0] for entry in container_env if entry not in base})


def commit_changes(cleared: list[str], labels: dict[str, str]) -> list[str]:
    """`docker commit --change` arguments: run idle, empty every name in `cleared`, and set `labels`."""
    changes = ['CMD ["sleep", "infinity"]', *(f"ENV {name}=" for name in cleared)]
    changes += [f"LABEL {LABEL}.{key}={value}" for key, value in labels.items()]
    return [argument for change in changes for argument in ("--change", change)]


def _twin_container(id: str, twin: str) -> str:
    found = _docker(
        "ps",
        "-q",
        "--filter",
        f"label={COMPOSE_PROJECT_LABEL}={id}",
        "--filter",
        f"label={COMPOSE_SERVICE_LABEL}={twin}",
    ).split()
    if len(found) != 1:
        raise BakeError(f"expected one {twin!r} container in universe {id}, found {len(found)}")
    return found[0]


def cache_grader(id: str, commit: str, cache: Path, logs: Path) -> None:
    """Install the grader in universe `id` as a trial does, then pack its uv cache into `cache` on the host."""
    trial.push_grader(id)
    installed = universe.execute(
        id, trial.direct(trial.install_command(commit)), timeout_seconds=trial.GRADER_INSTALL_SECONDS
    )
    with contextlib.suppress(universe.DigitalTwinUniverseError):
        universe.pull(id, f"{trial.GRADER_OUT}/install.log", logs / "grader-install.log")
    if installed.exit_code != 0:
        raise BakeError(f"grader install exited {installed.exit_code}, see {logs / 'grader-install.log'}")
    packed = universe.execute(
        id, f"tar -cf {trial.GRADER_CACHE_TAR} -C {trial.GRADER_CACHE} .", timeout_seconds=trial.GRADER_INSTALL_SECONDS
    )
    if packed.exit_code != 0:
        raise BakeError(f"packing the grader cache failed: {packed.stderr.strip()[-500:]}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_name(f"{cache.name}.{secrets.token_hex(4)}.partial")
    universe.pull(id, trial.GRADER_CACHE_TAR, partial)
    partial.replace(cache)


def _launch(compose: Path, timeout_seconds: int, log: Path) -> str:
    try:
        return universe.launch(compose, timeout_seconds, log).id
    except universe.DigitalTwinUniverseError as error:
        leftover = universe.leftover_id(error)
        if leftover is not None:
            with contextlib.suppress(Exception):
                universe.destroy(leftover)
        raise


def _verify(id: str, surface: str, install: str, identity: str, logs: Path) -> dict[str, Any]:
    """The provenance verdict for the bake universe's install, with setup.log and installed.json kept in `logs`."""
    for source, name in ((f"{trial.APP}/setup.log", "install.log"), (f"{trial.APP}/installed.json", "installed.json")):
        with contextlib.suppress(universe.DigitalTwinUniverseError):
            universe.pull(id, source, logs / name)
    status = universe.execute(id, f"cat {trial.APP}/.setup-status", timeout_seconds=60).stdout.strip()
    if status != "0":
        return {"ok": False, "reason": f"install status {status!r}, see {logs / 'install.log'}"}
    installed_path = logs / "installed.json"
    installed = json.loads(installed_path.read_text()) if installed_path.is_file() else {}
    return provenance.verdict(installed, install, identity, surface)


def bake(
    surface: str,
    install: str,
    identity: str,
    key: str,
    run_dir: Path,
    launch_seconds: int,
    grader_commit: str,
    grader_cache: Path | None,
) -> dict[str, Any]:
    """Launch the surface's profile, verify the install, then commit the twin as `image_tag(...)`.

    When `grader_cache` is given, the grader is installed in the same universe first and its uv cache packed there.
    The grader's directory is removed before the commit, so the image holds only what install.sh installed.
    """
    tag = image_tag(surface, install, key)
    logs = run_dir / "bake" / f"{surface}-{install}"
    logs.mkdir(parents=True, exist_ok=True)
    compose = trial.compose_file(surface, install)
    twin = (yaml.safe_load(compose.read_text()).get("x-dtu") or {}).get("twin_machine") or "twin"
    started = time.monotonic()
    id = _launch(compose, launch_seconds, logs / "launch.log")
    try:
        verdict = _verify(id, surface, install, identity, logs)
        (logs / "provenance.json").write_text(json.dumps(verdict, indent=2))
        if not verdict["ok"]:
            raise BakeError(f"{surface}-{install} bake failed provenance: {verdict['reason']}")
        if grader_cache is not None:
            cache_grader(id, grader_commit, grader_cache, logs)
        removed = universe.execute(id, f"rm -rf {trial.GRADER_HOME}", timeout_seconds=60)
        if removed.exit_code != 0:
            raise BakeError(f"rm -rf {trial.GRADER_HOME} failed: {removed.stderr.strip()}")
        container = _twin_container(id, twin)
        details = json.loads(_docker("container", "inspect", container))[0]
        base = details["Image"]
        image_env = (inspect_image(base) or {}).get("Config", {}).get("Env") or []
        cleared = injected_env(details["Config"].get("Env") or [], image_env)
        labels = {"key": key, "run": run_dir.name, "base-image": base}
        temporary = f"{tag}-partial-{secrets.token_hex(4)}"
        _docker("commit", *commit_changes(cleared, labels), container, temporary, timeout_seconds=DOCKER_SECONDS)
        try:
            _check_cleared(temporary, cleared)
            _docker("tag", temporary, tag)
        finally:
            _docker("image", "rm", temporary)
        image = inspect_image(tag)
        if image is None:
            raise BakeError(f"{tag} is missing after the commit")
        return {
            "seconds": round(time.monotonic() - started, 1),
            "base_image_id": base,
            "cleared_env": cleared,
            "logs": str(logs),
            "image_id": image["Id"],
        }
    finally:
        universe.destroy(id)


def _check_cleared(tag: str, cleared: list[str]) -> None:
    image = inspect_image(tag) or {}
    kept = [entry.partition("=")[0] for entry in image.get("Config", {}).get("Env") or [] if entry.partition("=")[2]]
    leaked = sorted(set(kept) & set(cleared))
    if leaked:
        raise BakeError(f"the committed image still sets {', '.join(leaked)}")


def select_prunable(images: list[dict[str, str]], keep: int, protected: set[str]) -> list[dict[str, str]]:
    """The images of one repository to remove: all but the `keep` newest by `created`, never one whose `id` is in
    `protected`. `keep` 0 removes nothing."""
    if keep <= 0:
        return []
    ordered = sorted(images, key=lambda image: image["created"], reverse=True)
    return [image for image in ordered[keep:] if image["id"] not in protected]


def select_prunable_caches(caches: list[tuple[Path, float]], keep: int, current: Path) -> list[Path]:
    """The grader cache directories to remove: all but the `keep` most recently used, never `current`."""
    if keep <= 0:
        return []
    ordered = sorted(caches, key=lambda cache: cache[1], reverse=True)
    return [path for path, _ in ordered[keep:] if path != current]


def _repository_images(repository: str) -> list[dict[str, str]]:
    tags = [
        tag
        for tag in _docker("image", "ls", "--format", "{{.Repository}}:{{.Tag}}", repository).split()
        if "<none>" not in tag
    ]
    if not tags:
        return []
    return [
        {"tag": tag, "id": image["Id"], "created": image["Created"]}
        for image in json.loads(_docker("image", "inspect", *tags))
        for tag in image.get("RepoTags") or []
        if tag in tags
    ]


def _images_in_use() -> set[str]:
    containers = _docker("ps", "-aq").split()
    if not containers:
        return set()
    return set(_docker("container", "inspect", "--format", "{{.Image}}", *containers).split())


def prune(repositories: set[str], used: set[str], keep: int, current_cache: Path) -> dict[str, list[dict[str, Any]]]:
    """Keep the `keep` newest images of each repository in `repositories` and grader caches beside `current_cache`.

    Images in `used` or in any container stay. A removal that fails, such as one racing another run, is recorded and
    reported, never raised.
    """
    removed: dict[str, list[dict[str, Any]]] = {"images": [], "grader_caches": []}
    if keep <= 0:
        return removed
    protected = used | _images_in_use()
    for repository in sorted(repositories):
        for image in select_prunable(_repository_images(repository), keep, protected):
            done = subprocess.run(["docker", "image", "rm", image["tag"]], capture_output=True, text=True, timeout=120)
            error = done.stderr.strip() if done.returncode != 0 else None
            removed["images"].append({"tag": image["tag"], "id": image["id"], "error": error})
            print(f"prune      {image['tag']} {'not removed: ' + error if error else 'removed'}", flush=True)
    root = current_cache.parents[1]
    caches = [(path, path.stat().st_mtime) for path in root.iterdir() if path.is_dir()] if root.is_dir() else []
    for path in select_prunable_caches(caches, keep, current_cache.parent):
        error = None
        try:
            shutil.rmtree(path)
        except OSError as failure:
            error = str(failure)
        removed["grader_caches"].append({"path": str(path), "error": error})
        print(f"prune      {path} {'not removed: ' + error if error else 'removed'}", flush=True)
    return removed


def ensure(
    surfaces: set[str],
    install: str,
    expected: dict[str, str],
    run_dir: Path,
    launch_seconds: int,
    grader_commit: str,
    rebake: bool = False,
    keep: int = KEEP_IMAGES,
) -> dict[str, Any]:
    """The image for each surface, reused when one with the same key exists and baked otherwise, plus the grader
    cache, then pruning to `keep` per repository. Writes `<run_dir>/bake.json` and a baked profile per surface under
    `<run_dir>/profiles/`."""
    cache = grader_cache_path()
    images: dict[str, dict[str, Any]] = {}
    for surface in sorted(surfaces):
        key = image_key(surface, install, expected[surface])
        tag = image_tag(surface, install, key)
        existing = None if rebake else inspect_image(tag)
        entry: dict[str, Any] = {"key": key, "tag": tag, "cache_hit": existing is not None}
        if existing is None:
            baked = bake(
                surface,
                install,
                expected[surface],
                key,
                run_dir,
                launch_seconds,
                grader_commit,
                None if cache.is_file() else cache,
            )
            entry |= baked | {"baked_by": run_dir.name}
        else:
            labels = existing.get("Config", {}).get("Labels") or {}
            entry |= {
                "image_id": existing["Id"],
                "base_image_id": labels.get(f"{LABEL}.base-image"),
                "baked_by": labels.get(f"{LABEL}.run"),
                "logs": None,
            }
        entry["profile"] = str(
            baked_profile(trial.compose_file(surface, install), tag, run_dir / "profiles" / f"{surface}-{install}.yaml")
        )
        images[surface] = entry
        how = "reused" if entry["cache_hit"] else f"baked in {entry['seconds']:.0f}s"
        print(f"image      {tag} {how}, baked by {entry['baked_by']}", flush=True)
    if not cache.is_file():
        first = images[min(images)]
        logs = run_dir / "bake" / "grader"
        logs.mkdir(parents=True, exist_ok=True)
        id = _launch(Path(first["profile"]), launch_seconds, logs / "launch.log")
        try:
            cache_grader(id, grader_commit, cache, logs)
        finally:
            universe.destroy(id)
    os.utime(cache.parent)
    print(f"grader     cache {cache}", flush=True)
    repositories = {entry["tag"].rpartition(":")[0] for entry in images.values()}
    try:
        pruned: dict[str, Any] = prune(repositories, {entry["image_id"] for entry in images.values()}, keep, cache)
    except (BakeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"prune      skipped: {error}", flush=True)
        pruned = {"error": str(error)}
    record = {"images": images, "grader_cache": str(cache), "pruned": pruned}
    (run_dir / "bake.json").write_text(json.dumps(record, indent=2))
    return record
