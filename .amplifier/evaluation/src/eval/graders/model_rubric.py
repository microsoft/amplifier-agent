"""model_rubric grader: a Foundation session that audits an agent's work in a DTU.

This is the default grader. It is a Foundation session
driven by Claude Opus 4.8 (the local `providers/anthropic-opus-4-8.yaml`), with
an auditor SYSTEM_INSTRUCTION whose ABSOLUTE RULE is that the grader must NEVER
modify the agent's files (read-only audit).

For each evaluation in the grader.yaml the grader runs one multi-turn session
with three phases:

1. Explore the DTU via bash (`amplifier-digital-twin exec`) and write a
   free-text initial_report.md.
2. Submit the structured rubric via the `submit_rubric` tool (the JSON input
   schema is built per-evaluation with the exact criterion keys and max points).
3. Up to 2 correction retries via `validate_rubric_submission`.

The final weighted score is computed across all evaluations and returned as the
harness's shared `base.GraderResult`.

Copy-adapted for this harness from the reference library
(`amplifier_evaluation.grader.grader`). The auditor framing, three-phase
session, per-evaluation submit tool, and retry loop are preserved. Adaptations:
the driver model is the local Opus 4.8 provider (not anthropic-sonnet); the
class conforms to `eval.graders.base.Grader` and returns `base.GraderResult` /
`base.EvaluationResult`; and grading runs sequentially (the single-evaluation
skeleton task does not need the library's concurrency, and sequential audits
keep the read-only DTU access trivially safe).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from amplifier_foundation import Bundle, load_bundle

from eval.graders.base import CriterionScore, EvaluationResult, GraderResult
from eval.graders.schema import Evaluation, GraderConfig
from eval.graders.tools import SubmitRubricTool, validate_rubric_submission

logger = logging.getLogger(__name__)

GRADER_TYPE = "model_rubric"

DEFAULT_FOUNDATION_SOURCE = "git+https://github.com/microsoft/amplifier-foundation@main"

# The grader's driver model is Claude Opus 4.8, pinned by the local provider
# bundle copied into this harness. Resolve it relative to the harness root
# (src/eval/graders/model_rubric.py -> parents[3] == the `evaluation/` root) so
# the path is correct regardless of the caller's working directory.
_HARNESS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROVIDER_SOURCE = str(_HARNESS_ROOT / "providers" / "anthropic-opus-4-8.yaml")

MAX_RETRIES = 2


SYSTEM_INSTRUCTION = """\
You are an auditor evaluating another AI agent's deliverables. Remain
impartial, critical, and objective. You will receive a list of steps to follow
during the audit and a rubric to score the agent's work against.

The agent's work lives inside a Digital Twin Universe container. You will
receive its id (like `dtu-abc12345`). You have a `bash` tool. To run a command
inside the Digital Twin Universe, prefix it with the exec wrapper:

    amplifier-digital-twin exec <dtu_id> -- <command>

For commands with tricky quoting or multi-line input, write your message to a
host file first and push it in:

    echo "<message>" > /tmp/msg.txt
    amplifier-digital-twin file-push <dtu_id> /tmp/msg.txt /tmp/msg.txt
    amplifier-digital-twin exec <dtu_id> -- bash -c 'cmd --input "$(cat /tmp/msg.txt)"'

ABSOLUTE RULES (NON-NEGOTIABLE):

- You must NEVER modify the agent's code or files. Changing its output is like
  a teacher changing a student's exam. Read only; do not write, edit, move, or
  delete anything inside the container.
- You are NOT debugging or troubleshooting. Evaluate the work as-is. If
  something does not work after following the agent's instructions, note that
  and move on. Do not fix it.
- You should not need to obtain API keys. They are provided as environment
  variables in the container.
- Never read large PDFs or binary files directly. Write code to parse them
  into text instead.
- If a tool times out or hangs, treat that as a failure for that criterion.
- Ignore stale files or build artifacts from previous runs. Evaluate based
  only on what you observe during this audit.
