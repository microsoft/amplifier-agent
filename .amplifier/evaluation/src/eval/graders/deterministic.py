"""Deterministic grader: score a candidate patch by official SWE-bench Pro tests.

This is the `deterministic` grader selected when a task's grader.yaml declares
`type: deterministic`. Unlike the model_rubric
grader (an LLM auditing a rubric), this grader runs the official test entry
script INSIDE the DTU and compares the parsed per-test statuses against the
instance's fail_to_pass / pass_to_pass lists. The verdict is fully deterministic:
no model, no judgment.

Faithful to the official SWE-bench Pro entry script (`create_entryscript` in the
upstream `swe_bench_pro_eval.py`): reset the repo to base_commit, apply the
candidate patch, check out the held-out reference test files, run the official
`run_script.sh`, parse with the official `parser.py`, then score. Copy-adapted
from the proven prior-art `swe_bench_pro/grading.py`; the pure entry-script
builder and scorer live here (the grader owns the trust-critical verdict), while
the fetch/convert helpers live in `eval.task_loaders.swe_bench_support`.

Runtime inputs come from the loader via `grader_data_dir` (the contract written
by `eval.task_loaders.swe_bench`):

    grader_data_dir/deterministic_config.json  # base_commit, test lists, exports, ...
    grader_data_dir/run_script.sh              # official test runner
    grader_data_dir/parser.py                  # official stdout/err -> JSON parser
    grader_data_dir/patch.diff                 # candidate patch (gold or agent)

The grader pushes the assets into the DTU, runs the entry script, pulls
`output.json`, scores, and returns the harness's shared `base.GraderResult`
(overall_score 1.0 iff resolved, else 0.0) plus a full `grade.json` in
`output_dir`.
"""

from __future__ import annotations

import json
import logging
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.dtu import DTU
from eval.graders.base import EvaluationResult, GraderResult

logger = logging.getLogger(__name__)

GRADER_TYPE = "deterministic"

# In-DTU locations. The repo is at /app; grading scratch is kept separate at
# /grading so the pushed assets never pollute the repo's git diff.
REPO_DIR = "/app"
GRADING_DIR = "/grading"

CONFIG_FILENAME = "deterministic_config.json"
RUN_SCRIPT_FILENAME = "run_script.sh"
PARSER_FILENAME = "parser.py"
PATCH_FILENAME = "patch.diff"


# ---------------------------------------------------------------------------
# Pure grading logic (stdlib only): build the entry script + score parsed tests.
# Faithful to the official entry script; unit-testable without a container.
# ---------------------------------------------------------------------------


