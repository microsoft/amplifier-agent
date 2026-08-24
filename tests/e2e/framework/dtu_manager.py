"""High-level orchestration of the warm DTU (used by cli.py and conftest.py).

Ties together the Gitea mirror, the profile launch, and the state file into the
four lifecycle verbs the harness exposes: provision / is_warm / refresh / teardown.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from . import dtu, state
from .progress import log

# tests/e2e/framework/dtu_manager.py -> framework -> e2e -> tests -> amplifier-agent (repo root)
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
PROFILE_REL = ".amplifier/digital-twin-universe/profiles/e2e.yaml"
DTU_ASSETS_REL = "tests/e2e/framework/provisioning"

GITEA_NAME = "aa-e2e"
DTU_NAME = "aa-e2e"

# Repos the checked-in profile already redirects to the Gitea mirror. Everything else
# is redirected only when it arrives via --repo (see _profile_with_extra_rules).
PROFILE_REDIRECTED_REPOS = ("amplifier-agent",)

# Default in-DTU server coordinates. The `server` fixture starts the HTTP server;
# these are recorded in the state file so tests know where to reach it.
DEFAULT_SERVER_BASE_URL = "http://127.0.0.1:9099"
DEFAULT_SERVER_TOKEN = "local-dev-secret"


def parse_extra_repos(specs: Sequence[str]) -> list[dtu.RepoSpec]:
    """Parse the raw ``--repo`` values, rejecting duplicate repo names.

    Two specs naming the same repo would fight over one mirror and one rewrite rule, so
    that is a hard error rather than a last-one-wins surprise.
    """
    parsed: list[dtu.RepoSpec] = []
    seen: dict[str, str] = {}
    for spec in specs:
        repo = dtu.parse_repo_spec(spec)
        if repo.name in seen:
            raise dtu.DTUError(f"--repo {spec!r} conflicts with earlier --repo {seen[repo.name]!r} (same repo name)")
        seen[repo.name] = spec
        parsed.append(repo)
    return parsed


def _mirror_repos(gitea: dict[str, Any], extra: Sequence[dtu.RepoSpec] = ()) -> list[str]:
    """Ensure + snapshot-push every dirty (and always amplifier-agent) repo, plus extras.

    Returns the mirrored repo names in push order. Extras are pushed LAST so an explicit
    ``--repo NAME@REF`` wins when NAME also happens to be a dirty candidate repo.

    Which source an extra comes from:

    * local checkout, no ref -> working-tree snapshot (same treatment as the candidates)
    * local checkout, w/ ref -> that committed ref, working tree ignored
    * no local checkout      -> cloned from GitHub at the ref (default ``main``)
    """
    repos = dtu.dirty_repos(str(WORKSPACE_ROOT))
    for repo in repos:
        local_path = WORKSPACE_ROOT / repo
        dtu.ensure_repo(gitea["port"], gitea["token"], repo)
        dtu.snapshot_push(str(local_path), gitea["port"], gitea["token"], repo)

    for spec in extra:
        local_path = WORKSPACE_ROOT / spec.name
        has_local = (local_path / ".git").exists()
        dtu.ensure_repo(gitea["port"], gitea["token"], spec.name)
        if has_local and spec.ref is None:
            dtu.snapshot_push(str(local_path), gitea["port"], gitea["token"], spec.name)
        else:
            dtu.snapshot_push_ref(
                str(local_path) if has_local else None,
                spec.github_url,
                spec.ref or "main",
                gitea["port"],
                gitea["token"],
                spec.name,
            )
        if spec.name not in repos:
            repos.append(spec.name)
    return repos


def _build_varmap(gitea: dict[str, Any]) -> dict[str, str]:
    """Assemble the --var map for launch/update.

    The ``VLLM_*`` entries travel as vars rather than ``passthrough`` entries on
    purpose. Passthrough copies the host value verbatim, but the vllm suite targets
    a vLLM server running on the HOST, and inside the container ``localhost`` is the
    container. DTU rewrites ``localhost`` / ``127.0.0.1`` in *var values* to the
    bridge gateway IP at launch, which is the same mechanism ``GITEA_URL`` already
    depends on -- so routing the endpoint through a var is what makes
    ``VLLM_BASE_URL=http://localhost:8007/v1`` actually resolve to the host server.

    Always emitted, even when unset on the host: unresolved ``${VAR}`` references are
    left verbatim by DTU's substitution, so omitting them would leak the literal
    string ``${VLLM_BASE_URL}`` into the container environment. An empty value is
    correct and unambiguous -- the vllm suite skips on it.
    """
    return {
        "GITEA_URL": gitea["gitea_url"],
        "GITEA_TOKEN": gitea["token"],
        "AA_E2E_BASE_IMAGE": "ubuntu:24.04",
        "VLLM_BASE_URL": os.environ.get("VLLM_BASE_URL", ""),
        "VLLM_MODEL": os.environ.get("VLLM_MODEL", ""),
        "VLLM_API_KEY": os.environ.get("VLLM_API_KEY", ""),
    }


def _profile_with_extra_rules(profile_src: Path, extra: Sequence[dtu.RepoSpec]) -> str:
    """Return the profile YAML text with one ``url_rewrites`` rule per extra repo.

    Mirroring a repo to Gitea is only half the job: without a rewrite rule the DTU still
    resolves it from GitHub, so the mirror is never read. Each injected rule mirrors the
    shape of the checked-in amplifier-agent rule.

    Injection happens on the STAGED COPY only. The checked-in profile is never touched.
    Rules already present in the profile are left alone so an explicit ``--repo`` for a
    repo the profile already redirects cannot produce a duplicate, shadowed rule.

    Uses pyyaml, already a declared dependency of this project. Comments are lost in the
    round-trip, which is fine: the staged copy is a throwaway launch artifact and the
    commentary lives in the checked-in file.
    """
    data = yaml.safe_load(profile_src.read_text(encoding="utf-8"))
    rewrites = data.setdefault("url_rewrites", {})
    rules = rewrites.setdefault("rules", [])
    existing = {rule.get("match") for rule in rules if isinstance(rule, dict)}

    for spec in extra:
        match = f"github.com/{spec.owner}/{spec.name}"
        if match in existing:
            continue
        rules.append({"match": match, "target": "${GITEA_URL}/admin/" + spec.name})
        existing.add(match)

    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def _stage_launch_dir(extra: Sequence[dtu.RepoSpec] = ()) -> str:
    """Copy the profile + dtu assets into a temp dir so profile ./dtu/... paths resolve.

    With no extra repos the profile is copied verbatim, so the default path is byte-for-byte
    what is checked in. Extras get their ``url_rewrites`` rules injected into the copy.

    Returns the path to the staged profile YAML.
    """
    tmp = tempfile.mkdtemp(prefix="aa-e2e-launch-")
    profile_src = REPO_ROOT / PROFILE_REL
    profile_dst = Path(tmp) / "e2e.yaml"
    if extra:
        profile_dst.write_text(_profile_with_extra_rules(profile_src, extra), encoding="utf-8")
        log(f"provision: injected url_rewrites rules for {', '.join(spec.name for spec in extra)}")
    else:
        shutil.copyfile(profile_src, profile_dst)

    assets_src = REPO_ROOT / DTU_ASSETS_REL
    assets_dst = Path(tmp) / "dtu"
    shutil.copytree(assets_src, assets_dst)

    return str(profile_dst)


def _warn_extra_repos(mirrored: Sequence[str], extra: Sequence[dtu.RepoSpec] = ()) -> None:
    """Warn about repos that are mirrored to Gitea but NOT redirected inside the DTU.

    A repo in that state is snapshotted for nothing: the DTU still resolves it from
    GitHub. That is the situation for a dirty amplifier-core or amplifier-foundation,
    which are mirrored automatically but have no rewrite rule. Repos passed via
    ``--repo`` do get a rule injected at launch, so they never warn.
    """
    redirected = set(PROFILE_REDIRECTED_REPOS) | {spec.name for spec in extra}
    unredirected = [repo for repo in mirrored if repo not in redirected]
    if unredirected:
        print(
            f"[dtu_manager] warning: mirrored {unredirected} to Gitea but they are still "
            "resolved from GitHub inside the DTU; pass --repo <name> to redirect them."
        )


def _check_passthrough_env() -> None:
    """Warn about suite env vars that are missing on the launching process.

    Two mechanisms end up in the same place. DTU bakes each ``passthrough.services``
    value into ``/etc/profile.d/dtu-env.sh`` at launch with a bare ``if value:`` guard,
    and ``_build_varmap``'s ``VLLM_*`` vars are written by ``setup-vllm-env.sh``. Both
    treat an absent value as an empty export rather than an error, so the failure
    surfaces much later as an opaque provider auth or connection error. Warning here
    turns that into an immediate, actionable message.

    Deliberately a warning, not a hard failure: these are per-suite requirements, and
    a missing GITHUB_TOKEN should not block someone running the skills or modes suites.
    Each suite enforces its own requirement directly, and inside the container, which
    is what actually matters -- github_copilot fails loud via
    ``test_ghcp_token_reaches_dtu``, gemini skips itself via its ``gemini_key`` fixture,
    vllm skips itself via its ``vllm_endpoint`` fixture.
    """
    required = (
        ("ANTHROPIC_API_KEY", "most suites will fail"),
        ("GITHUB_TOKEN", "the github_copilot suite will fail"),
        ("GOOGLE_API_KEY", "the gemini suite will skip"),
        ("VLLM_BASE_URL", "the vllm suite will skip"),
    )
    for var, consequence in required:
        if not os.environ.get(var):
            print(
                f"[dtu_manager] warning: {var} is not set on this process, so it will NOT "
                f"be exported inside the DTU; {consequence}."
            )


def _find_instance(name: str) -> dict[str, Any] | None:
    """Return the DTU instance dict named ``name``, or None if it does not exist."""
    for inst in dtu.list_instances():
        if inst.get("id") == name:
            return inst
    return None


def _write_state(
    dtu_id: str,
    dtu_name: str,
    gitea: dict[str, Any],
    extra: Sequence[dtu.RepoSpec] = (),
) -> dict[str, Any]:
    new_state: dict[str, Any] = {
        "dtu_id": dtu_id,
        "dtu_name": dtu_name,
        "gitea_id": gitea["id"],
        "gitea_port": gitea["port"],
        # What --repo this DTU was actually built with. url_rewrites rules are baked in
        # at launch, so a later --skip-setup run or refresh has to be checked against it.
        "extra_repos": [spec.key for spec in extra],
        "server_base_url": DEFAULT_SERVER_BASE_URL,
        "server_token": DEFAULT_SERVER_TOKEN,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.write_state(new_state)
    return new_state


def warn_repo_mismatch(extra_repos: Sequence[str]) -> None:
    """Warn when a warm DTU was not provisioned with the requested ``--repo`` set.

    Reused by ``run --skip-setup``, which reuses the container as-is. The mirror content
    and the rewrite rules were both fixed at provision time, so a differing --repo set is
    silently ignored unless we say so.
    """
    current = state.read_state() or {}
    provisioned = set(current.get("extra_repos", []))
    requested = {spec.key for spec in parse_extra_repos(extra_repos)}
    if provisioned != requested:
        print(
            f"[dtu_manager] warning: this DTU was provisioned with --repo {sorted(provisioned) or '(none)'} "
            f"but you asked for {sorted(requested) or '(none)'}; the running DTU does NOT match. "
            "Re-run without --skip-setup (or `up`) to rebuild with the requested repos."
        )


def provision(extra_repos: Sequence[str] = ()) -> dict[str, Any]:
    """Provision a fresh warm DTU: mirror latest code to Gitea, destroy any existing
    aa-e2e container, then launch a clean one.

    Why always fresh (not an in-place ``update``): ``uv tool install --reinstall`` wipes
    amplifier-agent's lazily-installed provider module, which breaks the HTTP ``serve``
    model enumeration (``serve`` exits 2). A clean launch reliably yields a working CLI
    *and* server, so every ``run`` rebuilds rather than updating in place. A fresh launch
    is ~90s. Use ``--skip-setup`` to re-run against the existing container, or ``refresh``
    for a fast code-only in-place update (CLI-only iteration; leaves ``serve`` broken).

    ``extra_repos`` are raw ``--repo`` values (``[owner/]name[@ref]``). Each one is
    mirrored to Gitea AND given a ``url_rewrites`` rule in the staged profile, so the DTU
    actually installs it from the mirror instead of GitHub.
    """
    log("provision: starting fresh DTU provision")
    _check_passthrough_env()
    extra = parse_extra_repos(extra_repos)
    gitea = dtu.ensure_gitea(name=GITEA_NAME)
    _warn_extra_repos(_mirror_repos(gitea, extra), extra)
    varmap = _build_varmap(gitea)

    existing = _find_instance(DTU_NAME)
    if existing:
        log(f"provision: existing '{DTU_NAME}' found; destroying for a clean rebuild")
        dtu.destroy(existing["id"])

    profile_path = _stage_launch_dir(extra)
    launched = dtu.launch(profile_path, varmap, name=DTU_NAME)
    dtu_id = launched["id"]
    dtu.wait_ready(dtu_id)
    result = _write_state(dtu_id, launched.get("name", DTU_NAME), gitea, extra)
    log("provision: done; DTU is warm and state written")
    return result


def is_warm() -> bool:
    """True if a state file exists and its DTU is currently ready."""
    current = state.read_state()
    if not current:
        return False
    try:
        return dtu.check_ready(current["dtu_id"])
    except Exception:
        return False


def refresh(extra_repos: Sequence[str] = ()) -> None:
    """Re-mirror local repos and re-run the in-DTU install in place (no relaunch).

    With no ``--repo`` given this re-mirrors exactly what the DTU was provisioned with,
    so a refresh stays consistent with the running container. A DIFFERENT --repo set can
    only re-push mirrors: ``url_rewrites`` rules are baked into the container at launch
    and an in-place update cannot change them, so that case warns and needs a full `up`.
    """
    current = state.read_state()
    if not current:
        raise RuntimeError("no warm DTU to refresh; run `up` first")

    provisioned = list(current.get("extra_repos", []))
    extra = parse_extra_repos(extra_repos) if extra_repos else parse_extra_repos(provisioned)
    if {spec.key for spec in extra} != set(provisioned):
        print(
            f"[dtu_manager] warning: this DTU was provisioned with --repo {sorted(provisioned) or '(none)'}; "
            "refresh can re-push mirrors but cannot change url_rewrites rules (they are baked in at "
            "launch). Run `up` to rebuild with the requested repos."
        )

    log("refresh: re-mirroring code and updating DTU in place")
    gitea = dtu.ensure_gitea(name=GITEA_NAME)
    _mirror_repos(gitea, extra)
    varmap = _build_varmap(gitea)
    dtu.update(current["dtu_id"], varmap)
    log("refresh: done")


def teardown() -> None:
    """Destroy the DTU instance (if any) and clear state. Leaves Gitea running."""
    current = state.read_state()
    if current:
        dtu.destroy(current["dtu_id"])
    state.clear_state()
    log("teardown: state cleared")