- Be concise. Score what you saw with one or two sentences of reasoning per
  criterion.
"""


PHASE1_PROMPT_TEMPLATE = """\
The agent was asked to do the following:
\"\"\"
{task_context}
\"\"\"

You will evaluate the agent's work against this rubric. Each criterion has a
maximum point value and a description of what to look for:

{rubric_json}

The agent's work lives inside Digital Twin Universe `{dtu_id}`. Use bash with
`amplifier-digital-twin exec` to explore.

Follow these steps:

{steps}

After completing the steps, write a free-text "initial report" as your final
assistant message. The report should describe:

- What you observed in the DTU.
- For each criterion: your tentative scoring intent and what you saw.
- Anything you are uncertain about and how that affects scoring.

Do NOT call submit_rubric yet. Your final assistant message in this turn is
the initial report.
"""


PHASE2_PROMPT = """\
Now submit the structured rubric by calling the `submit_rubric` tool exactly
once.

For each criterion in the rubric, provide:

- points_awarded: integer in [0, max_points]
- reasoning: one or two sentences citing what you saw

Use the observations from your initial report. Do not run more bash commands
unless absolutely necessary. After submit_rubric, do not call any other tools.
"""


PHASE3_RETRY_TEMPLATE = """\
Your submit_rubric call had these problems:

{errors}

