"""Reap leaked `jb-`-prefixed DTU instances around a matrix run.

Every trial's DTU is uniquely named and destroyed in its own `finally` (see
`jobbench.trial._dtu_name` / `run_trial`), so under normal operation nothing
here ever has work to do. The gap this covers is a hard kill of the harness
process itself (Ctrl-C, OOM, node reboot) mid-sweep: that skips every
in-flight trial's `finally` and leaves its container running. At 260 trials
that gap is not hypothetical.

This is a coarse safety net around the whole matrix, not a substitute for
per-trial cleanup. It is deliberately only ever invoked from `run.py` BEFORE
the matrix starts and AFTER it finishes -- never while trials are in flight --
so it cannot destroy a container one of THIS PROCESS's own trials still owns.
That is the entire safety property, and it holds only within one process.

UNSAFE ACROSS PROCESSES: the sweep destroys every `jb-`-prefixed instance the
CLI reports, and a name cannot distinguish "leaked from a dead run" from
"owned by a live peer run" -- telling those apart is the whole point of the
pre-run sweep, so no naming scheme fixes this. Two harness processes running
against the same host WILL destroy each other's live containers. Run only one
harness process per host, or pass `--no-orphan-sweep` to the concurrent ones
(which then leak their own containers on a hard kill; reap them by hand).
"""

from __future__ import annotations

import asyncio
import json
import logging

from jobbench.dtu import CLI, DTU

logger = logging.getLogger(__name__)

JB_PREFIX = "jb-"


async def list_instances() -> list[dict]:
    """Raw `amplifier-digital-twin list` output (a JSON array of instance
    dicts, each carrying at least `id`). Best-effort: a sweep that can't
    enumerate instances must not abort the run, only skip reaping this time.
    """
    proc = await asyncio.create_subprocess_exec(
        CLI,
        "list",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "orphan sweep: `%s list` failed: %s",
            CLI,
            stderr_b.decode("utf-8", errors="replace").strip(),
        )
        return []
    try:
        payload = json.loads(stdout_b.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        logger.warning("orphan sweep: `%s list` did not return JSON", CLI)
        return []
    return payload if isinstance(payload, list) else []


async def sweep_orphans() -> list[str]:
    """Destroy every `jb-`-prefixed DTU instance currently reported by the
    CLI, and return the ids destroyed.

    Safe with respect to THIS process's trials only: it is called when this
    harness has none in flight (see module docstring), so every `jb-` instance
    it sees is a leak as far as this process can tell. It has no way to tell a
    leak from a container a PEER harness process on the same host is actively
    using, and destroys both alike -- so running two harness processes against
    one host concurrently is unsafe. `run --no-orphan-sweep` opts out.
    """
    instances = await list_instances()
    destroyed: list[str] = []
    for inst in instances:
        inst_id = inst.get("id")
        if not inst_id or not isinstance(inst_id, str) or not inst_id.startswith(JB_PREFIX):
            continue
        logger.info("orphan sweep: reaping %s", inst_id)
        # profile_path is unused by destroy(); DTU is just an id handle here.
        await DTU(id=inst_id, profile_path="").destroy()
        destroyed.append(inst_id)
    return destroyed


__all__ = ["JB_PREFIX", "list_instances", "sweep_orphans"]
