"""Load and validate a task's grader.yaml."""

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PASS_SCORE = 1.0


class RubricError(ValueError):
    pass


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load(path: Path) -> dict[str, Any]:
    """The grader.yaml at `path`, validated, with `weight` and `pass_score` defaults filled in."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RubricError(f"{path}: {error}") from error
    return normalize(raw, str(path))


def normalize(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RubricError(f"{where}: grader.yaml is a mapping with an evaluations list")
    errors: list[str] = []
    pass_score = raw.get("pass_score", DEFAULT_PASS_SCORE)
    if not _number(pass_score) or not 0 <= pass_score <= 1:
        errors.append(f"pass_score must be a number from 0 to 1, got {pass_score!r}")
    evaluations = raw.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        errors.append("evaluations must be a non-empty list")
        evaluations = []
    names: set[str] = set()
    normalized = []
    for i, evaluation in enumerate(evaluations):
        at = f"evaluations[{i}]"
        if not isinstance(evaluation, dict):
            errors.append(f"{at} must be a mapping")
            continue
        name = evaluation.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{at}.name must be a non-empty string")
        elif name in names:
            errors.append(f"{at}.name {name!r} is repeated")
        else:
            names.add(name)
        weight = evaluation.get("weight", 1)
        if not _number(weight) or weight <= 0:
            errors.append(f"{at}.weight must be a positive number")
        if not isinstance(evaluation.get("steps"), str) or not evaluation["steps"].strip():
            errors.append(f"{at}.steps must be a non-empty string")
        criteria = evaluation.get("rubric")
        if not isinstance(criteria, dict) or not criteria:
            errors.append(f"{at}.rubric must be a non-empty mapping of criteria")
            criteria = {}
        for key, spec in criteria.items():
            if not isinstance(spec, dict):
                errors.append(f"{at}.rubric.{key} must be a mapping with points and description")
                continue
            points = spec.get("points")
            if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
                errors.append(f"{at}.rubric.{key}.points must be a positive integer")
            if not isinstance(spec.get("description"), str) or not spec["description"].strip():
                errors.append(f"{at}.rubric.{key}.description must be a non-empty string")
        normalized.append(evaluation | {"weight": weight})
    if errors:
        raise RubricError(f"{where}: " + "; ".join(errors))
    return raw | {"pass_score": float(pass_score), "evaluations": normalized}
