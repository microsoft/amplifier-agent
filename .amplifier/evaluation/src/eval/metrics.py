"""Parse extracted Amplifier events.jsonl and emit one normalized metrics.json.

This is the light-code metrics pass. The agent-driven Extractor
(`eval.extractor`) already did the fuzzy part: it pulled the agent-under-test's
`events.jsonl` session logs (and deliverables) out of the DTU onto the host.
This module reads those already-extracted files and computes token/cost/wallclock
figures deterministically -- no LLM, no guessing.

Three agent stacks are supported, routed by the agent's own `extract.yaml`
hints (see `metrics_source_type`). Two of them emit Amplifier-format
`events.jsonl` (one JSON object per line) with slightly different shapes; the
third (vanilla opencode) has NO events.jsonl and instead records usage in a
SQLite database:

- amplifier-agent (Rust, the opencode-amplifier-agent stack): `events.jsonl`
  `data.usage` carries `input_tokens` / `output_tokens` / `cache_read_tokens` /
  `cache_write_tokens` and a per-response `cost_usd`; time is an ISO-8601
  `timestamp` (nanoseconds).
- amplifier-foundation (Python CLI, via foundation's hooks-logging):
  `events.jsonl` `data.usage` carries `input` / `output` / `cache_read` /
  `cache_write` and time is an ISO-8601 `ts`. Whether it carries `cost_usd` is
  provider-dependent; this module captures it IF a real cost field is present
  and reports `not_available` otherwise (never a fabricated 0).
- opencode-vanilla: NO events.jsonl. Per-session token usage and cost live in
  the opencode SQLite db `opencode.db` (WAL mode) at
  /root/.local/share/opencode. The `session` table aggregates
  tokens_input/tokens_output/tokens_cache_read/tokens_cache_write/cost with a
  `directory` and epoch-MILLISECONDS time_created/time_updated; the `message`
  table holds per-turn rows (role="assistant") for the llm_responses count. The
  install-time warm-up session (directory /root) is EXCLUDED; only the task
  session (directory /workspace) is counted.

The events.jsonl parser is "dual-shape aware" (handles both the `_tokens`
suffixed and the bare token key names). The SQLite parser is a separate branch.

`not_available` discipline (never fabricate): every normalized field is either a
real number or the exact string `"not_available"`. A source that is genuinely
absent -- no events pulled, or a runtime that does not emit `cost_usd` -- is
reported as `not_available`, never as a silent 0.

Wallclock caveat: clean agent-only timing (timing the
in-DTU agent command) is not yet wired. For now `agent_wallclock_s` is
the span between the earliest and latest event timestamps in the pulled logs,
and `total_wallclock_s` is the whole-trial elapsed measured by the harness. The
events-span figure is a floor on true agent time, not the exact command time;
this limitation is recorded in the metrics.json `notes`.

Standalone use:
    python metrics.py '<glob-or-path-to-events.jsonl>'
"""

from __future__ import annotations

import datetime
import glob
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# The normalized, agent-agnostic efficiency schema. Every field must appear in
# metrics.json as a number or the NOT_AVAILABLE marker.
NOT_AVAILABLE = "not_available"

METRIC_FIELDS = (
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read",
    "cache_write",
    "total_tokens",
    "llm_responses",
    "agent_wallclock_s",
    "total_wallclock_s",
)


def _parse_iso(tstr: str) -> float | None:
    """Parse an ISO-8601 string to epoch seconds, trimming sub-microsecond fraction.

    Handles both the amplifier-agent `timestamp` (nanoseconds, e.g.
    "2026-07-07T17:22:40.591486782+00:00") and the Python amplifier `ts` (e.g.
    "2026-02-05T22:33:33.323+00:00"). datetime.fromisoformat only accepts up to
    microseconds, so the fraction is trimmed to '.' + 6 digits.
    """
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", tstr)
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = frac[:7] if frac else ""  # '.' + up to 6 digits
    try:
        return datetime.datetime.fromisoformat(f"{base}{frac}{tz}").timestamp()
    except ValueError:
        return None


def _event_epoch(obj: dict) -> float | None:
    """Return an event's time as epoch seconds.

    Reads whichever time field is present -- amplifier-agent uses `timestamp`,
    the Python amplifier hooks-logging format uses `ts`. Either may be a numeric
    epoch or an ISO-8601 string, so both encodings are handled.
    """
    for key in ("ts", "timestamp"):
        val = obj.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            epoch = _parse_iso(val)
            if epoch is not None:
                return epoch
    return None


