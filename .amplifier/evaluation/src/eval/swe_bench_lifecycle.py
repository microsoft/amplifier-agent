"""Lifecycle for loader-driven (swe-bench) trials: fetch -> launch -> grade.

Loader-driven tasks have NO static profile.yaml: the swe_bench loader must run
FIRST to fetch the instance + convert the Dockerfiles into a DTU profile, build
the scenario, and synthesize the deterministic grader config. This module is
that lifecycle. It is intentionally SEPARATE from the static
`lifecycle.run_trial` loop because a swe-bench trial is structurally different:

- gold mode runs NO agent at all (it grades the dataset's reference patch), so
  the static loop's install -> AIUser-drive -> extract -> model-rubric-grade
  sequence does not apply;
- grading is deterministic (run the official tests in the DTU), not an LLM audit;
- the environment is a reconstructed repo at /app, not a seeded /workspace.

Forcing both flows through one function would bolt a large mode branch onto a
proven loop for no shared benefit; a dedicated brick keeps each flow simple
(MODULAR_DESIGN_PHILOSOPHY). What IS shared -- the DTU wrapper, the agent
install/compose helpers, the AIUser, and the pluggable grader factory -- is
reused verbatim, so the deterministic grader is still selected by grader.yaml's
`type:` exactly like the model_rubric grader.

Two modes:
  gold   -- apply the dataset's reference patch (no agent). Validates that the
            reconstructed environment + deterministic grader reproduce resolved.
  agent  -- install the agent-under-test, drive it with the issue via the AIUser,
            capture its patch, then grade by test pass/fail.
"""

from __future__ import annotations

import json
import logging
import shlex
import uuid
from pathlib import Path

from eval.ai_user import AIUser
from eval.dtu import DTU
from eval.graders import DeterministicGrader, make_grader
from eval.graders.deterministic import CONFIG_FILENAME, PATCH_FILENAME
from eval.install import compose_launch_profile, install_agent, verify_env
from eval.schema import AgentSpec
from eval.task_loaders.swe_bench import GRADING_DIR, REPO_DIR, SweBenchTaskLoader

logger = logging.getLogger(__name__)

# Some agents' driving patterns write scratch files into the workspace, which is
# the repo via the /workspace -> /app symlink. Remove these before capturing the
# model patch so they do not pollute the diff.
DRIVER_SCRATCH = ["eval-run.out", "eval-run.done", "eval-run.*", "eval-prompt.txt", "answer.txt"]


async def _sh(
    dtu: DTU, script: str, *, log_to: Path | None = None, timeout_s: float | None = 900.0
):
    """Run a login-shell command in the DTU (heredocs / $VAR expansion work)."""
    return await dtu.exec_cmd(
        ["bash", "-lc", script], timeout_s=timeout_s, stream_to_logfile=log_to
    )


