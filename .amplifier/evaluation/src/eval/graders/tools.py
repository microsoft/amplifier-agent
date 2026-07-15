"""Tools the model_rubric grader session calls to submit its rubric verdict.

The grader has two phases per evaluation. Phase 1 is free-text exploration
ending in an "initial report" as the assistant's final message. Phase 2 calls
`submit_rubric` with the scored rubric. If validation fails, a follow-up
message asks for fixes (up to 2 retries).

Copy-adapted for this harness from the reference library
(`amplifier_evaluation.grader.tools`). The per-evaluation JSON input schema
(exact criterion keys plus per-criterion max points) and the post-tool
validation are preserved. The one change: `CriterionScore` now lives in
`eval.graders.base` (shared with the result dataclasses), so it is imported
here rather than redefined.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amplifier_core import ToolResult

from eval.graders.base import CriterionScore
from eval.graders.schema import Evaluation


@dataclass
class RubricSubmission:
    """Captured by SubmitRubricTool when the grader calls submit_rubric."""

    scores: dict[str, CriterionScore]


def build_rubric_input_schema(evaluation: Evaluation) -> dict[str, Any]:
    """Build the JSON schema for `submit_rubric` input from an evaluation.

    The schema embeds the exact criterion keys and per-criterion max points so
    the provider enforces shape. Post-tool validation in
    `validate_rubric_submission` catches anything the provider lets through.
    """
    score_props: dict[str, Any] = {}
    for c in evaluation.rubric:
        score_props[c.name] = {
            "type": "object",
            "description": c.description,
            "properties": {
                "points_awarded": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": c.points,
                    "description": (f"Points awarded for this criterion (max {c.points})."),
                },
                "reasoning": {
                    "type": "string",
                    "minLength": 1,
                    "description": ("One or two sentences citing what you observed."),
                },
            },
            "required": ["points_awarded", "reasoning"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "object",
                "description": (
                    "Map of criterion name to its scored entry. Every "
                    "criterion in the rubric must be present."
                ),
                "properties": score_props,
                "required": [c.name for c in evaluation.rubric],
                "additionalProperties": False,
            }
        },
        "required": ["scores"],
    }


class SubmitRubricTool:
    """Capture the grader's structured rubric verdict.

    Construct one tool instance per evaluation; the rubric structure (criterion
    keys plus max points) is embedded in the input_schema so the provider
    enforces it.
    """

    def __init__(self, evaluation: Evaluation) -> None:
        self._evaluation = evaluation
        self._schema = build_rubric_input_schema(evaluation)
        self.last_submission: RubricSubmission | None = None
        self.call_count = 0

    @property
    def name(self) -> str:
        return "submit_rubric"

    @property
    def description(self) -> str:
        return (
            "Submit the scored rubric for this evaluation. Provide "
            "`points_awarded` (integer in [0, max]) and `reasoning` (one or "
            "two sentences citing what you observed) for every criterion key. "
            "Call this exactly once per evaluation. If your submission has "
            "errors, you will receive a follow-up message asking you to call "
            "this tool again with corrections."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        scores_raw = input.get("scores", {}) if isinstance(input, dict) else {}
        scores: dict[str, CriterionScore] = {}
        if isinstance(scores_raw, dict):
            for key, val in scores_raw.items():
                if not isinstance(val, dict):
                    continue
                try:
                    scores[str(key)] = CriterionScore(
                        points_awarded=int(val.get("points_awarded", 0)),
                        reasoning=str(val.get("reasoning", "")),
                    )
                except (TypeError, ValueError):
                    # Recorded as-is so validate_rubric_submission can flag it.
                    scores[str(key)] = CriterionScore(
                        points_awarded=-1,
                        reasoning=str(val.get("reasoning", "")),
                    )
        self.last_submission = RubricSubmission(scores=scores)
        self.call_count += 1
        return ToolResult(
            success=True,
            output="Rubric submission received. Stand by for validation.",
        )


def validate_rubric_submission(submission: RubricSubmission, evaluation: Evaluation) -> list[str]:
    """Validate a submission. Returns human-readable errors (empty if OK)."""
    errors: list[str] = []
    expected = {c.name: c for c in evaluation.rubric}

    missing = sorted(set(expected.keys()) - set(submission.scores.keys()))
    extra = sorted(set(submission.scores.keys()) - set(expected.keys()))
    if missing:
        errors.append(f"Missing criterion(s): {', '.join(missing)}")
    if extra:
        errors.append(f"Unknown criterion(s) (not in rubric): {', '.join(extra)}")

    for key, score in submission.scores.items():
        if key not in expected:
            continue
        max_pts = expected[key].points
        if not isinstance(score.points_awarded, int) or score.points_awarded < 0:
            errors.append(f"{key}: points_awarded must be an integer >= 0")
        elif score.points_awarded > max_pts:
            errors.append(f"{key}: points_awarded={score.points_awarded} exceeds max {max_pts}")
        if not score.reasoning or not score.reasoning.strip():
            errors.append(f"{key}: reasoning must be a non-empty string")

    return errors


__all__ = [
    "RubricSubmission",
    "SubmitRubricTool",
    "build_rubric_input_schema",
    "validate_rubric_submission",
]