def _pick(usage: dict, *keys: str) -> object:
    """Return the first present key from `usage`, else None.

    Providers disagree on token field names: the amplifier-agent stack emits
    `input_tokens`/`cache_read_tokens`; the Python Anthropic provider emits
    `input`/`cache_read`. Try the `_tokens` name first, then the bare name.
    """
    for key in keys:
        if key in usage:
            return usage[key]
    return None


def _to_int(value: object) -> int:
    """Coerce a usage field to int; missing/malformed -> 0."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float:
    """Coerce a cost field (often a string like '0.01867525') to float; else 0.0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _ms_to_epoch(value: object) -> float | None:
    """Coerce an opencode timestamp (epoch milliseconds) to epoch seconds.

    opencode stores `time_created`/`time_updated` as epoch MILLISECONDS. A value
    above 1e12 is unambiguously ms (year ~2001+ in seconds is ~1e9), so divide;
    smaller values are treated as already-seconds defensively.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v / 1000.0 if v > 1e12 else v


def _opencode_sessions(db_path: str, workspace_dir: str) -> tuple[list[dict] | None, int]:
    """Read per-session usage rows from one opencode SQLite db (`opencode.db`).

    Returns (sessions, assistant_message_count), or (None, 0) when the file is
    not a usable opencode db (unreadable, or missing the expected `session`
    columns). The db is opened READ-ONLY (`mode=ro`); the WAL sidecar
    (`opencode.db-wal`) must be co-located for the newest writes to be visible.

    Only sessions whose `directory == workspace_dir` are returned, which excludes
    the install-time warm-up run (it executes from /root, not the task
    workspace). If no session matches, all sessions are returned defensively so a
    real run is never silently dropped.
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, 0
    try:
        con.row_factory = sqlite3.Row
        cols = {row[1] for row in con.execute("PRAGMA table_info(session)")}
        if not {"tokens_input", "tokens_output", "cost", "directory"}.issubset(cols):
            return None, 0
        sessions = [dict(r) for r in con.execute("SELECT * FROM session")]
        matched = [s for s in sessions if s.get("directory") == workspace_dir] or sessions

        ids = {s.get("id") for s in matched}
        assistant = 0
        try:
            for sid, data in con.execute("SELECT session_id, data FROM message"):
                if sid not in ids:
                    continue
                try:
                    md = json.loads(data)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if isinstance(md, dict) and md.get("role") == "assistant":
                    assistant += 1
        except sqlite3.Error:
            assistant = 0
        return matched, assistant
    except sqlite3.Error:
        return None, 0
    finally:
        con.close()


def parse_opencode_db(db_paths: list[str], workspace_dir: str = "/workspace") -> dict[str, Any]:
    """Sum token/cost usage for a vanilla opencode run from its SQLite db(s).

    Vanilla opencode has NO amplifier events.jsonl; the `session` table in
    `opencode.db` already aggregates per-session usage (tokens_input/output/
    cache_read/cache_write, cost) with a `directory` and epoch-ms
    time_created/time_updated. llm_responses is the count of assistant rows in
    the `message` table for the task session.

    Returns the SAME dict shape as `parse_events` (input_tokens, output_tokens,
    cache_read_tokens, cache_write_tokens, total_tokens, cost_usd,
    cost_from_events, llm_responses, files_read, had_timestamps,
    agent_wallclock_s) so `_finalize` can treat both branches uniformly.
    `cost_from_events` is True when a real cost figure was read, so a genuine $0
    is not mistaken for a free run.

    Args:
        db_paths: Paths to extracted `opencode.db` files (the WAL sidecar must
            be co-located for completeness).
        workspace_dir: Session directory identifying the task run (default
            `/workspace`), used to exclude the install-time warm-up session.
    """
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    cost_usd = 0.0
    saw_cost = False
    llm_responses = 0
    files_read = 0

    min_ts: float | None = None
    max_ts: float | None = None

    for db_path in db_paths:
        sessions, assistant = _opencode_sessions(db_path, workspace_dir)
        if sessions is None:
            continue
        files_read += 1
        # Prefer the true assistant-turn count; fall back to session count so a
        # run with clear usage is never reported as 0 responses.
        llm_responses += assistant or len(sessions)
        for s in sessions:
            input_tokens += _to_int(s.get("tokens_input"))
            output_tokens += _to_int(s.get("tokens_output"))
            cache_read_tokens += _to_int(s.get("tokens_cache_read"))
            cache_write_tokens += _to_int(s.get("tokens_cache_write"))
            if s.get("cost") is not None:
                saw_cost = True
                cost_usd += _to_float(s.get("cost"))
            for key in ("time_created", "time_updated"):
                ep = _ms_to_epoch(s.get(key))
                if ep is not None:
                    min_ts = ep if min_ts is None else min(min_ts, ep)
                    max_ts = ep if max_ts is None else max(max_ts, ep)

    had_timestamps = min_ts is not None and max_ts is not None
    agent_wallclock_s = (max_ts - min_ts) if had_timestamps else 0.0

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
        "cost_from_events": saw_cost,
        "llm_responses": llm_responses,
        "files_read": files_read,
        "had_timestamps": had_timestamps,
        "agent_wallclock_s": agent_wallclock_s,
    }


