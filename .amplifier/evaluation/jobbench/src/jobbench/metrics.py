# Vendored from ../deep-swe/src/deepswe_agents/metrics.py. Stdlib-only.
# Kept byte-identical to that copy so a fix in either place ports cleanly;
# do not fork the logic here.
#
# The token-accounting normalization documented in the module docstring below
# was developed here and ported back upstream, so both harnesses now share it.
# Its short version: the two token sources disagree on what "input" means, so
# `input_tokens` is normalized to fresh-only in BOTH branches and
# `total_tokens` is the sum of four disjoint fields.

"""Parse extracted session logs and emit one normalized metrics.json.

Forked from an earlier internal evaluation harness and since diverged; this
copy is the source of truth for deep-swe.

Two token/cost sources are supported, and both produce the same record shape:

- Amplifier `events.jsonl` (one JSON object per line), in either of the two
  envelope shapes the runtimes emit (`input_tokens`/`timestamp` or the bare
  `input`/`ts` names). A single call is written to disk once per logging hook
  the session composes, so `parse_events` de-duplicates by response identity.
- Vanilla opencode, which has NO events.jsonl and records per-session usage in
  the SQLite `opencode.db`. Only the session whose `directory` matches this
  harness's workdir (`/app`) is counted, excluding the install-time warm-up
  session, and cost is recomputed from `MODEL_RATES_PER_M` (see
  `parse_opencode_db` for why).

`not_available` discipline (never fabricate): every normalized field is either
a real number or the exact string `"not_available"` -- never a silent 0.

TOKEN ACCOUNTING. Every token field means exactly one thing, in both branches,
and the four are disjoint so they add up:

    input_tokens        fresh input only, never previously cached
    cache_read_tokens   input served from cache
    cache_write_tokens  input written into cache
    output_tokens       generated output
    total_tokens        the sum of all four: every token actually processed

Reaching that required normalizing the two sources, which do NOT agree on what
"input" means:

- opencode's `session.tokens_input` column is already fresh-only. Verified on a
  real run: `tokens_input=322` alongside `tokens_cache_read=19,299,708`.
- The amplifier stacks fold cache_read INTO their reported `input_tokens` (but
  not cache_write), so `parse_events` subtracts it back out. Verified across
  114 events of a real run: 0/114 had input < cache_read, while 114/114 had
  input < cache_read + cache_write -- e.g. `input=872` with `cache_read=0` and
  `cache_write=12354`, which only a fresh-plus-cache_read reading explains.

Why it matters: while `total_tokens` was `input + output`, an opencode trial
reported 95,147 against an amplifier trial's 1,218,757 on the same run -- an
apparent 12x gap that INVERTED the true ordering, since opencode had actually
processed ~19.6M tokens to amplifier's ~10.2M. The old figure silently dropped
opencode's entire 19.3M cache-read volume.

`cost_usd` is unaffected by any of this: it is priced from the raw per-source
counts against `MODEL_RATES_PER_M`, never derived from `total_tokens`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
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
    the Python amplifier hooks-logging format uses `ts`. Both emit ISO-8601.
    """
    for key in ("ts", "timestamp"):
        val = obj.get(key)
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
    """Coerce an opencode timestamp (epoch milliseconds) to epoch seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 1000.0


@dataclass
class _Usage:
    """Token/cost/timestamp accumulator shared by both parser branches.

    The two parsers read genuinely different sources (SQLite rows vs JSONL
    lines) but accumulate the same figures and must return the same dict shape,
    because `_finalize` treats the two branches uniformly. That common part
    lives here once; the reading bodies stay separate.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    saw_cost: bool = False
    llm_responses: int = 0
    files_read: int = 0
    min_ts: float | None = None
    max_ts: float | None = None
    #: Records where reported input was somehow smaller than cache_read, i.e.
    #: the source's convention is not what `parse_events` assumes. Surfaced in
    #: `notes` rather than swallowed, because the resulting figure is wrong.
    negative_fresh_input: int = 0

    def observe_time(self, epoch: float | None) -> None:
        """Widen the earliest-to-latest span with one timestamp; None is a no-op."""
        if epoch is None:
            return
        self.min_ts = epoch if self.min_ts is None else min(self.min_ts, epoch)
        self.max_ts = epoch if self.max_ts is None else max(self.max_ts, epoch)

    def as_dict(self) -> dict[str, Any]:
        """The keys `_finalize` reads, identical for both branches.

        `agent_wallclock_s` is 0.0 when no timestamp was seen -- callers must
        consult `had_timestamps` to tell that apart from a genuine 0-length run.
        """
        lo, hi = self.min_ts, self.max_ts
        had_timestamps = lo is not None and hi is not None
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            # Every token the model actually processed. `input_tokens` is
            # fresh-only in both branches by construction, so cache_read and
            # cache_write are additive here and nothing is double-counted.
            "total_tokens": (
                self.input_tokens
                + self.cache_read_tokens
                + self.cache_write_tokens
                + self.output_tokens
            ),
            "negative_fresh_input": self.negative_fresh_input,
            "cost_usd": self.cost_usd,
            "cost_from_events": self.saw_cost,
            "llm_responses": self.llm_responses,
            "files_read": self.files_read,
            "had_timestamps": had_timestamps,
            "agent_wallclock_s": (hi - lo) if lo is not None and hi is not None else 0.0,
        }


