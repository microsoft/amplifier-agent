"""Extractor package: an agent-driven Foundation session that pulls artifacts from a DTU.

Public surface:

- `Extractor`: composes foundation + anthropic-sonnet + the fixed extractor
  system instruction, then runs a three-phase session that explores a DTU,
  file-pulls the agent-under-test's session logs and deliverables to a host
  output dir, and emits a `manifest.json`.
- `ExtractionResult`: the typed outcome of one `Extractor.run(...)`.
- `ExtractionManifest` / `ExtractedFile` / `MissingItem`: the manifest shapes.

Metrics normalization is intentionally NOT part of this package. Extraction is
agent-driven (fuzzy, judgment-based); metrics parsing is a separate light-code
pass (`eval.metrics`) over the events.jsonl this extractor pulls. Keeping them
apart follows the plan: extraction does the fuzzy part, metrics is light code
over already-extracted, normalized files.
"""

from __future__ import annotations

from eval.extractor.extractor import (
    DEFAULT_FOUNDATION_SOURCE,
    DEFAULT_PROVIDER_SOURCE,
    MAX_RETRIES,
    ExtractionResult,
    Extractor,
)
from eval.extractor.tools import (
    ExtractedFile,
    ExtractionManifest,
    MissingItem,
    SubmitExtractionManifestTool,
    validate_manifest,
)

__all__ = [
    "DEFAULT_FOUNDATION_SOURCE",
    "DEFAULT_PROVIDER_SOURCE",
    "MAX_RETRIES",
    "Extractor",
    "ExtractionResult",
    "ExtractionManifest",
    "ExtractedFile",
    "MissingItem",
    "SubmitExtractionManifestTool",
    "validate_manifest",
]