def last_setup_line(before_repo_set_cmd: str) -> str:
    """Final line of before_repo_set_cmd: checks out the held-out test files."""
    lines = [ln for ln in (before_repo_set_cmd or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def build_entry_script(
    *,
    base_commit: str,
    before_repo_set_cmd: str,
    selected_test_files: list[str],
    exports: list[str],
    apply_patch: bool = True,
    repo_dir: str = REPO_DIR,
    grading_dir: str = GRADING_DIR,
) -> str:
    """Build the grade-time shell script, faithful to the official entry script.

    Resets to base_commit, applies the candidate patch, checks out the held-out
    reference test files, runs the official run_script.sh over the selected test
    files, and parses the output to JSON. When ``apply_patch`` is False the repo
    is left at the buggy baseline (useful for validating the target fail_to_pass
    test actually fails without a fix).
    """
    patch_path = f"{grading_dir}/{PATCH_FILENAME}"
    run_script_path = f"{grading_dir}/{RUN_SCRIPT_FILENAME}"
    parser_path = f"{grading_dir}/{PARSER_FILENAME}"
    stdout_path = f"{grading_dir}/stdout.log"
    stderr_path = f"{grading_dir}/stderr.log"
    output_path = f"{grading_dir}/output.json"
    done_path = f"{grading_dir}/grade_done.txt"

    selected = ",".join(selected_test_files)
    lines: list[str] = ["set -x", *exports, f"cd {repo_dir}"]
    lines.append(f"git reset --hard {shlex.quote(base_commit)}")
    lines.append(f"git checkout {shlex.quote(base_commit)}")
    if apply_patch:
        lines.append(f"git apply -v {patch_path}")
    held_out = last_setup_line(before_repo_set_cmd)
    if held_out:
        lines.append(held_out)
    lines.append(f"bash {run_script_path} {shlex.quote(selected)} > {stdout_path} 2> {stderr_path}")
    lines.append(f"python {parser_path} {stdout_path} {stderr_path} {output_path}")
    lines.append(f"echo PARSER_EXIT=$? > {done_path}")
    return "\n".join(lines)


@dataclass
class GradeResult:
    """The deterministic verdict computed from parser.py output."""

    resolved: bool
    fail_to_pass_total: int
    fail_to_pass_passed: int
    pass_to_pass_total: int
    pass_to_pass_passed: int
    missing_fail_to_pass: list[str] = field(default_factory=list)
    missing_pass_to_pass: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "fail_to_pass": {
                "total": self.fail_to_pass_total,
                "passed": self.fail_to_pass_passed,
                "missing": self.missing_fail_to_pass,
            },
            "pass_to_pass": {
                "total": self.pass_to_pass_total,
                "passed": self.pass_to_pass_passed,
                "missing": self.missing_pass_to_pass,
            },
        }


def score_parsed(
    parser_output: dict[str, Any],
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> GradeResult:
    """Compute the resolved verdict from parser.py output.

    An instance is resolved iff every fail_to_pass test is PASSED and every
    pass_to_pass test is still PASSED (the official SWE-bench Pro criterion).
    """
    status = {t.get("name"): t.get("status") for t in parser_output.get("tests", [])}
    missing_f = [n for n in fail_to_pass if status.get(n) != "PASSED"]
    missing_p = [n for n in pass_to_pass if status.get(n) != "PASSED"]
    return GradeResult(
        resolved=(not missing_f and not missing_p),
        fail_to_pass_total=len(fail_to_pass),
        fail_to_pass_passed=len(fail_to_pass) - len(missing_f),
        pass_to_pass_total=len(pass_to_pass),
        pass_to_pass_passed=len(pass_to_pass) - len(missing_p),
        missing_fail_to_pass=missing_f,
        missing_pass_to_pass=missing_p,
    )


# ---------------------------------------------------------------------------
# The grader: push assets into the DTU, run the entry script, pull + score.
# ---------------------------------------------------------------------------


class DeterministicGrader:
    """Run the official test entry script in a DTU and score it deterministically.

    Conforms to `eval.graders.base.Grader`. `setup()` is a no-op (there is no
    model to compose). `run()` reads its runtime config + assets from
    `grader_data_dir` (written by the swe_bench loader; the candidate patch is
    written there by the lifecycle before grading).
    """

    grader_type = GRADER_TYPE

    def __init__(self, grade_timeout_s: float = 3600.0) -> None:
        """Construct the grader.

        Args:
            grade_timeout_s: Wall-clock bound for running the in-DTU entry script
                (the whole reset -> apply -> test -> parse sequence).
        """
        self.grade_timeout_s = grade_timeout_s

    async def setup(self) -> None:
        """No-op: the deterministic grader has no model/bundle to prepare."""
        return None

    async def run(
        self,
        grader_yaml_path: Path | str,
        task_context: str,
        dtu_id: str,
        output_dir: Path | str,
        grader_data_dir: Path | str | None = None,
    ) -> GraderResult:
        """Grade the candidate patch in `dtu_id` against the official tests.

        Args:
            grader_yaml_path: Path to the task's grader.yaml (only its `type:` is
                relevant here; the real inputs come from `grader_data_dir`).
            task_context: The issue handed to the agent (unused; kept for the
                protocol).
            dtu_id: The Digital Twin Universe instance id to grade in.
            output_dir: Host dir for the pulled output.json + grade.json.
            grader_data_dir: Host dir holding deterministic_config.json,
                run_script.sh, parser.py, and patch.diff. Required.
        """
        if grader_data_dir is None:
            raise ValueError(
                "DeterministicGrader.run requires grader_data_dir (the swe_bench "
                "loader writes deterministic_config.json + assets there)."
            )
        data_dir = Path(grader_data_dir).expanduser().resolve()
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        config = json.loads((data_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
        run_script = data_dir / RUN_SCRIPT_FILENAME
        parser = data_dir / PARSER_FILENAME
        patch = data_dir / PATCH_FILENAME
        for required in (run_script, parser, patch):
            if not required.is_file():
                raise FileNotFoundError(
                    f"DeterministicGrader: required grading asset missing: {required}"
                )

        repo_dir = str(config.get("repo_dir", REPO_DIR))
        grading_dir = str(config.get("grading_dir", GRADING_DIR))
        fail_to_pass = list(config.get("fail_to_pass", []))
        pass_to_pass = list(config.get("pass_to_pass", []))

        dtu = DTU(id=dtu_id, profile_path="")

        start = time.monotonic()

        # Push the official assets + candidate patch into the grading scratch dir.
        await dtu.exec_cmd(["bash", "-lc", f"mkdir -p {grading_dir}"], timeout_s=60.0)
        await dtu.file_push(run_script, f"{grading_dir}/{RUN_SCRIPT_FILENAME}")
        await dtu.file_push(parser, f"{grading_dir}/{PARSER_FILENAME}")
        await dtu.file_push(patch, f"{grading_dir}/{PATCH_FILENAME}")

        # Build + push the entry script, then run it (faithful to the official one).
        entry = build_entry_script(
            base_commit=str(config["base_commit"]),
            before_repo_set_cmd=str(config.get("before_repo_set_cmd", "")),
            selected_test_files=list(config.get("selected_test_files", [])),
            exports=list(config.get("exports", [])),
            apply_patch=True,
            repo_dir=repo_dir,
            grading_dir=grading_dir,
        )
        entry_path = out / "entry_script.sh"
        entry_path.write_text(entry, encoding="utf-8")
        await dtu.file_push(entry_path, f"{grading_dir}/entry_script.sh")

        logger.info(
            "deterministic grader: running entry script in %s (reset -> apply -> test -> parse)",
            dtu_id,
        )
        run_res = await dtu.exec_cmd(
            ["bash", "-lc", f"bash {grading_dir}/entry_script.sh"],
            timeout_s=self.grade_timeout_s,
            stream_to_logfile=out / "grade.log",
        )
        logger.info(
            "deterministic grader: entry script finished (exit %s, %.0fs)",
            run_res.returncode,
            run_res.elapsed_s,
        )

        # Pull the parser output and score it. A missing output.json is a real
        # failure (the tests never produced parseable results): fail loud.
        output_json = out / "output.json"
        await dtu.file_pull(f"{grading_dir}/output.json", output_json)
        parser_output = json.loads(output_json.read_text(encoding="utf-8"))
        grade = score_parsed(parser_output, fail_to_pass, pass_to_pass)

        (out / "grade.json").write_text(json.dumps(grade.to_dict(), indent=2), encoding="utf-8")

        elapsed = time.monotonic() - start
        result = self._to_grader_result(
            grade=grade,
            grader_yaml_path=str(grader_yaml_path),
            dtu_id=dtu_id,
            elapsed_s=elapsed,
        )
        (out / "grader_result.json").write_text(result.to_json(), encoding="utf-8")
        return result

    def _to_grader_result(
        self,
        *,
        grade: GradeResult,
        grader_yaml_path: str,
        dtu_id: str,
        elapsed_s: float,
    ) -> GraderResult:
        """Map the binary SWE-bench verdict into the shared GraderResult shape.

        `overall_score` is 1.0 iff the instance is resolved (all fail_to_pass
        flipped to PASS and all pass_to_pass stayed PASS), else 0.0 -- the
        official criterion. The two evaluations carry the fractional detail
        (how many of each category passed) for transparency; they do NOT drive
        `overall_score`, which is deliberately binary.
        """

        def _eval(name: str, passed: int, total: int) -> EvaluationResult:
            frac = (passed / total) if total > 0 else 1.0
            return EvaluationResult(
                name=name,
                weight=1.0,
                points_awarded=passed,
                points_possible=total,
                score=frac,
                rubric_scores=None,
            )

        evaluations = [
            _eval("fail_to_pass", grade.fail_to_pass_passed, grade.fail_to_pass_total),
            _eval("pass_to_pass", grade.pass_to_pass_passed, grade.pass_to_pass_total),
        ]
        return GraderResult(
            grader_type=self.grader_type,
            grader_yaml_path=grader_yaml_path,
            dtu_id=dtu_id,
            evaluations=evaluations,
            overall_score=1.0 if grade.resolved else 0.0,
            elapsed_s=elapsed_s,
        )


__all__ = [
    "GRADER_TYPE",
    "DeterministicGrader",
    "GradeResult",
    "build_entry_script",
    "score_parsed",
    "last_setup_line",
    "PATCH_FILENAME",
    "CONFIG_FILENAME",
    "RUN_SCRIPT_FILENAME",
    "PARSER_FILENAME",
]
