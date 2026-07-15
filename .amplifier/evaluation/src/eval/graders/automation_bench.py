"""AutomationBench grader: score the final simulated world by deterministic assertions.

This is the `automation_bench` grader selected when a task's grader.yaml declares
`type: automation_bench` (the shared tasks/automation-bench/grader.yaml). Like the
deterministic swe-bench grader, it runs a scoring step INSIDE the DTU and returns a
fully deterministic verdict -- no model, no judgment.

After the agent has driven the simulated business environment via the `ab-tool`
CLI (mutating the world file at /workspace/.ab_world.json), this grader:

  1. pushes ab_tool.py + task_info.json into /grading,
  2. runs `ab-tool grade` against the final world file, which scores the world
     against the task's assertions and writes output.json,
  3. pulls output.json back and maps partial_credit (0..1) into the shared
     GraderResult (overall_score == partial_credit).

Runtime inputs come from the loader via `grader_data_dir` (written by
`eval.task_loaders.automation_bench`):

    grader_data_dir/ab_tool.py       # the tool-bridge CLI (grade subcommand)
    grader_data_dir/task_info.json   # the vendored task (assertions + initial_state)

Modeled on `eval.graders.deterministic.DeterministicGrader`.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from eval.dtu import DTU
from eval.graders.base import EvaluationResult, GraderResult

logger = logging.getLogger(__name__)

GRADER_TYPE = "automation_bench"

# In-DTU locations. Grading scratch is kept at /grading so pushed assets never
# pollute the workspace. The world file and venv match the loader's contract.
GRADING_DIR = "/grading"
WORLD_FILE = "/workspace/.ab_world.json"
AB_VENV = "/opt/ab-venv"

AB_TOOL_FILENAME = "ab_tool.py"
TASK_INFO_FILENAME = "task_info.json"
OUTPUT_FILENAME = "output.json"


class AutomationBenchGrader:
    """Run `ab-tool grade` inside a DTU and score it deterministically.

    Conforms to `eval.graders.base.Grader`. `setup()` is a no-op (there is no
    model to compose). `run()` reads its assets from `grader_data_dir` (written
    by the automation_bench loader).
    """

    grader_type = GRADER_TYPE

    def __init__(self, grade_timeout_s: float = 600.0) -> None:
        """Construct the grader.

        Args:
            grade_timeout_s: Wall-clock bound for the in-DTU `ab-tool grade` step.
        """
        self.grade_timeout_s = grade_timeout_s

    async def setup(self) -> None:
        """No-op: the automation_bench grader has no model/bundle to prepare."""
        return None

    async def run(
        self,
        grader_yaml_path: Path | str,
        task_context: str,
        dtu_id: str,
        output_dir: Path | str,
        grader_data_dir: Path | str | None = None,
    ) -> GraderResult:
        """Grade the final world in `dtu_id` against the task's assertions.

        Args:
            grader_yaml_path: Path to the task's grader.yaml (only its `type:` is
                relevant here; the real inputs come from `grader_data_dir`).
            task_context: The scenario handed to the agent (unused; kept for the
                protocol).
            dtu_id: The Digital Twin Universe instance id to grade in.
            output_dir: Host dir for the pulled output.json + grader_result.json.
            grader_data_dir: Host dir holding ab_tool.py + task_info.json. Required.
        """
        if grader_data_dir is None:
            raise ValueError(
                "AutomationBenchGrader.run requires grader_data_dir (the automation_bench "
                "loader writes ab_tool.py + task_info.json there)."
            )
        data_dir = Path(grader_data_dir).expanduser().resolve()
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        ab_tool = data_dir / AB_TOOL_FILENAME
        task_info = data_dir / TASK_INFO_FILENAME
        for required in (ab_tool, task_info):
            if not required.is_file():
                raise FileNotFoundError(
                    f"AutomationBenchGrader: required grading asset missing: {required}"
                )

        dtu = DTU(id=dtu_id, profile_path="")

        start = time.monotonic()

        # Push the tool-bridge + task data into the grading scratch dir.
        await dtu.exec_cmd(["bash", "-lc", f"mkdir -p {GRADING_DIR}"], timeout_s=60.0)
        await dtu.file_push(ab_tool, f"{GRADING_DIR}/{AB_TOOL_FILENAME}")
        await dtu.file_push(task_info, f"{GRADING_DIR}/{TASK_INFO_FILENAME}")

        # Score the final world against the task assertions (deterministic).
        grade_cmd = (
            f"{AB_VENV}/bin/python {GRADING_DIR}/{AB_TOOL_FILENAME} "
            f"--world {WORLD_FILE} grade "
            f"--info {GRADING_DIR}/{TASK_INFO_FILENAME} "
            f"--out {GRADING_DIR}/{OUTPUT_FILENAME}"
        )
        logger.info("automation_bench grader: running `ab-tool grade` in %s", dtu_id)
        run_res = await dtu.exec_cmd(
            ["bash", "-lc", grade_cmd],
            timeout_s=self.grade_timeout_s,
            stream_to_logfile=out / "grade.log",
        )
        logger.info(
            "automation_bench grader: grade finished (exit %s, %.0fs)",
            run_res.returncode,
            run_res.elapsed_s,
        )

        # Pull the parser output and score it. A missing output.json is a real
        # failure (grading never produced a verdict): fail loud.
        output_json = out / OUTPUT_FILENAME
        await dtu.file_pull(f"{GRADING_DIR}/{OUTPUT_FILENAME}", output_json)
        parsed = json.loads(output_json.read_text(encoding="utf-8"))
        partial_credit = float(parsed.get("partial_credit", 0.0))
        completed = float(parsed.get("task_completed_correctly", 0.0))

        elapsed = time.monotonic() - start
        evaluation = EvaluationResult(
            name="assertions",
            weight=1.0,
            points_awarded=partial_credit,
            points_possible=1.0,
            score=partial_credit,
            rubric_scores=None,
            initial_report=json.dumps(
                {
                    "task_completed_correctly": completed,
                    "assertion_results": parsed.get("assertion_results"),
                }
            ),
            elapsed_s=elapsed,
        )
        result = GraderResult(
            grader_type=self.grader_type,
            grader_yaml_path=str(grader_yaml_path),
            dtu_id=dtu_id,
            evaluations=[evaluation],
            overall_score=partial_credit,
            elapsed_s=elapsed,
        )
        (out / "grader_result.json").write_text(result.to_json(), encoding="utf-8")
        return result


__all__ = [
    "GRADER_TYPE",
    "AutomationBenchGrader",
    "GRADING_DIR",
    "WORLD_FILE",
    "AB_VENV",
]