# ---------------------------------------------------------------------------
# Reference rate card
# ---------------------------------------------------------------------------

#: USD per 1M tokens, keyed by model id. THE single rate card for this harness.
#:
#: Mirrors the upstream provider cost tables that stamp `cost_usd` into the
#: amplifier arms' events.jsonl -- now TWO of them, one per provider family:
#: `_RATES` in amplifier-module-provider-anthropic/_cost.py for the claude-*
#: rows, and the short-context rates in amplifier-module-provider-openai/
#: _cost.py:102-107 for the gpt-* rows. Arms are only comparable if every
#: dollar figure comes from the same card, so this is also what the opencode
#: arm's cost is RECOMPUTED with -- see `compute_cost_from_tokens` and the WHY
#: in `parse_opencode_db`.
#:
#: All four keys are indexed unconditionally by `compute_cost_from_tokens`, so
#: a row missing one is a KeyError, not a silent zero.
#:
#: `opencode_vanilla.py` imports this to populate the model's `cost` block in
#: opencode.json. One definition, one home.
#:
#: CAVEAT (gpt-5.6-terra): upstream re-rates that model above 272K input
#: tokens; this flat card cannot express a threshold, so it always applies the
#: short-context rate. The amplifier arms are unaffected -- their cost comes
#: from provider events, computed upstream with the real tiering. Only the
#: opencode arm is recomputed here, so ITS terra cost is a FLOOR, understated
#: for any session that crosses the threshold.
MODEL_RATES_PER_M: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "gpt-5.6-terra": {"input": 2.50, "output": 15.00, "cache_read": 0.25, "cache_write": 3.125},
}


def compute_cost_from_tokens(
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """USD for one call/session from token counts and the reference card.

    Returns None for an unrecognised model -- semantically distinct from 0.0
    (a genuinely free call). Callers must propagate that as `not_available`
    rather than as a free run.
    """
    rates = MODEL_RATES_PER_M.get(model or "")
    if rates is None:
        return None
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read_tokens * rates["cache_read"]
        + cache_write_tokens * rates["cache_write"]
    ) / 1_000_000.0


