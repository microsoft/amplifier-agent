"""Run one (agent, task) trial: launch a DTU, drive the agent, pull deliverables.

Prove one agent can complete one real task end to end, deliverables land on
disk, telemetry is captured for cost/token accounting, and `trial.json`
honestly distinguishes a crash, a timeout, and a legitimate zero-deliverable
run from each other and from success.

A trial always writes `trial.json` and always destroys its DTU, even when a
stage fails partway through -- a failed trial is data, not a dead end.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobbench import agents, images, prompt
from jobbench.dataset import Task
from jobbench.dtu import DTU, DTUError
from jobbench.metrics import (
    NOT_AVAILABLE,
    find_events_files,
    find_opencode_db_files,
    normalize_metrics,
    normalize_opencode_metrics,
)

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "profiles" / "task.template.yaml"
IMAGE_PLACEHOLDER = "__AGENT_IMAGE__"

DEFAULT_TIMEOUT_S = 3600.0

# Slack added on top of the agent's own wall-clock budget for the DTU exec
# round trip itself (CLI startup, JSON envelope write). Keeps a legitimate
# near-the-limit run from being misclassified as a DTU-layer timeout.
_EXEC_SLACK_S = 60.0

# Signatures left in a captured agent.log when amplifier-module-provider-anthropic
# injects a synthetic error message because a tool_call had no paired
# tool_result in conversation history (see
# amplifier_module_provider_anthropic/__init__.py:2014). The provider only
# logs a logger.warning when this happens -- exit code, deliverables, and
# score all look completely normal -- so without this scan the run silently
# measures an agent re-deciding it never read its own instructions instead of
# the agent's real capability. Fixing the provider/opencode is out of scope
# for this harness; this list only makes the condition visible. Kept as a
# module-level constant, not inline in the scan function, so a new signature
# discovered later is a one-line addition.
TOOL_RESULT_LOSS_SIGNATURES: tuple[str, ...] = (
    "[SYSTEM ERROR: Tool result missing from conversation history]",
    "Tool execution was interrupted and no result was captured",
)

# The signatures above are the direct, high-confidence evidence, but they are
# frequently NOT observable: the provider injects that text into the message
# history it sends to the model, and only logs a logger.warning locally. For a
# CLI whose stdout carries just the assistant's prose (opencode), the literal
# string never appears in agent.log at all -- measured: 0 hits on a run that
# looped 27 times. What IS observable is the model narrating the injected error
# in its own words, re-deciding it never read a file it already read.
#
# So this second pass is an INDIRECT heuristic on that narration. It is kept
# separate from the literal signatures above, reported with a lower confidence,
# and gated behind a threshold, because a single "let me read the file" line is
# ordinary agent behavior. Repetition is the tell.
#
# Measured discriminating power on one identical task:
#   amplifier-agent 0, amplifier-foundation 0, opencode-vanilla 0,
#   opencode-amplifier 21 and 27.
TOOL_RESULT_LOSS_NARRATION = re.compile(
    r"(?:"
    r"(?:still |actually |now )?(?:need to|have to|must) actually (?:read|open)"
    r"|haven[''`]?t (?:actually )?(?:read|opened)"
    r"|(?:realize|notice) (?:I|that I) (?:have not|haven[''`]?t|never)"
    r"|operating (?:on|from) infer(?:red|ence)"
    r"|without (?:actually )?(?:opening|reading)"
    r")",
    re.IGNORECASE,
)

# Below this many narration hits the signal is indistinguishable from an agent
# legitimately deciding to re-read something, so it is not worth flagging.
TOOL_RESULT_LOSS_NARRATION_THRESHOLD = 3

# agent.log is stdout, then this marker, then stderr. The narration heuristic
# scans only the stdout half; see `_detect_tool_result_loss`.
STDERR_SEPARATOR = "--- stderr ---"


class TrialError(RuntimeError):
    """Trial setup failed before there was a DTU to attribute it to."""


@dataclass
class TrialResult:
    """Everything `run.py run` needs to report, and everything a later
    grading phase needs to know before it touches the deliverables."""

    agent: str
    task_id: str
    task_selector: str
    split: str
    model: str
    dtu_id: str | None
    image_alias: str
    status: str  # "completed" | "timeout" | "crashed" | "no_deliverables"
    exit_code: int | None
    agent_run_s: float | None
    started_at: str
    finished_at: str
    deliverable_count: int
    deliverable_bytes: int
    error: str | None
    # Telemetry summary, duplicated from metrics.json so a single file
    # answers "did it run, and what did it cost" without a second read.
    # `not_available` (never a fabricated 0) when no session telemetry
    # could be collected -- see jobbench.metrics's not_available discipline.
    cost_usd: float | str
    total_tokens: int | str
    llm_responses: int | str
    # Quality signals distinct from `status`: a run can complete (exit 0,
    # deliverables produced, a real score) while still being a degenerate
    # measurement of something other than the agent's real capability.
    # Never folded into `status` -- see `_detect_tool_result_loss`. Empty
    # list, never omitted, so a clean run and an ungraded field are never
    # ambiguous in trial.json.
    warnings: list[dict[str, Any]]

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _detect_tool_result_loss(log_path: Path) -> dict[str, Any] | None:
    """Scan a captured agent.log for provider tool-result-loss signatures.

    Returns a `trial.json` warnings-list entry when any signature appears,
    else None. This is a quality signal, not a run-status signal: a trial
    that hits this can still exit 0 and produce deliverables, because the
    model narrates the synthetic error back to itself and keeps going --
    the exit code, deliverable count, and score all look normal even though
    the agent burned wall-clock time fighting its own context rather than
    doing the task. Deliberately does not touch `status`; see the docstring
    on `TrialResult.warnings`.

    Missing or unreadable logs are not an error here -- some agents/trial
    outcomes (e.g. a crash before the log was created) never produce one.
    """
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    direct = sum(text.count(sig) for sig in TOOL_RESULT_LOSS_SIGNATURES)
    if direct:
        matched = [sig for sig in TOOL_RESULT_LOSS_SIGNATURES if sig in text]
        return {
            "kind": "tool_result_loss",
            "confidence": "direct",
            "count": direct,
            "detail": (
                f"{direct} occurrence(s) of the provider-injected tool-result-loss "
                f"signature in agent.log ({matched}). The agent is narrating a "
                "synthetic [SYSTEM ERROR] back to itself and re-deciding it never "
                "read its own instructions, so this run measures the agent fighting "
                "its own context rather than its task capability. Root cause is "
                "outside this harness: "
                "amplifier_module_provider_anthropic/__init__.py:2014."
            ),
        }

    # Fall back to the narration heuristic. Only the assistant's prose is worth
    # scanning: the stderr half of the log is the CLI's own TUI rendering, whose
    # re-rendered checklist lines ("Read TASK_INSTRUCTIONS.txt directly") would
    # otherwise inflate the count without evidence of an actual re-decision.
    prose = text.split(STDERR_SEPARATOR, 1)[0]
    narration = len(TOOL_RESULT_LOSS_NARRATION.findall(prose))
    if narration < TOOL_RESULT_LOSS_NARRATION_THRESHOLD:
        return None

    return {
        "kind": "tool_result_loss",
        "confidence": "heuristic",
        "count": narration,
        "detail": (
            f"{narration} occurrence(s) of the agent re-deciding it had not read a "
            "file it already read (threshold "
            f"{TOOL_RESULT_LOSS_NARRATION_THRESHOLD}). The literal provider "
            "signature was absent, which is expected for CLIs whose stdout carries "
            "only assistant prose: the injected [SYSTEM ERROR] reaches the model "
            "but is never printed. Treat this run's wall-clock and token figures as "
            "contaminated. Root cause is outside this harness: "
            "amplifier_module_provider_anthropic/__init__.py:2014."
        ),
    }


def _render_launch_profile(image_alias: str, dest: Path) -> Path:
    """Substitute the image-alias placeholder and write the rendered profile.

    Plain string substitution, not DTU's own `${VAR}` launch-variable
    mechanism -- that substitution is documented to reach
    `provision.setup_cmds`, not `base.image`, so we don't lean on it for the
    one field that picks the whole environment.
    """
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if IMAGE_PLACEHOLDER not in text:
        raise TrialError(f"{TEMPLATE_PATH} is missing the {IMAGE_PLACEHOLDER!r} placeholder")
    dest.write_text(text.replace(IMAGE_PLACEHOLDER, image_alias), encoding="utf-8")
    return dest


def _flatten_pulled_deliverables(deliverables_dir: Path) -> None:
    """Undo `dtu.file_pull`'s `cp -r` basename convention for the output dir.

    Pulling `/workspace/output/` to `deliverables/` lands files at
    `deliverables/output/<file>`, because the CLI preserves the source
    directory's basename under the destination the same way `file_push`
    does (see dtu.py's `_pushed_dir_root`). Every downstream consumer
    (grading, deliverable counting) expects `deliverables/<file>` directly,
    so move the nested `output/` contents up one level.
    """
    nested = deliverables_dir / "output"
    if not nested.is_dir():
        return
    for item in nested.iterdir():
        target = deliverables_dir / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        item.rename(target)
    nested.rmdir()


def _incus_safe(value: str) -> str:
    """Incus instance names accept only alphanumerics and hyphens.

    Occupation slugs are underscore-separated (`civil_engineers`), so they
    must be transliterated before they can appear in a container name --
    Incus rejects the launch outright otherwise.
    """
    safe = "".join(ch if ch.isalnum() else "-" for ch in value)
    return safe.strip("-")


def _dtu_name(agent_name: str, task: Task) -> str:
    """`jb-<agent-short>-<occupation[:12]>-t<N>-<uuid6>`, kept to Incus's
    naming budget and unique per (agent, task) so concurrent trials never
    collide on the container name.
    """
    uuid6 = uuid.uuid4().hex[:6]
    occupation = _incus_safe(task.occupation[:12])
    tail = f"-t{task.task_num}-{uuid6}"
    fixed = len("jb-") + len("-") + len(occupation) + len(tail)
    agent_short = _incus_safe(agent_name[: max(60 - fixed, 1)])
    return f"jb-{agent_short}-{occupation}{tail}"[:60]


async def run_trial(
    agent_name: str,
    task: Task,
    trial_dir: Path,
    *,
    model: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    on_stage: Callable[[str], None] | None = None,
    agent_kwargs: dict[str, Any] | None = None,
) -> TrialResult:
    """Run one trial end to end.

    Sequence: render the launch profile, launch a DTU, let the adapter write
    its per-trial config, seed the task folder and prompt, run the agent
    under a wall-clock timeout, pull whatever landed in /workspace/output,
    destroy the DTU, write trial.json.

    `on_stage`, if given, is called with a short human-readable message at
    each major step -- the mechanism for a caller's progress reporting.
    Must not raise; this function does not guard against it.

    `agent_kwargs`, if given, is forwarded to the adapter's constructor (e.g.
    `{"bundle": "..."}` for amplifier-foundation). Most agents take none.
    """

    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    trial_dir.mkdir(parents=True, exist_ok=True)
    adapter = agents.get(agent_name, **(agent_kwargs or {}))
    image_alias = images.agent_alias(agent_name)
    started_at = _now()

    dtu: DTU | None = None
    status = "crashed"
    exit_code: int | None = None
    agent_run_s: float | None = None
    error: str | None = None
    deliverable_count = 0
    deliverable_bytes = 0
    cost_usd: float | str = NOT_AVAILABLE
    total_tokens: int | str = NOT_AVAILABLE
    llm_responses: int | str = NOT_AVAILABLE
    warnings: list[dict[str, Any]] = []

    try:
        stage(f"rendering launch profile ({image_alias})")
        profile_path = _render_launch_profile(image_alias, trial_dir / "launch_profile.yaml")
        dtu_name = _dtu_name(agent_name, task)
        stage(f"launching DTU {dtu_name}")
        dtu = await DTU.launch(profile_path, name=dtu_name)

        stage("configuring agent")
        await adapter.configure(dtu, model=model)

        stage("seeding task folder and prompt")
        prompt_text = prompt.render()
        (trial_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
        await dtu.file_push(task.task_folder, f"{prompt.WORKSPACE}/")
        await dtu.file_push(trial_dir / "prompt.txt", prompt.PROMPT_PATH)

        log_path = trial_dir / "agent.log"
        stage(f"running agent (timeout={timeout_s:.0f}s)")
        start = time.monotonic()
        try:
            result = await dtu.exec_cmd(
                adapter.command(),
                timeout_s=timeout_s + _EXEC_SLACK_S,
                stream_to_logfile=log_path,
            )
        except DTUError as exc:
            agent_run_s = time.monotonic() - start
            status = "timeout"
            error = str(exc)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- trial harness: {exc} ---\n")
        else:
            agent_run_s = time.monotonic() - start
            exit_code = result.returncode
            status = "completed" if exit_code == 0 else "crashed"

        # Pull whatever landed even after a timeout -- partial output is
        # still signal, and a pull of an empty/missing dir is harmless.
        stage("pulling deliverables")
        deliverables_dir = trial_dir / "deliverables"
        try:
            await dtu.file_pull(f"{prompt.OUTPUT_DIR}/", deliverables_dir)
        except DTUError as exc:
            if error is None:
                error = str(exc)

        if deliverables_dir.is_dir():
            _flatten_pulled_deliverables(deliverables_dir)
            files = [p for p in deliverables_dir.rglob("*") if p.is_file()]
            deliverable_count = len(files)
            deliverable_bytes = sum(p.stat().st_size for p in files)

        # Upstream also force-fails an exit-0 run that produced nothing --
        # a deliverable-free "success" is not success.
        if status == "completed" and deliverable_count == 0:
            status = "no_deliverables"

        # Quality signal, deliberately kept separate from `status` above: a
        # trial can complete cleanly while the agent spent real wall-clock
        # time fighting a provider-injected synthetic error instead of doing
        # the task. See `_detect_tool_result_loss`.
        tool_result_loss = _detect_tool_result_loss(log_path)
        if tool_result_loss is not None:
            warnings.append(tool_result_loss)

        # Session/trajectory state, for later cost and token accounting.
        # Best effort -- a pull failure here must never take down an
        # otherwise good trial, but is logged loudly so a gap in telemetry
        # is visible rather than silently absent.
        stage("collecting session telemetry")
        sessions_dir = trial_dir / "sessions"
        for session_path in adapter.session_dirs:
            basename = Path(session_path).name
            try:
                await dtu.file_pull(session_path, sessions_dir / basename)
            except Exception as exc:  # noqa: BLE001 - telemetry is best effort, must not fail the trial
                logger.warning(
                    "session pull failed for %s (%s): %s -- metrics will be incomplete",
                    session_path,
                    agent_name,
                    exc,
                )

        # metrics.json: the normalized cost/token record for this trial.
        # Routed on the adapter's own declared metrics_source, never on agent
        # name -- opencode has no events.jsonl at all, so hardcoding the
        # events.jsonl path here would search for a file that agent never
        # writes and silently report all-not_available, indistinguishable
        # from a real collection failure.
        # agent_wallclock_s (event-timestamp span) is replaced with our own
        # agent_run_s (measured around the agent command) -- the harness's
        # own clock is ground truth for how long the trial actually ran.
        try:
            if adapter.metrics_source == "opencode_db":
                metrics_record = normalize_opencode_metrics(
                    find_opencode_db_files(sessions_dir),
                    source=adapter.name,
                    workspace_dir=prompt.WORKSPACE,
                )
            else:
                metrics_record = normalize_metrics(
                    find_events_files(sessions_dir), source=adapter.name
                )
            metrics_record.pop("agent_wallclock_s", None)
            metrics_record["agent_run_s"] = agent_run_s
            (trial_dir / "metrics.json").write_text(
                json.dumps(metrics_record, indent=2) + "\n", encoding="utf-8"
            )
            cost_usd = metrics_record["cost_usd"]
            total_tokens = metrics_record["total_tokens"]
            llm_responses = metrics_record["llm_responses"]
        except Exception as exc:  # noqa: BLE001 - telemetry is best effort, must not fail the trial
            logger.warning(
                "metrics computation failed: %s -- trial.json cost fields stay not_available", exc
            )

    except Exception as exc:  # noqa: BLE001 - trial.json must record ANY failure honestly
        if status != "timeout":
            status = "crashed"
        error = error or str(exc)
    finally:
        finished_at = _now()
        if dtu is not None:
            stage(f"destroying DTU {dtu.id}")
            await dtu.destroy()

    trial_result = TrialResult(
        agent=agent_name,
        task_id=task.id,
        task_selector=task.selector,
        split=task.split,
        model=model,
        dtu_id=dtu.id if dtu is not None else None,
        image_alias=image_alias,
        status=status,
        exit_code=exit_code,
        agent_run_s=agent_run_s,
        started_at=started_at,
        finished_at=finished_at,
        deliverable_count=deliverable_count,
        deliverable_bytes=deliverable_bytes,
        error=error,
        cost_usd=cost_usd,
        total_tokens=total_tokens,
        llm_responses=llm_responses,
        warnings=warnings,
    )
    trial_result.write(trial_dir / "trial.json")
    return trial_result


__all__ = ["DEFAULT_TIMEOUT_S", "TrialError", "TrialResult", "run_trial"]