async def run_swe_bench_instance(
    *,
    task_dir: Path,
    mode: str,
    out_dir: Path,
    agent: AgentSpec | None = None,
    ai_user: AIUser | None = None,
    cache_dir: str | None = None,
    launch_timeout_s: float = 3600.0,
    grade_timeout_s: float = 3600.0,
) -> dict:
    """Run one swe-bench instance end to end (loader -> launch -> grade -> teardown).

    Args:
        task_dir: The instance dir (holds meta.yaml).
        mode: "gold" (grade the reference patch) or "agent" (grade an agent's patch).
        out_dir: Host output dir (generated profile, patches, logs, grade).
        agent: Required in agent mode; the agent-under-test spec.
        ai_user: Required in agent mode; an already-setup AIUser.
        cache_dir: Optional local clone of the official scaleapi repo.
        launch_timeout_s: Bound for the DTU launch (provisioning builds the repo).
        grade_timeout_s: Bound for the in-DTU grading entry script.

    Returns:
        A JSON-safe result dict with the deterministic verdict and paths.
    """
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode not in ("gold", "agent"):
        raise ValueError(f"unknown mode {mode!r} (expected 'gold' or 'agent')")
    if mode == "agent" and (agent is None or ai_user is None):
        raise ValueError("agent mode requires both `agent` and a setup `ai_user`")

    # ---- LOADER: fetch + convert + synthesize grader config (before launch) ---
    loader = SweBenchTaskLoader(cache_dir=cache_dir)
    logger.info("swe-bench lifecycle: loading %s (mode=%s)", task_dir, mode)
    loaded = await loader.load(task_dir, runtime_dir=out_dir, mode=mode)
    task = loaded.task
    grader_data_dir = loaded.grader_data_dir
    if grader_data_dir is None:
        raise RuntimeError("swe_bench loader did not produce a grader_data_dir")

    result: dict = {
        "instance_id": loaded.extras.get("instance_id"),
        "repo": loaded.extras.get("repo"),
        "mode": mode,
        "generated_profile": str(loaded.profile_path),
        "grader_data_dir": str(grader_data_dir),
    }

    # ---- compose the launch profile (agent mode merges the agent's env) -------
    launch_profile_path = loaded.profile_path
    if mode == "agent":
        assert agent is not None
        missing = verify_env(agent)
        if missing:
            raise RuntimeError(f"agent {agent.id} requires host env not present: {missing}")
        launch_profile_path = out_dir / "launch_profile.yaml"
        compose_launch_profile(agent, task, launch_profile_path)

    # Unique name so multiple agents can run the SAME instance in parallel without
    # an Incus container-name collision.
    dtu_name = f"swebench-{task.id[:36].replace('_', '-')}-{uuid.uuid4().hex[:6]}"[:60]
    logger.info("swe-bench lifecycle: launching DTU %s (provisioning builds the repo)...", dtu_name)
    dtu = await DTU.launch(launch_profile_path, name=dtu_name, launch_timeout_s=launch_timeout_s)
    result["dtu_id"] = dtu.id

    try:
        if mode == "gold":
            gold = grader_data_dir / PATCH_FILENAME
            gold.write_text(loaded.extras["gold_patch"], encoding="utf-8")
            logger.info("swe-bench lifecycle: staged gold patch -> %s", gold)
        else:
            assert agent is not None and ai_user is not None
            result.update(
                await _run_agent(dtu, agent, task, loaded, grader_data_dir, out_dir, ai_user)
            )

        # ---- GRADE (deterministic, factory-selected by grader.yaml `type:`) ---
        grader = make_grader(task.grader_path)
        if not isinstance(grader, DeterministicGrader):
            raise RuntimeError(
                f"expected a DeterministicGrader for {task.grader_path}, got {type(grader).__name__}"
            )
        grader.grade_timeout_s = grade_timeout_s
        await grader.setup()
        logger.info("swe-bench lifecycle: grading (reset -> apply patch -> run tests -> parse)...")
        grader_result = await grader.run(
            grader_yaml_path=task.grader_path,
            task_context=task.scenario,
            dtu_id=dtu.id,
            output_dir=out_dir / "grade",
            grader_data_dir=grader_data_dir,
        )
        grade = json.loads((out_dir / "grade" / "grade.json").read_text(encoding="utf-8"))
        result["grade"] = grade
        result["overall_score"] = grader_result.overall_score
        result["resolved"] = grade.get("resolved")
        logger.info(
            "swe-bench lifecycle: resolved=%s (fail_to_pass %s/%s, pass_to_pass %s/%s)",
            grade.get("resolved"),
            grade.get("fail_to_pass", {}).get("passed"),
            grade.get("fail_to_pass", {}).get("total"),
            grade.get("pass_to_pass", {}).get("passed"),
            grade.get("pass_to_pass", {}).get("total"),
        )
    finally:
        await dtu.destroy()

    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    return result