def _opencode_model_id(raw: Any) -> str | None:
    """Extract the bare model id from opencode's `session.model` column.

    Stored as a JSON object, e.g.
    ``{"id":"claude-sonnet-5","providerID":"anthropic","variant":"default"}``,
    which SQLite hands back as the raw JSON text.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(raw, dict):
        mid = raw.get("id")
        return mid if isinstance(mid, str) and mid else None
    return None


def _opencode_sessions(db_path: str, workspace_dir: str) -> tuple[list[dict] | None, int, int]:
    """Read per-session usage rows from one opencode SQLite db (`opencode.db`).

    Returns (sessions, assistant_message_count, total_session_count), or
    (None, 0, 0) when the file is not a usable opencode db (unreadable, or
    missing the expected `session` columns). The db is opened READ-ONLY
    (`mode=ro`); the WAL sidecar (`opencode.db-wal`) must be co-located for the
    newest writes to be visible.

    ONLY sessions whose `directory == workspace_dir` are returned. There is
    deliberately NO "if nothing matched, return everything" fallback.

    That fallback was worse than useless. `directory` is an absolute path, so a
    caller passing the wrong workspace matched nothing and silently summed
    EVERY session in the database -- including unrelated ones -- publishing a
    plausible-looking number that was wrong by orders of magnitude. An empty
    result surfaces as `not_available`, which is honest and fixable; a
    fabricated total is neither.
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, 0, 0
    try:
        con.row_factory = sqlite3.Row
        cols = {row[1] for row in con.execute("PRAGMA table_info(session)")}
        if not {"tokens_input", "tokens_output", "cost", "directory"}.issubset(cols):
            return None, 0, 0
        sessions = [dict(r) for r in con.execute("SELECT * FROM session")]
        matched = [s for s in sessions if s.get("directory") == workspace_dir]

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
        return matched, assistant, len(sessions)
    except sqlite3.Error:
        return None, 0, 0
    finally:
        con.close()


def parse_opencode_db(db_paths: list[str], workspace_dir: str) -> dict[str, Any]:
    """Sum token/cost usage for a vanilla opencode run from its SQLite db(s).

    Vanilla opencode has NO amplifier events.jsonl; the `session` table in
    `opencode.db` already aggregates per-session usage (tokens_input/output/
    cache_read/cache_write, cost) with a `directory` and epoch-ms
    time_created/time_updated. llm_responses is the count of assistant rows in
    the `message` table for the task session.

    Returns the SAME dict shape as `parse_events` (see `_Usage.as_dict`) plus
    `extra_notes`, so `_finalize` can treat both branches uniformly.
    `cost_from_events` is True when a real cost figure was produced, so a
    genuine $0 is not mistaken for a free run.

    COST IS RECOMPUTED, NOT READ. The `session.cost` column is deliberately
    ignored for the published figure and reported only as an audit note.

    Why: opencode prices calls from a rate card it fetches from models.dev, and
    that card is NOT the one this harness bills against -- it diverges on the
    cache rates, and opencode ignores the explicit `cost.cache` override baked
    into opencode.json. Comparing arms priced from two different cards is a
    silent error: both numbers look plausible and neither is flagged. The token
    counts are ground truth from the agent; only the multiplication belongs to
    the harness, so cost is recomputed from `MODEL_RATES_PER_M`.

    Args:
        db_paths: Paths to extracted `opencode.db` files (the WAL sidecar must
            be co-located for completeness).
        workspace_dir: Session directory identifying the task run, used to
            exclude the install-time warm-up session. Required: a wrong value
            silently matches nothing, so there is no safe default.
    """
    usage = _Usage()
    extra_notes: list[str] = []

    for db_path in db_paths:
        sessions, assistant, total_sessions = _opencode_sessions(db_path, workspace_dir)
        if sessions is None:
            continue
        if not sessions:
            # The db is a valid opencode db but holds no session for this
            # workspace. Report it; do NOT substitute the other sessions.
            extra_notes.append(
                f"{Path(db_path).name} held {total_sessions} session(s), none with "
                f"directory == {workspace_dir!r}; contributed nothing. If the agent "
                f"really ran, the workspace_dir passed to the parser is wrong."
            )
            continue
        usage.files_read += 1
        # Prefer the true assistant-turn count; fall back to session count so a
        # run with clear usage is never reported as 0 responses.
        usage.llm_responses += assistant or len(sessions)
        for s in sessions:
            # opencode's `tokens_input` column is already FRESH-ONLY (it
            # excludes both cache figures), which is the convention this module
            # normalizes to, so it is accumulated as-is. The amplifier branch
            # has to strip cache_read out to reach the same meaning; see
            # `parse_events`.
            s_in = _to_int(s.get("tokens_input"))
            s_out = _to_int(s.get("tokens_output"))
            s_cr = _to_int(s.get("tokens_cache_read"))
            s_cw = _to_int(s.get("tokens_cache_write"))
            usage.input_tokens += s_in
            usage.output_tokens += s_out
            usage.cache_read_tokens += s_cr
            usage.cache_write_tokens += s_cw

            model = _opencode_model_id(s.get("model"))
            recomputed = compute_cost_from_tokens(
                model,
                input_tokens=s_in,
                output_tokens=s_out,
                cache_read_tokens=s_cr,
                cache_write_tokens=s_cw,
            )
            reported = _to_float(s.get("cost"))
            if recomputed is None:
                # Unknown model => no rate card => no honest dollar figure.
                # NOT 0.0: the run was not free, we just cannot price it.
                extra_notes.append(
                    f"Model {model!r} is not in the reference rate card, so cost_usd is "
                    f"not_available for this session. opencode self-reported "
                    f"${reported:.6f}, which is priced from ITS card and is not "
                    f"comparable to the amplifier arms."
                )
            else:
                usage.saw_cost = True
                usage.cost_usd += recomputed
                # Always record the divergence. If the two ever agree this note
                # is the evidence; when they disagree it is the explanation.
                delta = recomputed - reported
                pct = (delta / recomputed * 100.0) if recomputed else 0.0
                extra_notes.append(
                    f"cost_usd RECOMPUTED from token counts at the reference rate card "
                    f"for {model}: ${recomputed:.6f}. opencode self-reported "
                    f"${reported:.6f} (delta ${delta:+.6f}, {pct:+.1f}%); its figure is "
                    f"priced from a models.dev card that differs on cache rates and is "
                    f"NOT used."
                )
            for key in ("time_created", "time_updated"):
                usage.observe_time(_ms_to_epoch(s.get(key)))

    return {**usage.as_dict(), "extra_notes": extra_notes}