Call submit_rubric again with corrections. Only change the entries that were
flagged; leave correct entries as they were.
"""


class ModelRubricGrader:
    """Compose Foundation + Opus 4.8 provider + auditor instruction, then grade.

    Conforms to `eval.graders.base.Grader`. Call `setup()` once (expensive
    bundle compose/prepare), then `run()` per trial.
    """

    grader_type = GRADER_TYPE

    def __init__(
        self,
        foundation_source: str = DEFAULT_FOUNDATION_SOURCE,
        provider_source: str = DEFAULT_PROVIDER_SOURCE,
    ) -> None:
        """Construct a ModelRubricGrader.

        Args:
            foundation_source: Source for the foundation bundle. Defaults to the
                canonical git URL so no local checkout is required.
            provider_source: Source for the provider bundle YAML. Defaults to
                the local Opus 4.8 provider copied into this harness, so the
                grader is driven by Claude Opus 4.8.
        """
        self.foundation_source = foundation_source
        self.provider_source = provider_source
        self._prepared = None

    async def setup(self) -> None:
        """Load + compose + prepare the bundle. Expensive; call once."""
        foundation = await load_bundle(self.foundation_source)
        provider = await load_bundle(self.provider_source)
        system_bundle = Bundle(
            name="grader-system",
            version="0.1.0",
            instruction=SYSTEM_INSTRUCTION,
        )
        composed = foundation.compose(provider).compose(system_bundle)
        self._prepared = await composed.prepare()

    async def run(
        self,
        grader_yaml_path: Path | str,
        task_context: str,
        dtu_id: str,
        output_dir: Path | str,
        grader_data_dir: Path | str | None = None,
    ) -> GraderResult:
        """Audit a DTU against a grader.yaml. Runs each evaluation in turn.

        Args:
            grader_yaml_path: Path to the grader.yaml describing evaluations.
            task_context: The original task instructions handed to the agent
                under test, as context for the auditor.
            dtu_id: The Digital Twin Universe instance id to audit.
            output_dir: Host directory where initial reports and rubric JSON
                files are written (per-evaluation subdirectories).
            grader_data_dir: Reserved for mounts (unused by the skeleton task).
                Kept in the signature to satisfy the `Grader` protocol.
        """
        if self._prepared is None:
            raise RuntimeError("ModelRubricGrader.setup() must be called before run().")

        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        config = GraderConfig.from_yaml(grader_yaml_path)

        start = time.monotonic()
        eval_results: list[EvaluationResult] = []
        # Sequential audits: each evaluation is an independent read-only pass
        # over the same live DTU. The skeleton task has one evaluation; running
        # them in turn keeps the read-only guarantee trivially safe.
        for evaluation in config.evaluations:
            eval_dir = out / evaluation.name
            eval_dir.mkdir(parents=True, exist_ok=True)
            eval_results.append(
                await self._run_one_evaluation(
                    evaluation=evaluation,
                    task_context=task_context,
                    dtu_id=dtu_id,
                    eval_dir=eval_dir,
                )
            )

        total_weight = sum(e.weight for e in config.evaluations) or 1.0
        overall = sum(r.score * r.weight for r in eval_results) / total_weight

        grader_result = GraderResult(
            grader_type=config.type,
            grader_yaml_path=str(grader_yaml_path),
            dtu_id=dtu_id,
            evaluations=eval_results,
            overall_score=overall,
            elapsed_s=time.monotonic() - start,
        )
        (out / "grader_result.json").write_text(grader_result.to_json(), encoding="utf-8")
        return grader_result

    async def _run_one_evaluation(
        self,
        evaluation: Evaluation,
        task_context: str,
        dtu_id: str,
        eval_dir: Path,
    ) -> EvaluationResult:
        if self._prepared is None:
            raise RuntimeError("ModelRubricGrader.setup() must be called before run().")
        start = time.monotonic()

        submit_tool = SubmitRubricTool(evaluation)

        session_id = f"grader-{evaluation.name}-{uuid.uuid4().hex[:8]}"
        session = await self._prepared.create_session(
            session_id=session_id,
            session_cwd=Path.cwd(),
        )
        await session.coordinator.mount("tools", submit_tool, name=submit_tool.name)

        rubric_json = json.dumps(evaluation.rubric_dict(), indent=2)
        phase1_prompt = PHASE1_PROMPT_TEMPLATE.format(
            task_context=task_context.strip(),
            rubric_json=rubric_json,
            dtu_id=dtu_id,
            steps=evaluation.steps.strip(),
        )

        initial_report = ""
        validation_errors: list[str] = []
        rubric_scores: dict[str, CriterionScore] | None = None
        points_awarded = 0

        async with session:
            # Phase 1: explore + initial report (free text response).
            initial_report = await session.execute(phase1_prompt)
            (eval_dir / "initial_report.md").write_text(initial_report, encoding="utf-8")

            # Phase 2/3: submit + retries.
            attempt_prompt = PHASE2_PROMPT
            for attempt in range(MAX_RETRIES + 1):
                _ = await session.execute(attempt_prompt)
                submission = submit_tool.last_submission
                if submission is None:
                    validation_errors = ["submit_rubric was not called"]
                else:
                    validation_errors = validate_rubric_submission(submission, evaluation)
                if not validation_errors and submission is not None:
                    rubric_scores = submission.scores
                    break
                if attempt < MAX_RETRIES:
                    attempt_prompt = PHASE3_RETRY_TEMPLATE.format(
                        errors="\n".join(f"  - {e}" for e in validation_errors)
                    )

        if rubric_scores is not None:
            points_awarded = sum(s.points_awarded for s in rubric_scores.values())
            (eval_dir / "rubric.json").write_text(
                json.dumps({k: asdict(v) for k, v in rubric_scores.items()}, indent=2),
                encoding="utf-8",
            )

        points_possible = evaluation.total_points
        score = (points_awarded / points_possible) if points_possible > 0 else 0.0

        return EvaluationResult(
            name=evaluation.name,
            weight=evaluation.weight,
            points_awarded=points_awarded,
            points_possible=points_possible,
            score=score,
            rubric_scores=rubric_scores,
            initial_report=initial_report,
            validation_errors=validation_errors,
            submit_attempts=submit_tool.call_count,
            grader_session_id=session_id,
            elapsed_s=time.monotonic() - start,
        )


__all__ = [
    "GRADER_TYPE",
    "DEFAULT_FOUNDATION_SOURCE",
    "DEFAULT_PROVIDER_SOURCE",
    "MAX_RETRIES",
    "SYSTEM_INSTRUCTION",
    "ModelRubricGrader",
]