def parse_events(events_paths: list[str]) -> dict[str, Any]:
    """Sum token/cost usage and compute wallclock from Amplifier events.jsonl.

    Dual-shape aware (see the module docstring). Malformed lines and unreadable
    files are skipped defensively; a run is never silently dropped because one
    line failed to parse.

    Args:
        events_paths: Paths to events.jsonl files (one JSON object per line).

    Returns:
        dict with keys: input_tokens, output_tokens, cache_read_tokens,
        cache_write_tokens, total_tokens, cost_usd, cost_from_events (bool,
        True only if a real cost field was seen), llm_responses, files_read
        (count of files that existed and were readable), had_timestamps (bool),
        agent_wallclock_s (float, 0.0 when no timestamps were found -- callers
        must consult had_timestamps to distinguish that from a genuine 0-length
        span).
    """
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    cost_usd = 0.0
    saw_cost = False
    llm_responses = 0
    files_read = 0

    min_ts: float | None = None
    max_ts: float | None = None

    for path in events_paths:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        files_read += 1

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue

            # Track wallclock across ALL events that carry a usable time field
            # (numeric `ts` or ISO-8601 `timestamp`).
            epoch = _event_epoch(obj)
            if epoch is not None:
                min_ts = epoch if min_ts is None else min(min_ts, epoch)
                max_ts = epoch if max_ts is None else max(max_ts, epoch)

            if obj.get("event") != "llm:response":
                continue

            data = obj.get("data")
            usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                # Still count the response even if usage is absent.
                llm_responses += 1
                continue

            llm_responses += 1
            # Field names differ by runtime/provider: amplifier-agent emits the
            # `_tokens`-suffixed names, the Python Anthropic provider emits the
            # bare names. Accept either.
            input_tokens += _to_int(_pick(usage, "input_tokens", "input"))
            output_tokens += _to_int(_pick(usage, "output_tokens", "output"))
            cache_read_tokens += _to_int(_pick(usage, "cache_read_tokens", "cache_read"))
            cache_write_tokens += _to_int(_pick(usage, "cache_write_tokens", "cache_write"))
            # cost_usd is only emitted by the amplifier-agent stack. Track
            # whether we ever saw it so a $0 from a stack that does not record
            # cost is not reported as a real, free run.
            if "cost_usd" in usage:
                saw_cost = True
                cost_usd += _to_float(usage.get("cost_usd"))

    had_timestamps = min_ts is not None and max_ts is not None
    agent_wallclock_s = (max_ts - min_ts) if had_timestamps else 0.0

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
        "cost_from_events": saw_cost,
        "llm_responses": llm_responses,
        "files_read": files_read,
        "had_timestamps": had_timestamps,
        "agent_wallclock_s": agent_wallclock_s,
    }


def find_events_files(output_dir: Path | str) -> list[str]:
    """Return all extracted `events.jsonl` paths under a run's output dir.

    The extractor pulls session logs under `output_dir/sessions/`, preserving
    each session's `context-intelligence/events.jsonl`. We glob the whole tree
    so nested session directories are all found, sorted for determinism.
    """
    root = Path(output_dir).expanduser().resolve()
    return sorted(str(p) for p in root.rglob("events.jsonl"))


def find_opencode_db_files(output_dir: Path | str) -> list[str]:
    """Return all extracted `opencode.db` paths under a run's output dir.

    Globs for the main db file only (not the `-wal`/`-shm` sidecars); sqlite3
    reads a co-located WAL automatically, and the extractor preserves the
    directory layout so the sidecars land next to the db. Sorted for
    determinism.
    """
    root = Path(output_dir).expanduser().resolve()
    return sorted(str(p) for p in root.rglob("opencode.db"))