def _response_identity(obj: dict) -> str | None:
    """Return a stable identity for an `llm:response` event, or None.

    WHY THIS EXISTS. A single logical LLM call is written to disk more than once
    whenever a session composes more than one logging hook. The published
    `anchors` bundle does exactly this: it includes BOTH
    `foundation:behaviors/logging` (hooks-logging -> `<session>/events.jsonl`)
    and `context-intelligence:behaviors/context-intelligence-logging`
    (hook-context-intelligence -> `<session>/context-intelligence/events.jsonl`).
    Both files are legitimate telemetry and both are pulled by extraction, so
    summing across them counted every call, token and dollar TWICE.

    The two copies are not detectable by comparing bytes: the loggers use
    different envelope shapes (`ts` vs `timestamp`, metadata at the top level vs
    nested under `data`), so the files share zero identical lines. They are only
    recognisable by what they DESCRIBE. Hence identity, not equality:

      strong    the provider's own response id (`data.raw.id`). Globally unique
                per call, present whenever raw payload capture is on.
      fallback  session id + event timestamp + usage, hashed. Used when raw
                capture is off. The timestamp is sub-microsecond and is byte
                identical across both loggers (verified on real run artifacts),
                so it discriminates distinct calls reliably.
      None      neither is available -- see the caller. We do NOT guess.

    Returning None on a weak signal is deliberate. Over-de-duplicating would
    silently UNDERSTATE cost, which is a worse failure than the overcount this
    function exists to fix: an inflated number invites scrutiny, a deflated one
    does not.
    """
    # Bind before narrowing: `x.get(k) if isinstance(x.get(k), dict) else {}`
    # calls get() twice, and the isinstance on the first call does not narrow
    # the second, so the result stays `Any | None`.
    raw_data = obj.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}

    raw_payload = data.get("raw")
    if isinstance(raw_payload, dict):
        rid = raw_payload.get("id")
        if isinstance(rid, str) and rid:
            return f"id:{rid}"

    # Fallback fingerprint. Require a timestamp: without one, two genuinely
    # distinct calls that happened to use the same token counts would collide.
    ts = data.get("timestamp") or obj.get("ts") or obj.get("timestamp")
    if not isinstance(ts, (str, int, float)) or isinstance(ts, bool):
        return None

    session_id = data.get("session_id") or obj.get("session_id")
    raw_usage = data.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    fingerprint = json.dumps([session_id, str(ts), usage], sort_keys=True, default=str)
    return "fp:" + hashlib.sha1(fingerprint.encode()).hexdigest()


