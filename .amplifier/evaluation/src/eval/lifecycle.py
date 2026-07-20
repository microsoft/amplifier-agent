"""Run one evaluation trial end to end: the single-command trial loop.

`run_trial()` is the only place that knows the full sequence of stages. It wires
the five proven building blocks (dtu, install, ai_user, extractor, grader) into
one lifecycle and adds the needed seams (metrics + clean agent-only
wall clock). It reuses those pieces verbatim; nothing here re-implements them.

Stages (deterministic order, mirrors the reference state machine):

    launching     -> compose + launch the DTU from the task profile
    installing    -> run the agent's setup_cmds inside the DTU
    seeding       -> push the task workspace + instructions into /workspace
    running_agent -> AIUser drives the agent until conclude / timeout
    extracting    -> Extractor pulls the agent's artifacts + session logs
    grading       -> Grader audits the live DTU against the task rubric
    cleaning_up   -> destroy the DTU

EXTRACT runs BEFORE GRADE deliberately: extraction pulls a
clean snapshot of the agent's work before the read-only grader audit touches the
live DTU. Extractor and grader failures are caught NON-FATALLY and recorded, so
teardown always happens. The DTU is destroyed in a `finally` no matter what.

Every stage transition writes `state.json` atomically so an external observer can
watch progress. On completion a consolidated `trial_result.json` is written with
the agent/task ids, the grader score, the extraction manifest pointer, the full
metrics (cost/tokens/agent + total wall clock), status, and all artifact paths.

Agent-only wall clock: this agent is one-shot, so
each in-DTU agent invocation is one bash tool call in the AIUser's own session
log. We sum the wall time of those marker-matched bash tool:pre/tool:post pairs
to get a clean agent-only number, distinct from AIUser model time and from the
events-span floor. See `compute_agent_wallclock`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.ai_user import AIUser, InteractionResult
from eval.dtu import DTU, DTUError
from eval.extractor import Extractor, ExtractionResult
from eval.graders import Grader, GraderResult
from eval.install import (
    InstallError,
    compose_launch_profile,
    install_agent,
    seed_sessions,
    seed_workspace,
    verify_env,
)
from eval.metrics import NOT_AVAILABLE, build_metrics
from eval.schema import (
    AgentSpec,
    StageRecord,
    TaskSpec,
    TrialResult,
    TrialState,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"
TRIAL_RESULT_FILENAME = "trial_result.json"


# ---------------------------------------------------------------------------
# On-disk trial state (atomic, per-transition), adapted from the reference
# library's harness/state.py. Kept tiny and local: the trial loop is the only
# writer, and schema.py already owns the state vocabulary + result shape.
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write text via tempfile + os.replace so external reads can never tear."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _save_state(trial_dir: Path, record: TrialResult) -> None:
    """Persist the current TrialResult snapshot to state.json atomically."""
    _atomic_write(trial_dir / STATE_FILENAME, json.dumps(asdict(record), indent=2, default=str))


def _transition(trial_dir: Path, record: TrialResult, new_state: TrialState) -> None:
    """Advance the trial to `new_state`: append history, save state.json."""
    now = utcnow_iso()
    record.state = new_state.value
    record.history.append(StageRecord(state=new_state.value, at=now))
    if new_state.is_terminal:
        record.finished_at = now
    _save_state(trial_dir, record)


# ---------------------------------------------------------------------------
# Agent-only wall clock: sum the in-DTU agent command time from the AI User's
# own session events (the "time each exec ... <agent cmd> and sum" approach,
# captured at the exec layer's event log rather than a separate subprocess).
# ---------------------------------------------------------------------------

# Foundation writes host sessions to ~/.amplifier/projects/<slug>/sessions/<id>/.
_SESSIONS_ROOT = Path.home() / ".amplifier" / "projects"


def _find_ai_user_session_dir(session_id: str | None) -> Path | None:
    """Locate the foundation session dir (with events.jsonl) for `session_id`."""
    if not session_id or not _SESSIONS_ROOT.is_dir():
        return None
    for candidate in _SESSIONS_ROOT.glob(f"*/sessions/{session_id}"):
        if (candidate / "events.jsonl").is_file():
            return candidate
    return None


def _iso_to_epoch(tstr: str) -> float | None:
    """Parse an ISO-8601 timestamp (nanoseconds tolerated) to epoch seconds.

    `datetime.fromisoformat` only accepts up to microseconds, so a longer
    fractional part (the amplifier stack emits nanoseconds) is trimmed to 6
    digits before parsing.
    """
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", tstr)
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = frac[:7] if frac else ""  # '.' + up to 6 digits
    try:
        return datetime.fromisoformat(f"{base}{frac}{tz}").timestamp()
    except ValueError:
        return None


def compute_agent_wallclock(session_dir: Path, markers: list[str]) -> dict[str, Any]:
    """Sum the wall time of the AI User's in-DTU agent commands.

    The AI User drives the agent by shelling `amplifier-digital-twin exec ...`
    through its `bash` tool. Each such call emits a `tool:pre` and a `tool:post`
    event in the AI User's `events.jsonl`, both carrying an ISO-8601 `ts` and a
    shared `data.tool_call_id`. For every bash command whose text contains one of
    `markers` (the agent binary invocation), the elapsed `post.ts - pre.ts` is
    the actual in-DTU agent-command time. Summed across turns, this is the clean
    agent-only wall clock -- it excludes AI User model/think time between turns
    and the AI User's own non-agent bash calls (e.g. `cat answer.txt`).

    Returns a dict with `agent_wallclock_s`, `matched_commands`, `per_command`
    (each a {command, elapsed_s} record), and `notes`. `matched_commands == 0`
    means no agent invocation was timed (caller should fall back).
    """
    events_path = session_dir / "events.jsonl"
    pre_ts: dict[str, float] = {}
    pre_cmd: dict[str, str] = {}
    per_command: list[dict[str, Any]] = []
    total = 0.0

    for raw in events_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        event = obj.get("event")
        data = obj.get("data")
        if not isinstance(data, dict) or data.get("tool_name") != "bash":
            continue
        call_id = data.get("tool_call_id")
        if not isinstance(call_id, str):
            continue
        tool_input = data.get("tool_input")
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command", ""))
        epoch = _iso_to_epoch(str(obj.get("ts", "")))
        if epoch is None:
            continue

        if event == "tool:pre":
            pre_ts[call_id] = epoch
            pre_cmd[call_id] = command
        elif event == "tool:post" and call_id in pre_ts:
            cmd = command or pre_cmd.get(call_id, "")
            if any(marker in cmd for marker in markers):
                elapsed = epoch - pre_ts[call_id]
                if elapsed >= 0:
                    total += elapsed
                    per_command.append({"command": cmd[:160], "elapsed_s": round(elapsed, 3)})
            pre_ts.pop(call_id, None)
            pre_cmd.pop(call_id, None)

    return {
        "agent_wallclock_s": round(total, 3),
        "matched_commands": len(per_command),
        "per_command": per_command,
        "notes": (
            f"Summed {len(per_command)} in-DTU agent command(s) matching "
            f"{markers} from the AI User session bash tool:pre/tool:post pairs."
        ),
    }


# ---------------------------------------------------------------------------
# The trial loop.
# ---------------------------------------------------------------------------


async def run_trial(
    agent: AgentSpec,
    task: TaskSpec,
    trial_dir: Path,
    *,
    ai_user: AIUser,
    extractor: Extractor,
    grader: Grader,
    trial_number: int = 1,
    dtu_name: str | None = None,
    launch_vars: dict[str, str] | None = None,
) -> TrialResult:
    """Run one full trial for `(agent, task)` end to end. Returns a TrialResult.

    The injected `ai_user`, `extractor`, and `grader` must already be `setup()`.
    The DTU is always destroyed on exit (success, failure, or exception), so
    callers never handle cleanup. Extractor/grader failures are caught and
    recorded without aborting teardown. A consolidated `trial_result.json` is
    written next to `state.json` capturing score, files, metrics, and paths.

    `launch_vars` are extra `--var k=v` values passed to the DTU launch (empty
    unless the local working-tree mirror is active; the task profile's
    url_rewrites use GITEA_URL/GITEA_TOKEN to install the agent from the mirror).
    """
    trial_dir = Path(trial_dir).expanduser().resolve()
    trial_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = trial_dir / "extracted"
    grader_dir = trial_dir / "grader"
    install_log = trial_dir / "install.log"

    trial_id = f"{agent.id}__{task.id}__trial-{trial_number}"
    record = TrialResult(
        trial_id=trial_id,
        agent_id=agent.id,
        task_id=task.id,
        trial_number=trial_number,
        state=TrialState.PENDING.value,
        started_at=utcnow_iso(),
        history=[StageRecord(state=TrialState.PENDING.value, at=utcnow_iso())],
    )
    _save_state(trial_dir, record)

    start = time.monotonic()
    dtu: DTU | None = None
    total_wallclock_s: float | None = None
    agent_wc_details: dict[str, Any] | None = None
    extraction: ExtractionResult | None = None
    grader_result: GraderResult | None = None
    metrics_record: dict[str, Any] | None = None

    try:
        # ---- preflight -------------------------------------------------
        missing = verify_env(agent)
        if missing:
            raise InstallError(
                f"agent {agent.id} requires host env not present: {', '.join(missing)}"
            )

        # ---- launch ----------------------------------------------------
        _transition(trial_dir, record, TrialState.LAUNCHING)
        launch_profile = compose_launch_profile(agent, task, trial_dir / "launch_profile.yaml")
        name = dtu_name or f"trial-{task.id[:12]}"
        dtu = await DTU.launch(launch_profile, name=name, variables=launch_vars)
        record.dtu_id = dtu.id
        _save_state(trial_dir, record)

        # ---- install ---------------------------------------------------
        _transition(trial_dir, record, TrialState.INSTALLING)
        await install_agent(agent, dtu, log_to=install_log)

        # ---- seed ------------------------------------------------------
        # SEEDING covers two plants: the task workspace, then
        # -- if the task ships a seed/ dir and the agent declares a seed_target --
        # the prior sessions planted into the agent's session store BEFORE it runs.
        _transition(trial_dir, record, TrialState.SEEDING)
        await seed_workspace(task, dtu)
        seed_result = await seed_sessions(agent, task, dtu, stage_dir=trial_dir)
        seeded_session_ids = list(seed_result.get("session_ids") or [])
        record.history.append(
            StageRecord(
                state=TrialState.SEEDING.value,
                at=utcnow_iso(),
                note=(
                    f"session-seed: {seed_result.get('note')}"
                    + (f" ids={seeded_session_ids}" if seeded_session_ids else "")
                ),
            )
        )
        (trial_dir / "seed_result.json").write_text(
            json.dumps(seed_result, indent=2, default=str), encoding="utf-8"
        )
        _save_state(trial_dir, record)

        # ---- run agent (AI User drives it) -----------------------------
        _transition(trial_dir, record, TrialState.RUNNING_AGENT)
        interaction = await ai_user.run_for(agent, task, dtu.id)
        # Whole-trial wall clock is measured launch -> agent-run finish (the
        # measured trial); extraction/grading/teardown are harness overhead.
        # This matches how the standalone metrics pass measured total_wallclock_s, so the two are
        # directly comparable.
        total_wallclock_s = time.monotonic() - start
        _record_interaction(interaction, trial_dir)
        record.ai_user = {
            "status": "timeout" if interaction.timed_out else "ok",
            "verdict": interaction.conclude.verdict if interaction.conclude else None,
            "summary": interaction.conclude.summary if interaction.conclude else None,
            "elapsed_s": round(interaction.elapsed_s, 3),
            "session_id": interaction.ai_user_session_id,
            "timed_out": interaction.timed_out,
        }
        _save_state(trial_dir, record)

        # Clean agent-only wall clock from the AI User's own session log.
        agent_wc_details = _measure_agent_wallclock(agent, interaction)

        # ---- extract (BEFORE grade, non-fatal) -------------------------
        _transition(trial_dir, record, TrialState.EXTRACTING)
        try:
            extraction = await extractor.run(
                dtu_id=dtu.id,
                task_context=task.scenario,
                extract_yaml_path=agent.extract_path,
                output_dir=extracted_dir,
            )
            record.extractor = {
                "status": "ok",
                "manifest_entries": (
                    len(extraction.manifest.extracted) if extraction.manifest else 0
                ),
                "missing_items": (len(extraction.manifest.missing) if extraction.manifest else 0),
                "elapsed_s": round(extraction.elapsed_s, 3),
            }
        except Exception as exc:  # non-fatal: still grade + teardown
            record.extractor = {"status": "failed", "error": str(exc)}
            logger.exception("extractor failed for trial %s", trial_id)
        _save_state(trial_dir, record)

        # ---- metrics + decontamination -------------------
        # Drop the seeded prior-session events from the metrics inputs so
        # cost/tokens/wallclock reflect ONLY the trial, not the seeded ~3MB.
        metrics_record = _build_metrics(
            agent=agent,
            extracted_dir=extracted_dir,
            total_wallclock_s=total_wallclock_s,
            agent_wc_details=agent_wc_details,
            exclude_session_ids=seeded_session_ids,
        )
        (trial_dir / "metrics.json").write_text(
            json.dumps(metrics_record, indent=2), encoding="utf-8"
        )
        record.metrics = metrics_record

        # ---- grade (non-fatal) -----------------------------------------
        _transition(trial_dir, record, TrialState.GRADING)
        try:
            grader_result = await grader.run(
                grader_yaml_path=task.grader_path,
                task_context=task.scenario,
                dtu_id=dtu.id,
                output_dir=grader_dir,
            )
            (trial_dir / "grader_result.json").write_text(grader_result.to_json(), encoding="utf-8")
            record.grader = {
                "status": "ok",
                "overall_score": round(grader_result.overall_score, 4),
                "evaluations": [
                    {
                        "name": e.name,
                        "weight": e.weight,
                        "score": round(e.score, 4),
                        "points_awarded": e.points_awarded,
                        "points_possible": e.points_possible,
                    }
                    for e in grader_result.evaluations
                ],
                "elapsed_s": round(grader_result.elapsed_s, 3),
            }
        except Exception as exc:  # non-fatal: still teardown
            record.grader = {"status": "failed", "error": str(exc)}
            logger.exception("grader failed for trial %s", trial_id)
        _save_state(trial_dir, record)

        # ---- cleanup ---------------------------------------------------
        _transition(trial_dir, record, TrialState.CLEANING_UP)
        await dtu.destroy()
        dtu = None

        record.elapsed_s = round(total_wallclock_s, 3)
        _transition(trial_dir, record, TrialState.COMPLETED)

    except Exception as exc:
        tb = traceback.format_exc(limit=20)
        record.error = f"{type(exc).__name__}: {exc}\n{tb}"
        record.elapsed_s = round(time.monotonic() - start, 3)
        _transition(trial_dir, record, TrialState.FAILED)
        logger.exception("trial %s failed", trial_id)

    finally:
        # Always destroy the DTU. Best-effort; destroy() already swallows errors.
        if dtu is not None:
            try:
                await dtu.destroy()
            except DTUError as exc:
                logger.warning("dtu destroy on cleanup failed: %s", exc)

    # Consolidated per-trial record (score + files + metrics + paths + status).
    consolidated = _build_trial_record(
        record=record,
        agent=agent,
        task=task,
        trial_dir=trial_dir,
        extraction=extraction,
        grader_result=grader_result,
        metrics_record=metrics_record,
        agent_wc_details=agent_wc_details,
        full_trial_wallclock_s=round(time.monotonic() - start, 3),
    )
    _atomic_write(
        trial_dir / TRIAL_RESULT_FILENAME, json.dumps(consolidated, indent=2, default=str)
    )
    return record


def _measure_agent_wallclock(agent: AgentSpec, interaction: InteractionResult) -> dict[str, Any]:
    """Compute the clean agent-only wall clock, or explain why it is unavailable.

    Returns a dict always carrying `agent_wallclock_s` (float or None),
    `method`, and `matched_commands`. When the agent declares
    `timing.agent_command_markers` in meta.yaml and its AI User session log is on
    disk, this is the summed in-DTU agent-command time. Otherwise it is None and
    the caller falls back to the events-span floor and marks it approximate.
    """
    timing = agent.meta.get("timing") if isinstance(agent.meta.get("timing"), dict) else {}
    markers = timing.get("agent_command_markers") if isinstance(timing, dict) else None
    if not (isinstance(markers, list) and all(isinstance(m, str) for m in markers) and markers):
        return {
            "agent_wallclock_s": None,
            "matched_commands": 0,
            "method": "unavailable",
            "reason": "agent declares no timing.agent_command_markers in meta.yaml",
        }

    session_dir = _find_ai_user_session_dir(interaction.ai_user_session_id)
    if session_dir is None:
        return {
            "agent_wallclock_s": None,
            "matched_commands": 0,
            "method": "unavailable",
            "reason": f"AI User session log not found for {interaction.ai_user_session_id}",
        }

    wc = compute_agent_wallclock(session_dir, markers)
    if wc["matched_commands"] == 0:
        return {
            "agent_wallclock_s": None,
            "matched_commands": 0,
            "method": "unavailable",
            "reason": f"no bash command matched markers {markers} in the session log",
        }
    wc["method"] = (
        "summed in-DTU agent command wall time from AI User session bash tool:pre/tool:post pairs"
    )
    wc["markers"] = markers
    wc["session_dir"] = str(session_dir)
    return wc


def _build_metrics(
    *,
    agent: AgentSpec,
    extracted_dir: Path,
    total_wallclock_s: float | None,
    agent_wc_details: dict[str, Any] | None,
    exclude_session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize extracted events into metrics.json, then set agent_wallclock_s.

    `normalize_metrics` gives us tokens/cost plus the events-span floor. We keep
    that floor as `agent_wallclock_events_span_s` for comparison and set the
    headline `agent_wallclock_s` to the clean per-command number when available,
    falling back to the floor (marked approximate) otherwise. Never fabricated.

    `exclude_session_ids` are seeded prior-session ids to DECONTAMINATE out of the
    metric inputs, so the headline figures reflect only the trial.
    """
    record = build_metrics(
        agent.extract,
        extracted_dir,
        total_wallclock_s=total_wallclock_s,
        source=agent.id,
        exclude_session_ids=exclude_session_ids,
    )

    # Preserve the events-span floor under its own key before overriding.
    events_span = record.get("agent_wallclock_s")
    record["agent_wallclock_events_span_s"] = events_span

    clean = agent_wc_details.get("agent_wallclock_s") if agent_wc_details else None
    if isinstance(clean, (int, float)) and not isinstance(clean, bool):
        record["agent_wallclock_s"] = round(float(clean), 3)
        record["agent_wallclock_method"] = agent_wc_details.get("method")
        record["agent_wallclock_per_command"] = agent_wc_details.get("per_command", [])
        record["notes"] = (
            record.get("notes", "")
            + " agent_wallclock_s is the CLEAN agent-only time: summed in-DTU agent "
            "command wall time from the AI User session bash tool:pre/tool:post pairs. "
            "The events-span floor is retained as agent_wallclock_events_span_s for "
            "comparison."
        )
    else:
        reason = (
            agent_wc_details.get("reason", "clean per-command timing unavailable")
            if agent_wc_details
            else "clean per-command timing unavailable"
        )
        record["agent_wallclock_method"] = f"events-span floor (approximate): {reason}"
        record["notes"] = (
            record.get("notes", "")
            + f" agent_wallclock_s is the events-span FLOOR (APPROXIMATE): {reason}."
        )
    return record