async def _run_agent(
    dtu: DTU,
    agent: AgentSpec,
    task,
    loaded,
    grader_data_dir: Path,
    out_dir: Path,
    ai_user: AIUser,
) -> dict:
    """Install the agent, drive it with the issue, and capture its patch."""
    # The agent CLIs cd into /workspace; point that at the repo so edits land in it.
    await _sh(dtu, f"rm -rf /workspace && ln -sfn {REPO_DIR} /workspace")

    # Agent runtimes fetch their installers (uv, opencode) with curl. The generated
    # SWE-bench Pro profile provisions git+python but not curl, so install the agent
    # prerequisites before the agent's own setup_cmds run.
    logger.info("swe-bench lifecycle: installing agent prerequisites (curl, ca-certificates)...")
    await _sh(
        dtu, "apt-get update && apt-get install -y --no-install-recommends curl ca-certificates"
    )

    logger.info("swe-bench lifecycle: installing agent %s...", agent.id)
    await install_agent(agent, dtu, log_to=out_dir / "install.log")

    logger.info("swe-bench lifecycle: driving the agent with the issue via AIUser...")
    interaction = await ai_user.run(
        scenario=task.scenario,
        dtu_id=dtu.id,
        invocation_guide=agent.invocation_md,
        workspace_dir="/workspace",
        timeout_s=float(task.timeout_s),
    )
    (out_dir / "ai_user.json").write_text(
        json.dumps(
            {
                "final_assistant_text": interaction.final_assistant_text,
                "elapsed_s": interaction.elapsed_s,
                "timed_out": interaction.timed_out,
                "verdict": interaction.conclude.verdict if interaction.conclude else None,
                "session_id": interaction.ai_user_session_id,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    # Remove driver scratch files that some agents write into the workspace (which
    # is the repo via the symlink) so they do not pollute the patch.
    await _sh(dtu, f"cd {REPO_DIR} && rm -f {' '.join(DRIVER_SCRATCH)}")

    # Diff against the exact commit the deterministic grader resets to before
    # `git apply` (base_commit), NOT the container's HEAD. The grader does
    # `git reset --hard <base_commit>` then `git apply patch.diff`, so the patch
    # must be expressed relative to base_commit to apply cleanly.
    config = json.loads((grader_data_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
    base_commit = str(config["base_commit"])

    # Capture the agent's patch by writing it to a file INSIDE the DTU and then
    # pulling it. We must NOT read it from exec stdout: the exec CLI returns a JSON
    # envelope, so stdout would wrap the diff in JSON and break `git apply`.
    #
    # Three correctness requirements, each a prior failure mode:
    #  1. The grading dir must exist before the redirect writes into it. In agent
    #     mode capture happens BEFORE the grader (which is what creates /grading),
    #     so we `mkdir -p` here or the `>` redirect fails and nothing is written.
    #  2. Stage ALL changes (`git add -A`) so NEW files are captured too; a plain
    #     `git diff` omits untracked files and the agent's fix may add files.
    #  3. Diff against base_commit so the patch applies onto the grader's reset.
    capture = await _sh(
        dtu,
        f"mkdir -p {GRADING_DIR} && cd {REPO_DIR} && git add -A && "
        f"git diff --cached {shlex.quote(base_commit)} > {GRADING_DIR}/{PATCH_FILENAME}",
    )
    if capture.returncode != 0:
        raise RuntimeError(
            f"agent patch capture failed in-DTU for {task.id} (exit {capture.returncode}): "
            f"{capture.stderr.strip() or capture.stdout.strip() or 'no output'}"
        )

    patch_path = grader_data_dir / PATCH_FILENAME
    await dtu.file_pull(f"{GRADING_DIR}/{PATCH_FILENAME}", patch_path)
    size = patch_path.stat().st_size if patch_path.is_file() else 0
    if size == 0:
        # Fail LOUD at the true source: an empty/absent patch means the agent made
        # no capturable changes to the repo, so there is nothing to grade. Surface
        # it here rather than letting it masquerade as an "unresolved" verdict.
        raise RuntimeError(
            f"agent produced an empty patch (0 bytes) for {task.id}: no changes were "
            f"captured from {REPO_DIR} relative to base_commit {base_commit[:12]} "
            f"(nothing to grade)."
        )
    logger.info("swe-bench lifecycle: captured model patch (%d bytes) -> %s", size, patch_path)
    return {
        "model_patch_bytes": size,
        "ai_user_timed_out": interaction.timed_out,
        "ai_user_verdict": interaction.conclude.verdict if interaction.conclude else None,
    }


__all__ = ["run_swe_bench_instance", "DRIVER_SCRATCH"]
