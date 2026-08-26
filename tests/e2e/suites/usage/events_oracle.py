"""Independent ground truth for a turn's token usage, read from ``events.jsonl``.

The envelope's ``metadata`` block is the thing under test, so it cannot also be the
thing that proves itself. This module supplies the second, independent source: the
provider's own per-call usage as recorded by hook-context-intelligence in the
session's ``context-intelligence/events.jsonl``.

Record shape (one JSON object per line, same as the raw-capture suite reads):

    {"event": "llm:response", "data": {"turn_id": "...", "usage": {...}}}

and the kernel's ``usage`` sub-dict carries ``input_tokens``, ``output_tokens``,
``cache_read_tokens``, ``cache_write_tokens`` and (when the provider reports one)
``cost_usd``. Note ``input_tokens`` there is the NEW input only -- cache reads and
cache writes are counted separately, which is why the charged total is a sum of the
three rather than ``input_tokens`` alone.

The file is summed INSIDE the container by a small ``python3 -c`` program and only
the totals cross back to the host. ``events.jsonl`` lines can be very large (a single
line carries a whole LLM request or response when raw capture is on), so ``cat``-ing
the file back would be both slow and a good way to blow up the test process.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass

from framework import dtu

# Root that buckets session state by workspace (persistence.workspaces_root()).
# Same constant the raw_capture suite uses; duplicated rather than imported so this
# suite stays a self-contained brick.
WORKSPACES_ROOT = "/root/.amplifier-agent/state/workspaces"

# Path to the event log, relative to a session directory.
EVENTS_RELPATH = "context-intelligence/events.jsonl"

# Summing program, run inside the DTU. Reads the log line by line and prints ONE
# small JSON object. Keeping it a separate argv element (python3 -c PROGRAM PATH)
# means no shell quoting is involved in either the program or the path.
_SUM_PROGRAM = r"""
import json, sys

path = sys.argv[1]
keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
totals = dict.fromkeys(keys, 0)
responses = 0
responses_without_usage = 0
turn_ids = []

with open(path, encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("event") != "llm:response":
            continue
        responses += 1
        data = obj.get("data")
        if not isinstance(data, dict):
            responses_without_usage += 1
            continue
        turn_id = data.get("turn_id")
        if isinstance(turn_id, str) and turn_id and turn_id not in turn_ids:
            turn_ids.append(turn_id)
        usage = data.get("usage")
        if not isinstance(usage, dict):
            responses_without_usage += 1
            continue
        for key in keys:
            value = usage.get(key)
            try:
                totals[key] += int(value or 0)
            except (TypeError, ValueError):
                pass

json.dump(
    {
        "totals": totals,
        "responses": responses,
        "responses_without_usage": responses_without_usage,
        "turn_ids": turn_ids,
    },
    sys.stdout,
)
"""


@dataclass(frozen=True)
class ProviderUsage:
    """Per-turn provider usage summed from ``llm:response`` records.

    ``input_tokens`` is the NEW input the provider billed as fresh; ``charged_input``
    adds the cached halves, which is what the envelope's ``tokensIn`` is specified to
    report.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    responses: int
    responses_without_usage: int
    turn_ids: tuple[str, ...]

    @property
    def charged_input(self) -> int:
        """CHARGED input total: new input + cache reads + cache writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def resolve_session_dir(dtu_id: str, session_id: str, *, root: str = WORKSPACES_ROOT) -> str:
    """Return the absolute in-DTU session directory for ``session_id``.

    The workspace slug is picked at runtime, so the directory is found rather than
    constructed. A missing or ambiguous match is a hard failure: with no record there
    is nothing to compare the envelope against, and a silent fallback would let a test
    pass for the wrong reason.
    """
    cmd = f"find {shlex.quote(root)} -maxdepth 3 -type d -name {shlex.quote(session_id)} 2>/dev/null"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    matches = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip()]

    if not matches:
        listing = dtu.exec_json(dtu_id, ["bash", "-lc", f"ls -1 {shlex.quote(root)} 2>&1"])
        raise AssertionError(
            f"no session directory named {session_id!r} under {root}.\n"
            f"The turn did not persist a session record, so there is no independent\n"
            f"usage record to check the envelope against.\n"
            f"workspaces present:\n{listing.get('stdout', '')}"
        )
    if len(matches) > 1:
        raise AssertionError(
            f"ambiguous session id {session_id!r}; matched {len(matches)} dirs:\n" + "\n".join(matches)
        )
    return matches[0]


def read_provider_usage(dtu_id: str, session_id: str) -> ProviderUsage:
    """Sum the provider's own usage for ``session_id`` from inside the DTU."""
    session_dir = resolve_session_dir(dtu_id, session_id)
    events_path = f"{session_dir}/{EVENTS_RELPATH}"

    result = dtu.exec_json(dtu_id, ["python3", "-c", _SUM_PROGRAM, events_path])
    if result.get("exit_code") != 0:
        raise AssertionError(
            f"could not sum {events_path} (exit {result.get('exit_code')}).\nstderr:\n{result.get('stderr', '')}"
        )

    stdout = result.get("stdout", "").strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"usage oracle produced non-JSON output for {events_path}:\n{stdout}") from exc

    if payload["responses"] == 0:
        raise AssertionError(
            f"{events_path} recorded no 'llm:response' events for session {session_id!r}.\n"
            "Without at least one provider-reported usage record there is nothing to\n"
            "check the envelope against, so this test cannot prove anything."
        )

    totals = payload["totals"]
    return ProviderUsage(
        input_tokens=int(totals["input_tokens"]),
        output_tokens=int(totals["output_tokens"]),
        cache_read_tokens=int(totals["cache_read_tokens"]),
        cache_write_tokens=int(totals["cache_write_tokens"]),
        responses=int(payload["responses"]),
        responses_without_usage=int(payload["responses_without_usage"]),
        turn_ids=tuple(payload["turn_ids"]),
    )
