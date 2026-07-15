"""Graders package: the pluggable grading seam.

A task's `grader.yaml` carries a `type` discriminator; `make_grader` selects the
grader implementation for that type. New task types add a grader here without
touching the trial loop.

Public surface:

- `Grader`, `GraderResult`, `EvaluationResult`, `CriterionScore`: the shared
  protocol and result dataclasses (from `base`).
- `ModelRubricGrader`: the default LLM rubric grader.
- `make_grader(grader_yaml_path)`: factory that returns the right grader.
"""

from __future__ import annotations

from pathlib import Path

from eval.graders.base import (
    CriterionScore,
    EvaluationResult,
    Grader,
    GraderResult,
)
from eval.graders.automation_bench import GRADER_TYPE as AUTOMATION_BENCH_TYPE
from eval.graders.automation_bench import AutomationBenchGrader
from eval.graders.deterministic import GRADER_TYPE as DETERMINISTIC_TYPE
from eval.graders.deterministic import DeterministicGrader
from eval.graders.model_rubric import GRADER_TYPE as MODEL_RUBRIC_TYPE
from eval.graders.model_rubric import ModelRubricGrader
from eval.graders.schema import read_grader_type


def make_grader(grader_yaml_path: Path | str) -> Grader:
    """Return the grader implementation selected by grader.yaml's `type:`.

    Args:
        grader_yaml_path: Path to the task's grader.yaml. Its top-level `type:`
            field (default `model_rubric`) picks the implementation. `deterministic`
            selects the swe-bench test-pass/fail grader.

    Returns:
        A `Grader` instance. Call `setup()` once, then `run()` per trial.

    Raises:
        ValueError: If the grader.yaml declares an unknown `type`.
    """
    grader_type = read_grader_type(grader_yaml_path)
    if grader_type == MODEL_RUBRIC_TYPE:
        return ModelRubricGrader()
    if grader_type == DETERMINISTIC_TYPE:
        return DeterministicGrader()
    if grader_type == AUTOMATION_BENCH_TYPE:
        return AutomationBenchGrader()
    raise ValueError(
        f"{grader_yaml_path}: unknown grader type {grader_type!r} "
        f"(known types: {MODEL_RUBRIC_TYPE}, {DETERMINISTIC_TYPE}, {AUTOMATION_BENCH_TYPE})"
    )


__all__ = [
    "CriterionScore",
    "EvaluationResult",
    "Grader",
    "GraderResult",
    "ModelRubricGrader",
    "DeterministicGrader",
    "AutomationBenchGrader",
    "make_grader",
]