def parse_events(events_paths: list[str]) -> dict[str, Any]:
    """Sum token/cost usage and compute wallclock from Amplifier events.jsonl.

    Dual-shape aware (see the module docstring). Malformed lines and unreadable
    files are skipped defensively; a run is never silently dropped because one
    line failed to parse.

    Args:
        events_paths: Paths to events.jsonl files (one JSON object per line).

    Returns:
        The keys listed in `_Usage.as_dict`, plus `duplicate_responses` (count
        of duplicate `llm:response` events dropped) and
        `unidentified_responses` (count that carried no usable identity and
        were therefore counted without de-duplication).
    """
    usage = _Usage()
    duplicate_responses = 0
    unidentified_responses = 0
    seen_responses: set[str] = set()

    for path in events_paths:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        usage.files_read += 1

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

            # Track wallclock across ALL events that carry a usable time field.
            usage.observe_time(_event_epoch(obj))

            if obj.get("event") != "llm:response":
                continue

            # De-duplicate by identity. The same call reaches this loop once per
            # logging hook the session composed (see _response_identity), and
            # summing every copy is what inflated cost, tokens and call counts.
            identity = _response_identity(obj)
            if identity is None:
                # No usable identity: count it rather than risk collapsing two
                # genuinely distinct calls. Surfaced in the notes.
                unidentified_responses += 1
            elif identity in seen_responses:
                duplicate_responses += 1
                continue
            else:
                seen_responses.add(identity)

            data = obj.get("data")
            event_usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(event_usage, dict):
                # Still count the response even if usage is absent.
                usage.llm_responses += 1
                continue

            usage.llm_responses += 1
            # Field names differ by runtime/provider: amplifier-agent emits the
            # `_tokens`-suffixed names, the Python Anthropic provider emits the
            # bare names. Accept either.
            reported_in = _to_int(_pick(event_usage, "input_tokens", "input"))
            ev_cache_read = _to_int(_pick(event_usage, "cache_read_tokens", "cache_read"))
            ev_cache_write = _to_int(_pick(event_usage, "cache_write_tokens", "cache_write"))
            # The amplifier stacks report an `input_tokens` that ALREADY
            # contains cache_read (but not cache_write), while opencode reports
            # a fresh-only figure. Strip cache_read here so `input_tokens` means
            # exactly one thing -- genuinely-new, never-cached input -- no
            # matter which source produced the record. Measured on a real run:
            # 114/114 events had input >= cache_read and input < cache_read +
            # cache_write, e.g. input=872 with cache_read=0, cache_write=12354.
            fresh_in = reported_in - ev_cache_read
            if fresh_in < 0:
                # The invariant above broke, so the assumption no longer holds
                # for this source. Clamp rather than emit a negative token
                # count, and say so: a silently wrong number is the failure
                # this whole normalization exists to prevent.
                usage.negative_fresh_input += 1
                fresh_in = 0
            usage.input_tokens += fresh_in
            usage.output_tokens += _to_int(_pick(event_usage, "output_tokens", "output"))
            usage.cache_read_tokens += ev_cache_read
            usage.cache_write_tokens += ev_cache_write
            # cost_usd is only emitted by the amplifier-agent stack. Track
            # whether we ever saw it so a $0 from a stack that does not record
            # cost is not reported as a real, free run.
            if "cost_usd" in event_usage:
                usage.saw_cost = True
                usage.cost_usd += _to_float(event_usage.get("cost_usd"))

    return {
        **usage.as_dict(),
        "duplicate_responses": duplicate_responses,
        "unidentified_responses": unidentified_responses,
    }


