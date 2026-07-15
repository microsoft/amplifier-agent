"""Extractor: an Amplifier Foundation session that pulls an agent's artifacts out of a DTU.

- SYSTEM_INSTRUCTION (fixed): how to use `bash` for two host-side commands:
  `amplifier-digital-twin exec` to explore inside the DTU, and
  `amplifier-digital-twin file-pull` to copy files out onto the host.
- extract.yaml (per-run): rendered as a hint, not a strict spec.
- output_dir (per-run): host directory where everything should land.

For each run the Extractor runs one Foundation session with three phases on a
single multi-turn session:

1. Explore the DTU, then file-pull each artifact out and write a free-text
   extraction report.
2. Submit the structured manifest via the `submit_extraction_manifest` tool.
3. If validation fails, ask for fixes (max 2 retries).

The Extractor does NOT pre-parse extract.yaml into a typed schema. The whole
point of going agentic is that the LLM can adapt to drift between the hints and
what is actually inside the DTU (per the eval philosophy: extraction is
agent-driven, not brittle code).

Copy-adapted for this harness from the reference library
(`amplifier_evaluation.extractor.extractor`). Two adaptations: the per-agent
hints file is this harness's `extract.yaml` (the library called it `data.yaml`),
and the `run(...)` parameter is named `extract_yaml_path` to match. The
Foundation composition, the three-phase session, and the manifest emission are
preserved. Metrics normalization is deliberately NOT done here: it is a separate
light-code pass (`eval.metrics`) over the events.jsonl this extractor pulls.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from amplifier_foundation import Bundle, load_bundle

from eval.extractor.tools import (
    ExtractedFile,
    ExtractionManifest,
    MissingItem,
    SubmitExtractionManifestTool,
    validate_manifest,
)

DEFAULT_FOUNDATION_SOURCE = "git+https://github.com/microsoft/amplifier-foundation@main"
# Local Sonnet provider bundle mirroring foundation's anthropic-sonnet.yaml but
# with a hardened retry budget (max_retries/overloaded_delay_multiplier/
# max_retry_delay), so a transient Anthropic "Overloaded" (529) during extraction
# retries instead of failing the phase. The foundation yaml is git-fetched and
# cannot be edited in place, hence the local bundle.
_HARNESS_ROOT = Path(__file__).resolve().parents[3]  # .../evaluation
DEFAULT_PROVIDER_SOURCE = str(_HARNESS_ROOT / "providers" / "anthropic-sonnet-retry.yaml")

MAX_RETRIES = 2


SYSTEM_INSTRUCTION = """\
You are an extractor agent. Your goal is to pull an agent's work out of a
Digital Twin Universe container onto the host so it can be analyzed later.

"The agent's work" has two parts you MUST extract:

1. The agent's DELIVERABLES for the task it was given. Wherever the agent
   actually wrote them. Code, data files, output artifacts, generated
   reports - whatever the task description implies the agent should have
   produced. Categorize these as `workspace`.

2. The agent's SESSION LOGS produced by the agent framework. Typically
   transcripts, events, and metadata files. Categorize these as
   `session_data`. These are the authoritative token/cost/timing source, so
   pull ALL of them.

The extract.yaml you receive describes where BOTH the session logs AND the
deliverables live, and often names the exact deliverable files. Treat it
as usually-correct guidance, not as unreliable. Work HINT-FIRST:

1. Try the paths and files extract.yaml names. Verify they exist with ONE
   combined command (a single `find` / `ls`), not many small probes.
2. Only if a hinted path is genuinely missing do you walk the directory
   tree to locate it. Record anything that stays missing in `missing`.

The hints are usually right, so aim to finish in as few tool calls as
possible. Use the original task description to confirm what the
deliverables should be.

You have a `bash` tool. You will use two host-side commands:

  - To inspect inside the DTU:

        amplifier-digital-twin exec <dtu_id> -- <command>

  - To copy files out of the DTU onto the host:

        amplifier-digital-twin file-pull <dtu_id> <inside_path> <host_path>
        amplifier-digital-twin file-pull -r <dtu_id> <inside_dir> <host_dir>

RULES:

- Place all host files under the provided output_dir, with sensible
  subdirectories. Suggested layout:
      output_dir/sessions/<session_id>/         (session logs)
      output_dir/workspace/                     (agent's deliverables)
      output_dir/other/<descriptive_name>/      (auxiliary state)
- Use `file-pull -r` for directories. Use plain `file-pull` for single files.
- Do NOT use `exec + cat > local_file`. Use `file-pull`; it handles binary
  files, permissions, and large files correctly.
- Never modify anything inside the DTU. You may read freely.
- HINT-FIRST: pull exactly the files extract.yaml names. Only if a hinted
  path is missing do you search (`find`, not recursive `ls`) common
  locations like /root, /workspace, /tmp, /home.
- The session logs are often several files under a nested directory (for
  example each session's `context-intelligence/events.jsonl`). When the hint
  gives a base_path plus a glob, pull the WHOLE base directory recursively
  (`file-pull -r`) so every matching events.jsonl comes across, then the
  metrics pass can sum them. Preserve the directory structure under
  output_dir/sessions/ so multiple sessions do not collide.
- SIZE GUARD: before any `file-pull -r`, check the directory size with
  `du -sh <dir>`. Never recursively pull a directory that contains a cloned
  source repository, a virtualenv, or node_modules, or that exceeds ~50 MB.
  Pull the specific files instead.
- If a hinted path in extract.yaml genuinely does not exist after you've
  walked the tree, record it in the `missing` list and move on.
- Be efficient. Do not pull the same file twice.
- After you have pulled everything, call `submit_extraction_manifest`
  exactly once and stop.
"""


PHASE1_PROMPT_TEMPLATE = """\
The agent was asked to do the following:

\"\"\"
{task_context}
\"\"\"

Your goal: pull the agent's work out of Digital Twin Universe `{dtu_id}`
onto the host. The host output directory is:

    {output_dir}

All host paths in your manifest must be absolute and live under that directory.

Here is the agent's extract.yaml. It describes where BOTH the deliverables
and the session logs live, and often names the exact deliverable files
and any directories you must NOT pull:

```yaml
{extract_yaml_text}
```

You want to extract two things, working HINT-FIRST from the extract.yaml:

1. The agent's DELIVERABLES for the task above. The `workspace:` block in
   extract.yaml names where they live. Use the task description to confirm
   what to pull (for this task, the agent writes an answer file plus any
   working files under the workspace path). Categorize these as `workspace`.

2. The agent's SESSION LOGS. The `session_data:` block names where they
   live, usually as a base_path plus a glob. Pull ALL matching files; they
   hold the token usage and cost. For these amplifier-agent sessions the logs
   are each session's `context-intelligence/events.jsonl` under the base_path
   -- pull the base directory recursively so every session's events come
   across. Categorize these as `session_data`.

Only walk the directory tree if a hinted path is actually missing. The
hints are usually correct; aim to finish in as few tool calls as possible.

Steps:

1. Verify the hinted paths exist with ONE combined command (a single
   `find` / `ls`), not many small probes. Before any `file-pull -r`, run
   `du -sh` and never recursively pull a cloned repo, virtualenv, or any
   directory over ~50 MB.
2. Pull every artifact with `amplifier-digital-twin file-pull` (use `-r`
   for directories), placing them under sensible subdirectories of
   `output_dir` (session logs under `sessions/`, deliverables under
   `workspace/`).
3. If you spot related state nearby (configs the agent wrote, agent logs
   outside the session dir, etc.) pull them under category `other` with a
   `note` explaining what they are.
4. If extract.yaml mentioned paths that genuinely do not exist after you
   walked the tree, list them under `missing`.

After all file-pulls are complete, write a free-text "extraction report"
as your final assistant message describing what you found, what you pulled,
and anything missing or anomalous.

Do NOT call `submit_extraction_manifest` yet. Your final assistant message
in this turn is the extraction report.
"""


PHASE2_PROMPT = """\
Now call the `submit_extraction_manifest` tool exactly once with the manifest
of everything you pulled.

For each entry, provide:

- source: the absolute path inside the DTU
- destination: the absolute host path you pulled it to (must be inside
  output_dir)
- category: one of "session_data", "workspace", or "other"
- is_directory: true if you used `file-pull -r`, false for single files
- note: optional, use for `other` entries

Use the observations from your extraction report. Do not run more bash
commands unless absolutely necessary. After submit_extraction_manifest, do
not call any other tools.
"""


PHASE3_RETRY_TEMPLATE = """\
Your submit_extraction_manifest call had these problems:

{errors}

Call submit_extraction_manifest again with corrections. If a destination
does not exist on disk, either fix the path or run the missing file-pull
first. Only change the entries that were flagged; leave correct entries
as they were.
"""


@dataclass
class ExtractionResult:
    """Outcome of one extraction run."""

    dtu_id: str
    extract_yaml_path: str
    output_dir: str
    extraction_report: str  # free-text from phase 1
    manifest: ExtractionManifest | None
    validation_errors: list[str] = field(default_factory=list)
    submit_manifest_attempts: int = 0
    extractor_session_id: str | None = None
    elapsed_s: float = 0.0

    @property
    def session_dirs(self) -> list[str]:
        """Unique parent directories of all `session_data` entries."""
        if self.manifest is None:
            return []
        seen: dict[str, None] = {}
        for entry in self.manifest.extracted:
            if entry.category != "session_data":
                continue
            dest = Path(entry.destination)
            parent = str(dest if entry.is_directory else dest.parent)
            seen.setdefault(parent, None)
        return list(seen.keys())

    @property
    def workspace_paths(self) -> list[str]:
        """All host paths categorized as `workspace`, in insertion order."""
        if self.manifest is None:
            return []
        return [e.destination for e in self.manifest.extracted if e.category == "workspace"]

    @property
    def workspace_dir(self) -> str | None:
        """Host directory containing the extracted workspace."""
        paths = self.workspace_paths
        if not paths:
            return None
        if len(paths) == 1 and self.manifest is not None:
            entry = next(e for e in self.manifest.extracted if e.destination == paths[0])
            if entry.is_directory:
                return entry.destination
        try:
            return os.path.commonpath(paths)
        except ValueError:
            return None

    def to_json(self) -> str:
        """Serialize as a JSON string (dataclasses recursed via asdict)."""
        return json.dumps(asdict(self), indent=2)


class Extractor:
    """Compose Foundation + provider + extractor system instruction, then pull."""

    def __init__(
        self,
        foundation_source: str = DEFAULT_FOUNDATION_SOURCE,
        provider_source: str = DEFAULT_PROVIDER_SOURCE,
    ) -> None:
        """Construct an Extractor.

        Args:
            foundation_source: Source for the foundation bundle. Defaults to
                the canonical git URL so no local checkout is required.
                Accepts any string `load_bundle` understands.
            provider_source: Source for the provider bundle YAML. Defaults
                to the canonical foundation `anthropic-sonnet.yaml`.
        """
        self.foundation_source = foundation_source
        self.provider_source = provider_source
        self._prepared = None

    async def setup(self) -> None:
        """Load + compose + prepare the bundle. Expensive; call once."""
        foundation = await load_bundle(self.foundation_source)
        provider = await load_bundle(self.provider_source)
        system_bundle = Bundle(
            name="extractor-system",
            version="0.1.0",
            instruction=SYSTEM_INSTRUCTION,
        )
        composed = foundation.compose(provider).compose(system_bundle)
        self._prepared = await composed.prepare()

    async def run(
        self,
        dtu_id: str,
        task_context: str,
        extract_yaml_path: Path | str,
        output_dir: Path | str,
    ) -> ExtractionResult:
        """Extract the agent's work from a DTU.

        Args:
            dtu_id: The Digital Twin Universe instance id where the agent
                did its work.
            task_context: The original task instructions handed to the agent
                under test. Used by the extractor to recognize what kind of
                deliverables to look for.
            extract_yaml_path: Path to the agent's `extract.yaml` on the host.
                Read here and rendered into the phase-1 prompt as a hint for
                where session logs and deliverables typically live.
            output_dir: Host directory where pulled artifacts will land.
                Created if it doesn't exist. All manifest destinations must
                be inside this directory.
        """
        if self._prepared is None:
            raise RuntimeError("Extractor.setup() must be called before run().")

        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        extract_yaml_text = Path(extract_yaml_path).read_text(encoding="utf-8")

        start = time.monotonic()
        submit_tool = SubmitExtractionManifestTool()

        session_id = f"extractor-{uuid.uuid4().hex[:8]}"
        session = await self._prepared.create_session(
            session_id=session_id,
            session_cwd=Path.cwd(),
        )
        await session.coordinator.mount("tools", submit_tool, name=submit_tool.name)

        phase1_prompt = PHASE1_PROMPT_TEMPLATE.format(
            task_context=task_context.strip(),
            dtu_id=dtu_id,
            output_dir=str(out),
            extract_yaml_text=extract_yaml_text.rstrip(),
        )

        extraction_report = ""
        validation_errors: list[str] = []
        manifest: ExtractionManifest | None = None

        async with session:
            # Phase 1: explore + pull + report.
            extraction_report = await session.execute(phase1_prompt)
            (out / "extraction_report.md").write_text(extraction_report, encoding="utf-8")

            # Phase 2/3: submit + retries.
            attempt_prompt = PHASE2_PROMPT
            for attempt in range(MAX_RETRIES + 1):
                _ = await session.execute(attempt_prompt)
                submission = submit_tool.last_submission
                if submission is None:
                    validation_errors = ["submit_extraction_manifest was not called"]
                else:
                    validation_errors = validate_manifest(submission, out)
                if not validation_errors and submission is not None:
                    manifest = submission
                    break
                if attempt < MAX_RETRIES:
                    attempt_prompt = PHASE3_RETRY_TEMPLATE.format(
                        errors="\n".join(f"  - {e}" for e in validation_errors)
                    )

        if manifest is not None:
            (out / "manifest.json").write_text(
                json.dumps(
                    {
                        "extracted": [asdict(e) for e in manifest.extracted],
                        "missing": [asdict(m) for m in manifest.missing],
                        "summary": manifest.summary,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        result = ExtractionResult(
            dtu_id=dtu_id,
            extract_yaml_path=str(extract_yaml_path),
            output_dir=str(out),
            extraction_report=extraction_report,
            manifest=manifest,
            validation_errors=validation_errors,
            submit_manifest_attempts=submit_tool.call_count,
            extractor_session_id=session_id,
            elapsed_s=time.monotonic() - start,
        )
        (out / "extraction_result.json").write_text(result.to_json(), encoding="utf-8")
        return result


__all__ = [
    "DEFAULT_FOUNDATION_SOURCE",
    "DEFAULT_PROVIDER_SOURCE",
    "MAX_RETRIES",
    "SYSTEM_INSTRUCTION",
    "ExtractedFile",
    "ExtractionManifest",
    "ExtractionResult",
    "Extractor",
    "MissingItem",
]
