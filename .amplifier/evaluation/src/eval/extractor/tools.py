"""Tools the extractor session calls to submit its extraction manifest.

The extractor has two phases per run. Phase 1 is free-text exploration of the
DTU plus the actual file-pull bash calls. Phase 2 calls
`submit_extraction_manifest` with a structured record of everything that was
pulled. If validation fails, a follow-up message asks for fixes (up to 2
retries).

Copy-adapted for this harness from the reference library
(`amplifier_evaluation.extractor.tools`). The manifest schema, categories, and
validation are preserved verbatim: they do not depend on which agent produced
the files, so there is nothing agent-specific to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amplifier_core import ToolResult

CATEGORIES = ("session_data", "workspace", "other")


@dataclass
class ExtractedFile:
    """One file or directory the extractor claims it pulled."""

    source: str  # path inside the DTU
    destination: str  # absolute path on the host
    category: str  # session_data | workspace | other
    is_directory: bool
    note: str | None = None


@dataclass
class MissingItem:
    """One path the extractor expected (per extract.yaml) but could not pull."""

    path: str
    reason: str


@dataclass
class ExtractionManifest:
    """Captured by SubmitExtractionManifestTool when the extractor finishes."""

    extracted: list[ExtractedFile] = field(default_factory=list)
    missing: list[MissingItem] = field(default_factory=list)
    summary: str = ""


def build_manifest_input_schema() -> dict[str, Any]:
    """JSON schema for `submit_extraction_manifest` input.

    Fixed across all agents; the categories and shape don't depend on the
    agent. Strict on shape, open-ended on which paths land where.
    """
    return {
        "type": "object",
        "properties": {
            "extracted": {
                "type": "array",
                "description": (
                    "Every file or directory you successfully pulled out of "
                    "the DTU onto the host. Include the in-DTU source path "
                    "and the absolute host destination path."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Absolute path inside the DTU.",
                        },
                        "destination": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Absolute path on the host. Must be inside the run's output_dir."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": list(CATEGORIES),
                            "description": (
                                "session_data for session transcripts/metadata/events, "
                                "workspace for the agent's deliverables, "
                                "other for anything else worth keeping."
                            ),
                        },
                        "is_directory": {
                            "type": "boolean",
                            "description": (
                                "True if this entry is a directory (pulled with `file-pull -r`)."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "Optional. Use to explain unexpected finds (typically when category=other)."
                            ),
                        },
                    },
                    "required": ["source", "destination", "category", "is_directory"],
                    "additionalProperties": False,
                },
            },
            "missing": {
                "type": "array",
                "description": (
                    "Paths the extract.yaml hinted at but were not pullable. "
                    "Empty list if everything was found."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                    "required": ["path", "reason"],
                    "additionalProperties": False,
                },
            },
            "summary": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "One-paragraph summary of what was extracted and any noteworthy observations."
                ),
            },
        },
        "required": ["extracted", "missing", "summary"],
        "additionalProperties": False,
    }


class SubmitExtractionManifestTool:
    """Capture the extractor's structured manifest.

    Construct one instance per run. Reusable across retries (the latest
    submission replaces any previous one).
    """

    def __init__(self) -> None:
        self._schema = build_manifest_input_schema()
        self.last_submission: ExtractionManifest | None = None
        self.call_count = 0

    @property
    def name(self) -> str:
        return "submit_extraction_manifest"

    @property
    def description(self) -> str:
        return (
            "Submit the manifest of files and directories you pulled out of "
            "the Digital Twin Universe. Call this exactly once after all "
            "file-pulls are complete. If your submission has errors you "
            "will receive a follow-up message asking you to call this tool "
            "again with corrections."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        if not isinstance(input, dict):
            input = {}

        extracted_raw = input.get("extracted", [])
        extracted: list[ExtractedFile] = []
        if isinstance(extracted_raw, list):
            for item in extracted_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    extracted.append(
                        ExtractedFile(
                            source=str(item.get("source", "")),
                            destination=str(item.get("destination", "")),
                            category=str(item.get("category", "")),
                            is_directory=bool(item.get("is_directory", False)),
                            note=(str(item["note"]) if item.get("note") is not None else None),
                        )
                    )
                except (TypeError, ValueError):
                    continue

        missing_raw = input.get("missing", [])
        missing: list[MissingItem] = []
        if isinstance(missing_raw, list):
            for item in missing_raw:
                if not isinstance(item, dict):
                    continue
                missing.append(
                    MissingItem(
                        path=str(item.get("path", "")),
                        reason=str(item.get("reason", "")),
                    )
                )

        self.last_submission = ExtractionManifest(
            extracted=extracted,
            missing=missing,
            summary=str(input.get("summary", "")),
        )
        self.call_count += 1
        return ToolResult(
            success=True,
            output="Manifest received. Stand by for validation.",
        )


def validate_manifest(manifest: ExtractionManifest, output_dir: Path) -> list[str]:
    """Validate a manifest. Returns human-readable errors (empty if OK).

    Checks per `extracted` entry:
    - `destination` is an absolute path
    - `destination` is inside `output_dir` (after resolve())
    - `destination` exists on disk
    - `is_directory` matches what's on disk
    - `category` is one of the allowed values

    `missing` entries are informational; only basic non-emptiness is enforced.
    """
    errors: list[str] = []
    output_dir_resolved = output_dir.resolve()

    if not manifest.summary or not manifest.summary.strip():
        errors.append("summary must be a non-empty string")

    if not manifest.extracted:
        errors.append(
            "extracted must contain at least one entry "
            "(if nothing was pullable, explain that in `missing` and `summary`)"
        )

    for i, entry in enumerate(manifest.extracted):
        prefix = f"extracted[{i}] ({entry.source or '<no source>'})"

        if entry.category not in CATEGORIES:
            errors.append(f"{prefix}: category={entry.category!r} not in {list(CATEGORIES)}")

        if not entry.source or not entry.source.strip():
            errors.append(f"{prefix}: source must be a non-empty string")

        if not entry.destination or not entry.destination.strip():
            errors.append(f"{prefix}: destination must be a non-empty string")
            continue

        dest = Path(entry.destination)
        if not dest.is_absolute():
            errors.append(f"{prefix}: destination {entry.destination!r} must be absolute")
            continue

        try:
            dest_resolved = dest.resolve()
        except OSError as exc:
            errors.append(f"{prefix}: destination resolve failed: {exc}")
            continue

        try:
            dest_resolved.relative_to(output_dir_resolved)
        except ValueError:
            errors.append(
                f"{prefix}: destination {entry.destination!r} is not inside "
                f"output_dir {output_dir_resolved}"
            )
            continue

        if not dest_resolved.exists():
            errors.append(
                f"{prefix}: destination {entry.destination!r} does not exist "
                f"on disk (did the file-pull actually run?)"
            )
            continue

        actually_dir = dest_resolved.is_dir()
        if entry.is_directory and not actually_dir:
            errors.append(f"{prefix}: is_directory=true but {entry.destination!r} is a file")
        elif not entry.is_directory and actually_dir:
            errors.append(f"{prefix}: is_directory=false but {entry.destination!r} is a directory")

    for i, item in enumerate(manifest.missing):
        if not item.path or not item.path.strip():
            errors.append(f"missing[{i}]: path must be a non-empty string")
        if not item.reason or not item.reason.strip():
            errors.append(f"missing[{i}]: reason must be a non-empty string")

    return errors