def normalize_metrics(
    events_paths: list[str],
    total_wallclock_s: float | None,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Produce the normalized metrics.json record from extracted events.

    Applies the `not_available` discipline:
    - If no events file was readable, every token/cost/response/agent-wallclock
      field is `"not_available"` (the source is genuinely absent).
    - `cost_usd` is `"not_available"` when no event carried a `cost_usd` field
      (e.g. the Python amplifier stack), never a fabricated 0.
    - `agent_wallclock_s` is `"not_available"` when no event timestamp was
      found; otherwise it is the events-span floor (see module docstring).
    - `total_wallclock_s` is the harness-measured whole-trial elapsed, or
      `"not_available"` if the caller did not measure it.

    Args:
        events_paths: Paths to extracted events.jsonl files.
        total_wallclock_s: Whole-trial elapsed seconds measured by the harness.
        source: Optional identifier for the agent/stack (e.g. the agent id).

    Returns:
        A JSON-safe dict with every METRIC_FIELDS key present, plus `notes`,
        `source`, and `events_files` (the files this record was computed from).
    """
    parsed = parse_events(events_paths)
    return _finalize(
        parsed,
        total_wallclock_s,
        source=source,
        source_files=events_paths,
        source_label="events.jsonl file",
        response_label="llm:response event",
        no_source_note=(
            "No events.jsonl files were readable under the extraction output dir; "
            "all token/cost/response/agent-wallclock fields are not_available."
        ),
        cost_absent_note="cost_usd is not_available: no event carried a cost_usd field.",
        cost_present_note="cost_usd summed from per-response cost_usd fields.",
        wallclock_note=(
            "agent_wallclock_s is the earliest-to-latest event timestamp span "
            "(a floor on true agent time; exact agent-command timing is not yet "
            "wired)."
        ),
        parse_note_suffix="Token keys read with dual-shape fallback (input_tokens/input, etc.).",
    )


def normalize_opencode_metrics(
    db_paths: list[str],
    total_wallclock_s: float | None,
    *,
    source: str | None = None,
    workspace_dir: str = "/workspace",
) -> dict[str, Any]:
    """Produce the normalized metrics.json record from a vanilla opencode db.

    Same `not_available` discipline and output schema as `normalize_metrics`,
    but the token/cost source is the opencode SQLite `session` table rather than
    Amplifier events.jsonl. See `parse_opencode_db`.
    """
    parsed = parse_opencode_db(db_paths, workspace_dir=workspace_dir)
    return _finalize(
        parsed,
        total_wallclock_s,
        source=source,
        source_files=db_paths,
        source_label="opencode.db file",
        response_label="assistant message",
        no_source_note=(
            "No usable opencode.db was readable under the extraction output dir; "
            "all token/cost/response/agent-wallclock fields are not_available."
        ),
        cost_absent_note="cost_usd is not_available: the opencode session carried no cost.",
        cost_present_note="cost_usd summed from the opencode session table `cost` column.",
        wallclock_note=(
            "agent_wallclock_s is the task session's time_created-to-time_updated span "
            "(opencode epoch-ms timestamps; a floor on true agent time)."
        ),
        parse_note_suffix=(
            f"Only the task session (directory {workspace_dir}) was counted; the "
            "install-time warm-up session was excluded."
        ),
    )


def _finalize(
    parsed: dict[str, Any],
    total_wallclock_s: float | None,
    *,
    source: str | None,
    source_files: list[str],
    source_label: str,
    response_label: str,
    no_source_note: str,
    cost_absent_note: str,
    cost_present_note: str,
    wallclock_note: str,
    parse_note_suffix: str,
) -> dict[str, Any]:
    """Turn a parser's `parsed` dict into the normalized metrics.json record.

    Shared by both the events.jsonl and opencode.db branches: they produce the
    same `parsed` shape, so the `not_available` discipline, rounding, and note
    assembly live here once. Branch-specific wording is passed in.
    """
    files_read = parsed["files_read"]
    notes_parts: list[str] = []

    if files_read == 0:
        # No session sources were pulled: everything derived from them is absent.
        record: dict[str, Any] = {name: NOT_AVAILABLE for name in METRIC_FIELDS}
        notes_parts.append(no_source_note)
    else:
        cost_val: Any = parsed["cost_usd"] if parsed["cost_from_events"] else NOT_AVAILABLE
        agent_wc: Any = parsed["agent_wallclock_s"] if parsed["had_timestamps"] else NOT_AVAILABLE
        record = {
            "cost_usd": cost_val,
            "input_tokens": parsed["input_tokens"],
            "output_tokens": parsed["output_tokens"],
            "cache_read": parsed["cache_read_tokens"],
            "cache_write": parsed["cache_write_tokens"],
            "total_tokens": parsed["total_tokens"],
            "llm_responses": parsed["llm_responses"],
            "agent_wallclock_s": agent_wc,
            "total_wallclock_s": NOT_AVAILABLE,
        }
        notes_parts.append(
            f"Parsed {parsed['llm_responses']} {response_label}(s) across "
            f"{files_read} {source_label}(s). {parse_note_suffix}"
        )
        notes_parts.append(cost_absent_note if cost_val == NOT_AVAILABLE else cost_present_note)
        if agent_wc == NOT_AVAILABLE:
            notes_parts.append("agent_wallclock_s is not_available: no timestamps found.")
        else:
            notes_parts.append(wallclock_note)

    if total_wallclock_s is not None:
        record["total_wallclock_s"] = round(float(total_wallclock_s), 3)
    else:
        record["total_wallclock_s"] = NOT_AVAILABLE
        notes_parts.append("total_wallclock_s not measured by the caller.")

    # Round the span/cost figures for readability when they are numbers.
    if isinstance(record["agent_wallclock_s"], (int, float)):
        record["agent_wallclock_s"] = round(float(record["agent_wallclock_s"]), 3)
    if isinstance(record["cost_usd"], (int, float)):
        record["cost_usd"] = round(float(record["cost_usd"]), 6)

    record["source"] = source
    record["events_files"] = list(source_files)
    record["notes"] = " ".join(notes_parts)
    return record


def metrics_source_type(extract: dict[str, Any] | None) -> str:
    """Route the metrics parser from the agent's own extract.yaml hints.

    Returns `"opencode_db"` when the agent declares an opencode SQLite source
    (a `session_data` entry keyed `*opencode_db*`, or one whose `glob` names
    `opencode.db`), else `"events_jsonl"`. This keeps the branch selection
    data-driven -- no agent id is hardcoded; the agent declares where its
    metrics live.
    """
    if not isinstance(extract, dict):
        return "events_jsonl"
    session_data = extract.get("session_data")
    if isinstance(session_data, dict):
        for key, val in session_data.items():
            if "opencode_db" in str(key):
                return "opencode_db"
            if isinstance(val, dict) and "opencode.db" in str(val.get("glob", "")):
                return "opencode_db"
    return "events_jsonl"


def _path_has_any_id(path: str, session_ids: list[str]) -> bool:
    """True if `path` contains any of the given session ids as a substring.

    Extracted seeded sessions land at
    `.../sessions/<session-id>/context-intelligence/events.jsonl`, so the session
    id is a path component. Session ids are unique, so a substring test is a
    reliable, deterministic way to attribute an events file to a seeded session.
    """
    return any(sid and sid in path for sid in session_ids)


_RAW_METRIC_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read",
    "cache_write",
    "total_tokens",
    "cost_usd",
    "llm_responses",
    "agent_wallclock_s",
)


def build_metrics(
    extract: dict[str, Any] | None,
    extracted_dir: Path | str,
    total_wallclock_s: float | None,
    *,
    source: str | None = None,
    exclude_session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Find the right source files and normalize, routed by extract.yaml.

    Dispatches on `metrics_source_type`: opencode-vanilla -> opencode.db SQLite
    branch; every other agent -> events.jsonl branch. The workspace directory
    used to exclude the opencode warm-up session is read from the agent's
    `extract.yaml` `workspace.path` (default `/workspace`).

    Decontamination: when `exclude_session_ids` is given, any extracted
    `events.jsonl` belonging to a seeded prior session is DROPPED before token /
    cost / wallclock are computed, so the headline metrics reflect ONLY the trial
    and are not inflated by the seeded prior-session events. The pre-drop `raw`
    figures are retained under `raw_metrics` for a raw-vs-decontaminated
    comparison, and `decontaminated`/`seeded_session_ids`/`dropped_events_files`
    record what happened. For the opencode.db branch the seeded amplifier
    sessions are not part of the token/cost source at all, so decontamination is
    a no-op there and is recorded as such.
    """
    root = Path(extracted_dir).expanduser()
    exclude = list(exclude_session_ids or [])

    if metrics_source_type(extract) == "opencode_db":
        db_paths = find_opencode_db_files(root) if root.is_dir() else []
        workspace = "/workspace"
        ws = (extract or {}).get("workspace")
        if isinstance(ws, dict) and isinstance(ws.get("path"), str):
            workspace = ws["path"]
        record = normalize_opencode_metrics(
            db_paths, total_wallclock_s, source=source, workspace_dir=workspace
        )
        if exclude:
            record["decontaminated"] = True
            record["seeded_session_ids"] = exclude
            record["dropped_events_files"] = []
            record["notes"] = (
                record.get("notes", "")
                + " Decontamination no-op for this agent: its token/cost source is the "
                "opencode.db (only the /workspace task session), which does not include the "
                "seeded amplifier-agent prior sessions, so no seeded events could inflate it."
            )
        return record

    events_files = find_events_files(root) if root.is_dir() else []
    if not exclude:
        return normalize_metrics(events_files, total_wallclock_s=total_wallclock_s, source=source)

    kept = [p for p in events_files if not _path_has_any_id(p, exclude)]
    dropped = [p for p in events_files if _path_has_any_id(p, exclude)]

    # Raw = INCLUDING the seeded prior-session events (what an un-decontaminated
    # pass would report). Clean = the headline, EXCLUDING them.
    raw = normalize_metrics(events_files, total_wallclock_s=total_wallclock_s, source=source)
    clean = normalize_metrics(kept, total_wallclock_s=total_wallclock_s, source=source)

    clean["decontaminated"] = True
    clean["seeded_session_ids"] = exclude
    clean["dropped_events_files"] = dropped
    clean["raw_metrics"] = {k: raw.get(k) for k in _RAW_METRIC_KEYS}
    clean["notes"] = (
        clean.get("notes", "")
        + f" DECONTAMINATED: dropped {len(dropped)} seeded events.jsonl file(s) belonging "
        f"to prior session(s) {exclude} before computing metrics, so these headline figures "
        "reflect only the trial. The pre-drop figures are under raw_metrics for comparison."
    )
    return clean


def _is_number(value: Any) -> bool:
    """True for a real int/float (not bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_metrics(record: dict[str, Any]) -> list[str]:
    """Validate a normalized metrics record. Returns errors (empty if OK).

    Every field in METRIC_FIELDS must be present and be a non-negative number
    OR exactly `not_available`; `notes` must be non-empty; and when
    input/output/total tokens are all numeric they must be arithmetically
    consistent.
    """
    errors: list[str] = []

    if not record.get("notes") or not str(record.get("notes")).strip():
        errors.append("notes must be a non-empty string explaining what was computed")

    for name in METRIC_FIELDS:
        if name not in record:
            errors.append(
                f"{name} is required: provide a number, or the string "
                f"'{NOT_AVAILABLE}' if it cannot be determined"
            )
            continue
        value = record[name]
        if isinstance(value, str):
            if value != NOT_AVAILABLE:
                errors.append(f"{name}={value!r} must be a number or exactly '{NOT_AVAILABLE}'")
        elif isinstance(value, bool):
            errors.append(f"{name} must be a number, not a boolean")
        elif _is_number(value):
            if value < 0:
                errors.append(f"{name}={value} must be non-negative")
        else:
            errors.append(f"{name} must be a number or '{NOT_AVAILABLE}'")

    it = record.get("input_tokens")
    ot = record.get("output_tokens")
    tt = record.get("total_tokens")
    if _is_number(it) and _is_number(ot) and _is_number(tt) and abs(tt - (it + ot)) > 0.5:
        errors.append(
            f"total_tokens={tt} must equal input_tokens+output_tokens ({it}+{ot}={it + ot})"
        )

    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python metrics.py '<glob-or-path-to-events.jsonl>'", file=sys.stderr)
        sys.exit(2)

    pattern = sys.argv[1]
    paths = glob.glob(pattern, recursive=True)
    if not paths:
        # Treat the argument as a literal path if the glob matched nothing.
        paths = [pattern]

    out = normalize_metrics(paths, total_wallclock_s=None, source="cli")
    print(json.dumps(out, indent=2))
    problems = validate_metrics(out)
    if problems:
        print("VALIDATION ERRORS:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)
