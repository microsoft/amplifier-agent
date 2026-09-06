"""Require passing, discriminating evidence for every check and declared surface."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def read_requirements(directory: Path, catalog: dict[str, dict]) -> list[dict]:
    group_path = directory / "groups.json"
    document = json.loads(group_path.read_text())
    if set(document) != {"groups"} or not isinstance(document["groups"], dict):
        raise ValueError(f"{group_path}: supply named case groups")
    groups = document["groups"]
    for name, selector in groups.items():
        if not isinstance(name, str) or not name or not isinstance(selector, dict):
            raise ValueError(f"{group_path}: invalid case group")
        if not {"suite", "pattern"} <= set(selector) <= {"suite", "pattern", "count"}:
            raise ValueError(f"{group_path}: invalid selector for {name}")
        if not all(isinstance(selector[key], str) and selector[key] for key in ("suite", "pattern")):
            raise ValueError(f"{group_path}: {name} requires a suite and pattern")
        count = selector.get("count", 1)
        if type(count) is not int or count < 1 or ("*" in selector["pattern"] and "count" not in selector):
            raise ValueError(f"{group_path}: {name} needs an explicit positive variant count")
    requirements: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(directory.glob("*.json")):
        if path == group_path:
            continue
        document = json.loads(path.read_text())
        if set(document) != {"requirements"} or not isinstance(document["requirements"], list):
            raise ValueError(f"{path}: supply a requirements list")
        for requirement in document["requirements"]:
            if set(requirement) != {"check", "surface", "cases", "discrimination"}:
                raise ValueError(f"{path}: invalid evidence requirement fields")
            check, surface = requirement["check"], requirement["surface"]
            if check not in catalog or surface not in catalog[check]["surfaces"]:
                raise ValueError(f"{path}: unknown check/surface {check}/{surface}")
            if catalog[check]["kind"] == "review":
                raise ValueError(f"{path}: {check} requires a source review, not test registration")
            if (check, surface) in seen:
                raise ValueError(f"{path}: duplicate requirement {check}/{surface}")
            seen.add((check, surface))
            if not isinstance(requirement["discrimination"], str) or not requirement["discrimination"].strip():
                raise ValueError(f"{path}: describe the rejected violation for {check}/{surface}")
            names = requirement["cases"]
            if not isinstance(names, list) or not names:
                raise ValueError(f"{path}: {check}/{surface} needs required cases")
            if any(not isinstance(name, str) or name not in groups for name in names):
                raise ValueError(f"{path}: {check}/{surface} references an unknown case group")
            if len(names) != len(set(names)):
                raise ValueError(f"{path}: {check}/{surface} repeats a case group")
            requirements.append({**requirement, "cases": [dict(groups[name]) for name in names]})
    return requirements


def matches(pattern: str, case: str) -> bool:
    """Only '*' is a wildcard; parameter brackets and other punctuation are literal."""
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), case) is not None


def assess(
    catalog: dict[str, dict],
    requirements: list[dict],
    observations: list[dict],
    reviews: list[dict] | None = None,
) -> dict[str, Any]:
    registered = {(item["check"], item["surface"]): item for item in requirements}
    reviewed = {(item["check"], item["surface"]): item for item in reviews or []}
    coverage = []
    for identifier, check in sorted(catalog.items()):
        for surface in check["surfaces"]:
            key = (identifier, surface)
            row: dict[str, Any] = {"check": identifier, "surface": surface, "kind": check["kind"]}
            if check["kind"] == "review":
                review = reviewed.get(key)
                row.update(status=review["status"] if review else "missing_review", evidence=review or {})
            elif key not in registered:
                row.update(status="missing_registration", cases=[])
            else:
                requirement = registered[key]
                selectors = []
                for selector in requirement["cases"]:
                    cases = [item for item in observations if item["suite"] == selector["suite"]
                             and matches(selector["pattern"], item["case"])]
                    expected = selector.get("count", 1)
                    unique = {(item["suite"], item["case"]) for item in cases}
                    status = "passed"
                    if len(cases) != expected or len(unique) != len(cases):
                        status = "missing_cases" if len(cases) < expected else "unexpected_cases"
                    elif any(item["status"] != "passed" for item in cases):
                        status = "unpassed_cases"
                    selectors.append({**selector, "expected": expected, "status": status, "observed": cases})
                row.update(
                    status="passed" if all(item["status"] == "passed" for item in selectors) else "incomplete",
                    cases=selectors, discrimination=requirement["discrimination"],
                )
            coverage.append(row)
    covered = [identifier for identifier in sorted(catalog)
               if all(row["status"] == "passed" for row in coverage if row["check"] == identifier)]
    return {"coverage": coverage, "covered_checks": covered}


def obligations(directory: Path, covered: list[str]) -> tuple[list[dict], list[dict]]:
    satisfied, uncovered = [], []
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text())
        for obligation in document.get("obligations", []):
            missing = sorted(set(obligation["checks"]) - set(covered))
            row = {"contract": document["contract"], "obligation": obligation["id"],
                   "checks": missing or obligation["checks"]}
            (uncovered if missing else satisfied).append(row)
    return satisfied, uncovered
