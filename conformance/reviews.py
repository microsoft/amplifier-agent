"""Validate source-bound reviews without inferring historical approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def read_reviews(path: Path, root: Path, catalog: dict[str, dict]) -> list[dict]:
    if not path.exists():
        return []
    document = json.loads(path.read_text())
    if set(document) != {"reviews"} or not isinstance(document["reviews"], list):
        raise ValueError(f"{path}: supply a reviews list")
    outcomes = []
    seen = set()
    for review in document["reviews"]:
        expected = {"check", "surfaces", "reviewer", "conclusion", "rationale", "sources"}
        if set(review) != expected:
            raise ValueError(f"{path}: invalid review fields")
        check = review["check"]
        if check not in catalog or catalog[check]["kind"] != "review":
            raise ValueError(f"{path}: {check} is not a review check")
        if not review["surfaces"] or not set(review["surfaces"]) <= set(catalog[check]["surfaces"]):
            raise ValueError(f"{path}: {check} has unregistered surfaces")
        if review["conclusion"] not in {"pass", "fail"}:
            raise ValueError(f"{path}: use pass or fail for a reviewed conclusion")
        if not all(isinstance(review[field], str) and review[field].strip()
                   for field in ("reviewer", "rationale")):
            raise ValueError(f"{path}: name the reviewer and explain the reviewed evidence")
        if not isinstance(review["sources"], dict) or not review["sources"]:
            raise ValueError(f"{path}: {check} needs reviewed source hashes")
        stale = []
        for relative, digest in review["sources"].items():
            source = (root / relative).resolve()
            if not source.is_relative_to(root.resolve()) or not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"{path}: use repository-relative sources and SHA-256 hashes")
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                stale.append(relative)
        for surface in review["surfaces"]:
            if (check, surface) in seen:
                raise ValueError(f"{path}: duplicate review {check}/{surface}")
            seen.add((check, surface))
            outcomes.append({
                **review, "surface": surface,
                "status": "stale_review" if stale else "passed" if review["conclusion"] == "pass" else "failed_review",
                "changed_sources": stale,
            })
    return outcomes