def _find(output_dir: Path | str, filename: str) -> list[str]:
    """Return every `filename` under a run's output dir, sorted for determinism.

    The extractor pulls session logs under `output_dir/sessions/`, preserving
    each session's own layout, so the whole tree is globbed: narrowing the glob
    to pick a "primary" file would be fragile, because the layout differs per
    agent and would silently yield zero on any change.

    For `events.jsonl` this deliberately returns EVERY file, including the
    several a single session produces when it composes more than one logging
    hook; the duplicates are handled where they can be handled correctly, by
    `parse_events` de-duplicating on response identity. For `opencode.db` it
    matches the main db only, not the `-wal`/`-shm` sidecars -- sqlite3 reads a
    co-located WAL automatically and the extractor preserves the layout, so the
    sidecars land next to the db.
    """
    root = Path(output_dir).expanduser().resolve()
    return sorted(str(p) for p in root.rglob(filename))


def find_events_files(output_dir: Path | str) -> list[str]:
    """Return all extracted `events.jsonl` paths under a run's output dir."""
    return _find(output_dir, "events.jsonl")


def find_opencode_db_files(output_dir: Path | str) -> list[str]:
    """Return all extracted `opencode.db` paths under a run's output dir."""
    return _find(output_dir, "opencode.db")


def normalize_metrics(events_paths: list[str], *, source: str | None = None) -> dict[str, Any]:
    """Produce the normalized metrics.json record from extracted events.

    Applies the `not_available` discipline:
    - If no events file was readable, every token/cost/response/agent-wallclock
      field is `"not_available"` (the source is genuinely absent).
    - `cost_usd` is `"not_available"` when no event carried a `cost_usd` field
      (e.g. the Python amplifier stack), never a fabricated 0.
    - `agent_wallclock_s` is `"not_available"` when no event timestamp was
      found; otherwise it is the earliest-to-latest event span, a floor on true
      agent time.

    Args:
        events_paths: Paths to extracted events.jsonl files.
        source: Optional identifier for the agent/stack (e.g. the agent id).

    Returns:
        A JSON-safe dict with every METRIC_FIELDS key present, plus `notes`,
        `source`, and `events_files` (the files this record was computed from).
    """
    parsed = parse_events(events_paths)
    files_read = parsed["files_read"]
    notes: list[str] = []

    if files_read == 0:
        notes.append(
            "No events.jsonl files were readable under the extraction output dir; "
            "all token/cost/response/agent-wallclock fields are not_available."
        )
    else:
        notes.append(
            f"Parsed {parsed['llm_responses']} llm:response event(s) across "
            f"{files_read} events.jsonl file(s). Token keys read with dual-shape "
            f"fallback (input_tokens/input, etc.)."
        )
        # State the de-duplication explicitly. Without this the corrected figure
        # is indistinguishable from a run that simply made fewer calls.
        dupes = parsed["duplicate_responses"]
        if dupes:
            notes.append(
                f"Dropped {dupes} duplicate llm:response event(s): this session composes more "
                f"than one logging hook, so each call was written to disk more than once. "
                f"Counted once each by response identity."
            )
        unknown = parsed["unidentified_responses"]
        if unknown:
            notes.append(
                f"{unknown} llm:response event(s) carried no response id and no timestamp, so "
                f"they could not be de-duplicated and are counted as-is; if this session "
                f"composes multiple logging hooks these may be overcounted."
            )
        if parsed["cost_from_events"]:
            notes.append("cost_usd summed from per-response cost_usd fields.")
        else:
            notes.append("cost_usd is not_available: no event carried a cost_usd field.")
        if parsed["had_timestamps"]:
            notes.append(
                "agent_wallclock_s is the earliest-to-latest event timestamp span "
                "(a floor on true agent time; agent_run_s is the measured duration)."
            )
        else:
            notes.append("agent_wallclock_s is not_available: no timestamps found.")

    return _finalize(parsed, source=source, source_files=events_paths, notes=notes)


