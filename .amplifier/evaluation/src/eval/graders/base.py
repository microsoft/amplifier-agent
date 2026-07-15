"""Grader protocol and result types: the pluggable grading seam.

A task's `grader.yaml` carries a `type` discriminator. The harness selects a
grader implementation by that type via the
factory in `eval.graders.__init__`. Every implementation conforms to the
`Grader` protocol defined here and returns the same structured `GraderResult`,
so the trial loop never has to know which grader ran.

`model_rubric` is the default LLM rubric grader. `deterministic`
(test pass/fail) is the test-based grader. The result dataclasses live here, in the
base module, so both the protocol and every implementation share one definition
and there is no import cycle.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class CriterionScore:
    """One scored rubric entry: points awarded plus the grader's reasoning.

    `points_awarded` is an integer in [0, max_points] for the criterion.
    `reasoning` is one or two sentences citing what the grader observed. This is
    the atomic unit of a rubric grade and is common to every grader that scores
    against named criteria.
    """

    points_awarded: int
    reasoning: str


@dataclass
class EvaluationResult:
    """Outcome of one evaluation (one weighted rubric) within a grade.

    A `grader.yaml` may declare several weighted evaluations; each produces one
    of these. `score` is `points_awarded / points_possible` in [0, 1] (0.0 when
    nothing could be scored). `rubric_scores` maps each criterion name to its
    `CriterionScore`, or is None when the evaluation could not be scored at all.
    """

    name: str
    weight: float
    points_awarded: int
    points_possible: int
    score: float  # 0..1, points_awarded / points_possible (0.0 if unscored)
    rubric_scores: dict[str, CriterionScore] | None
    initial_report: str = ""
    validation_errors: list[str] = field(default_factory=list)
    submit_attempts: int = 0
    grader_session_id: str | None = None
    elapsed_s: float = 0.0


@dataclass
class GraderResult:
    """The structured verdict returned by any grader.

    `overall_score` is the weight-normalized average of the per-evaluation
    scores, in [0, 1]. `evaluations` carries the per-evaluation detail (points,
    per-criterion scores, reasoning). `grader_type` records which grader
    implementation produced the result.
    """

    grader_type: str
    grader_yaml_path: str
    dtu_id: str
    evaluations: list[EvaluationResult]
    overall_score: float  # weighted average, 0..1
    elapsed_s: float

    def to_json(self) -> str:
        """Serialize as an indented JSON string (dataclasses recursed)."""
        return json.dumps(asdict(self), indent=2, default=str)


@runtime_checkable
class Grader(Protocol):
    """The pluggable grading contract.

    Implementations compose whatever they need in `setup()` (expensive; call
    once) and produce a `GraderResult` from `run()`. The trial loop selects an
    implementation via the factory in `eval.graders`, calls `setup()` once, then
    `run()` per trial.
    """

    async def setup(self) -> None:
        """Prepare the grader (e.g. compose and prepare a Foundation bundle).

        Expensive; call once before the first `run()`.
        """
        ...

    async def run(
        self,
        grader_yaml_path: Path | str,
        task_context: str,
        dtu_id: str,
        output_dir: Path | str,
        grader_data_dir: Path | str | None = None,
    ) -> GraderResult:
        """Grade the work inside `dtu_id` against `grader_yaml_path`.

        Args:
            grader_yaml_path: Path to the task's grader.yaml.
            task_context: The original task instructions handed to the agent
                under test, as context for the grader.
            dtu_id: The Digital Twin Universe instance id to audit.
            output_dir: Host directory for per-evaluation reports and rubric
                JSON. Created if absent.
            grader_data_dir: Host directory that `mounts[].source` paths (if
                any) resolve against. Defaults to a sibling of grader.yaml.

        Returns:
            A structured `GraderResult`.
        """
        ...


__all__ = [
    "CriterionScore",
    "EvaluationResult",
    "GraderResult",
    "Grader",
]
