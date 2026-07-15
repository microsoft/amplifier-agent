"""Lifecycle for loader-driven (automation-bench) trials: load -> launch -> seed world -> drive -> grade.

Loader-driven tasks have NO static profile.yaml: the automation_bench loader must
run FIRST to synthesize a DTU profile (Ubuntu + a uv-managed python-3.13 venv with
AutomationBench installed and an `ab-tool` CLI on PATH), build the agent-facing
scenario, and materialize the seeded workspace + grader data dir. This module is
that lifecycle. It is intentionally SEPARATE from the static `lifecycle.run_trial`
loop (and from the swe-bench lifecycle) because an automation-bench trial is
structurally its own thing:

- the environment is a simulated business world persisted to a file at
  /workspace/.ab_world.json, seeded by `ab-tool seed` before the agent runs;
- the agent acts ONLY through the `ab-tool` CLI (search/fetch), not the filesystem;
- grading is deterministic (assertions on the final world state), not an LLM audit.

What IS shared -- the DTU wrapper, the agent install/compose helpers, the AIUser,
and the pluggable grader factory -- is reused verbatim, so the automation_bench
grader is still selected by grader.yaml's `type:` exactly like the others.

Only one mode: agent. AutomationBench measures an agent driving the tool surface;
there is no "gold" reference run (the loader installs NO agent, so a bare run has
nothing to drive the world).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from eval.ai_user import AIUser
from eval.dtu import DTU
from eval.extractor import Extractor
from eval.graders import make_grader
from eval.graders.automation_bench import AutomationBenchGrader
from eval.install import compose_launch_profile, install_agent, seed_workspace, verify_env
from eval.metrics import build_metrics
from eval.schema import AgentSpec
from eval.task_loaders.automation_bench import (
    AutomationBenchTaskLoader,
    AB_TOOL_PY,
    TASK_INFO_FILENAME,
    WORKSPACE_DIR,
    WORLD_FILE,
)

logger = logging.getLogger(__name__)


async def _sh(
    dtu: DTU, script: str, *, log_to: Path | None = None, timeout_s: float | None = 900.0
):
    """Run a login-shell command in the DTU (heredocs / $VAR expansion work)."""
    return await dtu.exec_cmd(
        ["bash", "-lc", script], timeout_s=timeout_s, stream_to_logfile=log_to
    )


async def run_automation_bench_instance(
    *,
    task_dir: Path,
    out_dir: Path,
    agent: AgentSpec,
    ai_user: AIUser,
    extractor: Extractor | None = None,
    launch_timeout_s: float = 3600.0,
    grade_timeout_s: float = 600.0,
) -> dict:
    """Run one automation-bench task end to end (load -> launch -> seed -> drive -> grade -> teardown).

    Args:
        task_dir: The task dir (holds meta.yaml + task_info.json).
        out_dir: Host output dir (generated profile, seeded workspace, logs, grade).
        agent: The agent-under-test spec (required; automation-bench only measures an agent).
        ai_user: An already-setup AIUser to drive the agent.
        launch_timeout_s: Bound for the DTU launch (provisioning installs AutomationBench).
        grade_timeout_s: Bound for the in-DTU `ab-tool grade` step.

    Returns:
        A JSON-safe result dict with the deterministic verdict and paths.
    """
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- LOADER: synthesize profile + scenario + grader data (before launch) --
    loader = AutomationBenchTaskLoader()
    logger.info("automation-bench lifecycle: loading %s", task_dir)
    loaded = await loader.load(task_dir, runtime_dir=out_dir, mode="agent")
    task = loaded.task
    grader_data_dir = loaded.grader_data_dir
    if grader_data_dir is None:
        raise RuntimeError("automation_bench loader did not produce a grader_data_dir")

    result: dict = {
        "task": loaded.extras.get("task"),
        "mode": "agent",
        "generated_profile": str(loaded.profile_path),
        "grader_data_dir": str(grader_data_dir),
    }

    # ---- compose the launch profile (merge the agent's required env) ----------
    missing = verify_env(agent)
    if missing:
        raise RuntimeError(f"agent {agent.id} requires host env not present: {missing}")
    launch_profile_path = out_dir / "launch_profile.yaml"
    compose_launch_profile(agent, task, launch_profile_path)

    # Unique name so multiple agents can run the SAME task in parallel without an
    # Incus container-name collision.
    dtu_name = f"abench-{task.id[:36].replace('_', '-').replace('.', '-')}-{uuid.uuid4().hex[:6]}"[
        :60
    ]
    logger.info(
        "automation-bench lifecycle: launching DTU %s (provisioning installs AutomationBench)...",
        dtu_name,
    )
    dtu = await DTU.launch(launch_profile_path, name=dtu_name, launch_timeout_s=launch_timeout_s)
    result["dtu_id"] = dtu.id

    try:
        # ---- install the agent-under-test ------------------------------------
        logger.info("automation-bench lifecycle: installing agent %s...", agent.id)
        await install_agent(agent, dtu, log_to=out_dir / "install.log")

        # ---- seed the workspace (ab_tool.py + task_info.json + INSTRUCTIONS.md) ----
        logger.info("automation-bench lifecycle: seeding workspace into %s...", WORKSPACE_DIR)
        await seed_workspace(task, dtu)

        # ---- place ab_tool.py where the `ab-tool` shim expects it -------------
        # seed_workspace pushes ab_tool.py into /workspace; the shim execs
        # /opt/ab-tool/ab_tool.py, so copy it into place before first use.
        cp_res = await _sh(
            dtu,
            f"cp {WORKSPACE_DIR}/ab_tool.py {AB_TOOL_PY}",
            log_to=out_dir / "seed.log",
        )
        if cp_res.returncode != 0:
            raise RuntimeError(
                f"failed to stage ab_tool.py into {AB_TOOL_PY} (exit {cp_res.returncode}): "
                f"{cp_res.stderr.strip() or cp_res.stdout.strip() or 'no output'}"
            )

        # ---- seed the simulated world from the vendored task_info.json --------
        logger.info("automation-bench lifecycle: building the simulated world (`ab-tool seed`)...")
        seed_res = await _sh(
            dtu,
            f"AB_WORLD_FILE={WORLD_FILE} ab-tool --world {WORLD_FILE} seed "
            f"--info {WORKSPACE_DIR}/{TASK_INFO_FILENAME}",
            log_to=out_dir / "seed.log",
        )
        if seed_res.returncode != 0:
            raise RuntimeError(
                f"world seed failed for {task.id} (exit {seed_res.returncode}): "
                f"{seed_res.stderr.strip() or seed_res.stdout.strip() or 'no output'}"
            )

        # ---- drive the agent with the scenario via the AIUser -----------------
        logger.info("automation-bench lifecycle: driving the agent with the scenario via AIUser...")
        interaction = await ai_user.run(
            scenario=task.scenario,
            dtu_id=dtu.id,
            invocation_guide=agent.invocation_md,
            workspace_dir=WORKSPACE_DIR,
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
        result["ai_user_timed_out"] = interaction.timed_out
        result["ai_user_verdict"] = interaction.conclude.verdict if interaction.conclude else None

        # ---- EXTRACT agent artifacts (agent-driven, robust; non-fatal) --------
        # An AI extractor session explores the DTU and pulls the agent-under-test's
        # session logs / transcripts / deliverables to the host, adapting to wherever
        # THIS agent actually wrote them (opencode vs amplifier paths differ). Kept
        # non-fatal: a capture miss must not lose the graded verdict.
        extracted_dir = out_dir / "extracted"
        if extractor is None:
            extractor = Extractor()
            await extractor.setup()
        logger.info("automation-bench lifecycle: extracting agent artifacts from the DTU...")
        try:
            extraction = await extractor.run(
                dtu_id=dtu.id,
                task_context=task.scenario,
                extract_yaml_path=agent.extract_path,
                output_dir=extracted_dir,
            )
            result["extraction"] = {
                "status": "ok",
                "extracted_dir": str(extracted_dir),
                "manifest_entries": (
                    len(extraction.manifest.extracted) if extraction.manifest else 0
                ),
                "missing_items": (len(extraction.manifest.missing) if extraction.manifest else 0),
                "workspace_dir": extraction.workspace_dir,
                "session_dirs": extraction.session_dirs,
                "elapsed_s": round(extraction.elapsed_s, 3),
            }
        except Exception as exc:  # non-fatal: still capture world + grade + teardown
            logger.exception("extractor failed for %s", task.id)
            result["extraction"] = {"status": "failed", "error": str(exc)}

        # ---- CAPTURE the AutomationBench ground truth (fixed paths we own) -----
        # The final simulated world is THE record of what the agent did (assertions
        # are graded against it), and it lives at a hidden, harness-defined path.
        # Pull it deterministically rather than trusting the extractor's judgement
        # about hidden files. Also snapshot a DTU filesystem manifest so coverage is
        # auditable and future gaps are visible.
        try:
            await dtu.file_pull(WORLD_FILE, out_dir / "ab_world_final.json")
            result["world_final"] = str(out_dir / "ab_world_final.json")
        except Exception as exc:
            logger.exception("failed to pull final world for %s", task.id)
            result["world_final_error"] = str(exc)
        try:
            manifest_res = await _sh(
                dtu,
                "for d in /workspace /root/.local/share/opencode /root/.amplifier "
                "/root/.amplifier-agent /root/.config/opencode; do "
                'echo "== $d =="; ls -laR "$d" 2>/dev/null | head -n 500; done',
            )
            (out_dir / "dtu_manifest.txt").write_text(manifest_res.stdout, encoding="utf-8")
        except Exception:
            logger.exception("failed to capture DTU manifest for %s", task.id)

        # ---- METRICS (tokens / cost / wallclock from the extracted sessions) --
        try:
            metrics_record = build_metrics(agent.extract, extracted_dir, interaction.elapsed_s)
            (out_dir / "metrics.json").write_text(
                json.dumps(metrics_record, indent=2), encoding="utf-8"
            )
            result["metrics"] = metrics_record
        except Exception as exc:
            logger.exception("metrics build failed for %s", task.id)
            result["metrics_error"] = str(exc)

        # ---- GRADE (deterministic, factory-selected by grader.yaml `type:`) ---
        grader = make_grader(task.grader_path)
        if not isinstance(grader, AutomationBenchGrader):
            raise RuntimeError(
                f"expected an AutomationBenchGrader for {task.grader_path}, "
                f"got {type(grader).__name__}"
            )
        grader.grade_timeout_s = grade_timeout_s
        await grader.setup()
        logger.info(
            "automation-bench lifecycle: grading (`ab-tool grade` against the final world)..."
        )
        grader_result = await grader.run(
            grader_yaml_path=task.grader_path,
            task_context=task.scenario,
            dtu_id=dtu.id,
            output_dir=out_dir / "grade",
            grader_data_dir=grader_data_dir,
        )
        parsed = json.loads((out_dir / "grade" / "output.json").read_text(encoding="utf-8"))
        result["grade"] = parsed
        result["overall_score"] = grader_result.overall_score
        result["partial_credit"] = parsed.get("partial_credit")
        result["task_completed_correctly"] = parsed.get("task_completed_correctly")
        logger.info(
            "automation-bench lifecycle: partial_credit=%s task_completed_correctly=%s",
            parsed.get("partial_credit"),
            parsed.get("task_completed_correctly"),
        )
    finally:
        await dtu.destroy()

    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    return result


__all__ = ["run_automation_bench_instance"]