def normalize_opencode_metrics(
    db_paths: list[str],
    *,
    source: str | None = None,
    workspace_dir: str,
) -> dict[str, Any]:
    """Produce the normalized metrics.json record from a vanilla opencode db.

    Same `not_available` discipline and output schema as `normalize_metrics`,
    but the token/cost source is the opencode SQLite `session` table rather than
    Amplifier events.jsonl. `workspace_dir` is required for the reason given in
    `parse_opencode_db`: a wrong value silently matches no session.
    """
    parsed = parse_opencode_db(db_paths, workspace_dir)
    files_read = parsed["files_read"]
    extra_notes: list[str] = list(parsed["extra_notes"])
    notes: list[str] = []

    if files_read == 0:
        # Only claim "nothing was readable" when nothing explains it better. A
        # source that WAS read but contributed no rows has already said so, and
        # the two statements together read as a contradiction.
        if not extra_notes:
            notes.append(
                "No usable opencode.db was readable under the extraction output dir; "
                "all token/cost/response/agent-wallclock fields are not_available."
            )
    else:
        notes.append(
            f"Parsed {parsed['llm_responses']} assistant message(s) across "
            f"{files_read} opencode.db file(s). ONLY sessions with directory == "
            f"{workspace_dir} were counted; every other session in the database was "
            f"excluded, with no fallback."
        )
        if parsed["cost_from_events"]:
            notes.append("cost_usd summed from the opencode session table `cost` column.")
        else:
            notes.append("cost_usd is not_available: the opencode session carried no cost.")
        if parsed["had_timestamps"]:
            notes.append(
                "agent_wallclock_s is the task session's time_created-to-time_updated span "
                "(opencode epoch-ms timestamps; a floor on true agent time)."
            )
        else:
            notes.append("agent_wallclock_s is not_available: no timestamps found.")

    # Parser-level warnings must reach metrics.json either way -- e.g. a db that
    # held sessions but none for this workspace, which is the difference between
    # "no data" and "wrong query".
    notes.extend(extra_notes)
    return _finalize(parsed, source=source, source_files=db_paths, notes=notes)


def _finalize(
    parsed: dict[str, Any],
    *,
    source: str | None,
    source_files: list[str],
    notes: list[str],
) -> dict[str, Any]:
    """Turn a parser's `parsed` dict into the normalized metrics.json record.

    Shared by both the events.jsonl and opencode.db branches: they produce the
    same `parsed` shape, so the `not_available` discipline and the rounding live
    here once. Branch-specific prose is composed by the caller and passed in.
    """
    if parsed["files_read"] == 0:
        # No session sources were pulled: everything derived from them is absent.
        record: dict[str, Any] = {name: NOT_AVAILABLE for name in METRIC_FIELDS}
    else:
        record = {
            "cost_usd": parsed["cost_usd"] if parsed["cost_from_events"] else NOT_AVAILABLE,
            "input_tokens": parsed["input_tokens"],
            "output_tokens": parsed["output_tokens"],
            "cache_read": parsed["cache_read_tokens"],
            "cache_write": parsed["cache_write_tokens"],
            "total_tokens": parsed["total_tokens"],
            "llm_responses": parsed["llm_responses"],
            "agent_wallclock_s": (
                parsed["agent_wallclock_s"] if parsed["had_timestamps"] else NOT_AVAILABLE
            ),
        }

    # Whole-trial elapsed is not measured here; the adapter records the agent
    # command duration instead. Never fabricate a number for it.
    record["total_wallclock_s"] = NOT_AVAILABLE

    # Round the span/cost figures for readability when they are numbers.
    if isinstance(record["agent_wallclock_s"], (int, float)):
        record["agent_wallclock_s"] = round(float(record["agent_wallclock_s"]), 3)
    if isinstance(record["cost_usd"], (int, float)):
        record["cost_usd"] = round(float(record["cost_usd"]), 6)

    # A source whose input figure was smaller than its own cache_read violates
    # the convention `parse_events` normalizes against, so the fresh-input
    # figure for those records is a clamped 0 rather than the truth. Say so.
    if parsed.get("negative_fresh_input"):
        notes = [
            *notes,
            (
                f"{parsed['negative_fresh_input']} record(s) reported input_tokens "
                "below their own cache_read, which contradicts the "
                "fresh-plus-cache_read convention this parser normalizes; fresh "
                "input was clamped to 0 for those, so input_tokens is a FLOOR and "
                "total_tokens may undercount."
            ),
        ]

    record["source"] = source
    record["events_files"] = list(source_files)
    record["notes"] = " ".join([*notes, "total_wallclock_s is not measured by this harness."])
    return record