def _build_trial_record(
    *,
    record: TrialResult,
    agent: AgentSpec,
    task: TaskSpec,
    trial_dir: Path,
    extraction: ExtractionResult | None,
    grader_result: GraderResult | None,
    metrics_record: dict[str, Any] | None,
    agent_wc_details: dict[str, Any] | None,
    full_trial_wallclock_s: float,
) -> dict[str, Any]:
    """Assemble the consolidated per-trial record written to trial_result.json."""
    manifest_path = extracted_dir_manifest(trial_dir)
    extracted_files = _list_files(trial_dir / "extracted")

    score: dict[str, Any] | None = None
    if grader_result is not None:
        score = {
            "overall_score": round(grader_result.overall_score, 4),
            "grader_type": grader_result.grader_type,
            "evaluations": [
                {
                    "name": e.name,
                    "weight": e.weight,
                    "score": round(e.score, 4),
                    "points_awarded": e.points_awarded,
                    "points_possible": e.points_possible,
                }
                for e in grader_result.evaluations
            ],
        }

    metrics_summary: dict[str, Any] | None = None
    if metrics_record is not None:
        metrics_summary = {
            k: metrics_record.get(k)
            for k in (
                "cost_usd",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "llm_responses",
                "agent_wallclock_s",
                "agent_wallclock_events_span_s",
                "agent_wallclock_method",
                "total_wallclock_s",
            )
        }

    return {
        "trial_id": record.trial_id,
        "agent_id": agent.id,
        "task_id": task.id,
        "trial_number": record.trial_number,
        "status": record.state,
        "error": record.error,
        "dtu_id": record.dtu_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "total_wallclock_s": record.elapsed_s,
        "full_trial_wallclock_s": full_trial_wallclock_s,
        "score": score,
        "metrics": metrics_summary,
        "agent_wallclock": agent_wc_details,
        "ai_user": record.ai_user,
        "extraction": {
            "status": (record.extractor or {}).get("status"),
            "manifest_path": str(manifest_path) if manifest_path else None,
            "extracted_file_count": len(extracted_files),
            "workspace_paths": extraction.workspace_paths if extraction else [],
            "session_dirs": extraction.session_dirs if extraction else [],
        },
        "answer": _read_extracted_answer(trial_dir, task),
        "paths": {
            "trial_dir": str(trial_dir),
            "state_json": str(trial_dir / STATE_FILENAME),
            "trial_result_json": str(trial_dir / TRIAL_RESULT_FILENAME),
            "metrics_json": str(trial_dir / "metrics.json"),
            "extraction_dir": str(trial_dir / "extracted"),
            "extraction_manifest": str(manifest_path) if manifest_path else None,
            "grader_dir": str(trial_dir / "grader"),
            "grader_result_json": str(trial_dir / "grader_result.json"),
            "ai_user_transcript": str(trial_dir / "ai_user_transcript.txt"),
            "install_log": str(trial_dir / "install.log"),
        },
        "history": [asdict(h) for h in record.history],
    }


def extracted_dir_manifest(trial_dir: Path) -> Path | None:
    """Return the extraction manifest.json path if the extractor wrote one."""
    candidate = trial_dir / "extracted" / "manifest.json"
    return candidate if candidate.is_file() else None


def _list_files(root: Path) -> list[Path]:
    """Every file under `root` (empty when the dir is absent)."""
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def _read_extracted_answer(trial_dir: Path, task: TaskSpec) -> str | None:
    """Best-effort read of the agent's deliverable from the extracted workspace.

    The extractor pulls /workspace (including the answer file) under
    extracted/workspace/. We locate the deliverable by basename so the record
    can carry the actual answer text for a human sanity check.
    """
    basename = Path(task.deliverable.path).name
    ws_root = trial_dir / "extracted"
    if not ws_root.is_dir():
        return None
    for candidate in sorted(ws_root.rglob(basename)):
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8").strip()
            except OSError:
                return None
    return None


def _record_interaction(interaction: InteractionResult, trial_dir: Path) -> None:
    """Persist the AI User transcript summary + result JSON next to the trial."""
    summary = {
        "scenario_present": bool(interaction.scenario),
        "dtu_id": interaction.dtu_id,
        "ai_user_session_id": interaction.ai_user_session_id,
        "elapsed_s": interaction.elapsed_s,
        "timed_out": interaction.timed_out,
        "conclude": None
        if interaction.conclude is None
        else {
            "verdict": interaction.conclude.verdict,
            "reasoning": interaction.conclude.reasoning,
            "summary": interaction.conclude.summary,
        },
        "final_assistant_text": interaction.final_assistant_text,
    }
    (trial_dir / "interaction.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    # Also drop the final assistant text as a readable transcript pointer.
    (trial_dir / "ai_user_transcript.txt").write_text(
        interaction.final_assistant_text.strip() + "\n", encoding="utf-8"
    )


__all__ = [
    "run_trial",
    "compute_agent_wallclock",
    "TRIAL_RESULT_FILENAME",
    "STATE_FILENAME",
]


# NOTE re NOT_AVAILABLE: imported so callers/readers of this module see the
# metrics sentinel used in the record; referenced to keep linters honest.
_ = NOT_AVAILABLE
