#!/usr/bin/env python3
"""Extract a single AutomationBench task into a self-contained task_info dict.

The evaluation harness must NOT depend on the `automationbench` package at the host
level (it targets Python 3.13 and pulls the verifiers stack). This tool imports the
package in an isolated env and emits one task's data (prompt, zapier_tools,
initial_state, assertions) as JSON.

This is used at RUNTIME by the automation-bench loader: no AutomationBench content
is vendored in this repo anymore. `eval.task_loaders.automation_bench_support.dataset`
shallow-clones the pinned AutomationBench repo once per machine and invokes this
script (in a `uv run --python 3.13 --with datasets --with pydantic` env with the
clone on PYTHONPATH) to fetch each task's data on the fly. It also remains usable
standalone against a local clone:

    PYTHONPATH=reference/AutomationBench \
      uv run --python 3.13 --with datasets --with pydantic \
      python .../automation_bench/extract_task.py \
        simple.email_sf_contact_phone_update --out .../task_info.json

Each domain module exposes one getter per task named ``get_<task_with_dots_as_underscores>``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys


def extract(task_name: str) -> dict:
    """Return the AutomationBench task_info dict for a dotted ``task_name``.

    The getter function names are NOT a stable derivation of the dotted task name
    (e.g. ``finance.timesheet_to_invoice`` -> ``get_fin_timesheet_to_invoice_task``,
    using a domain abbreviation and a ``_task`` suffix). So instead of guessing the
    getter name, resolve by matching each getter's own canonical ``task`` field --
    every ``get_*_task`` builder in a domain module sets it. This is robust across
    the package's naming conventions.
    """
    domain = task_name.split(".", 1)[0]
    module_name = f"automationbench.domains.{domain}.tasks"
    module = importlib.import_module(module_name)

    for attr in dir(module):
        # Domain modules expose one `get_<name>_task` builder per task plus a
        # `get_<domain>_dataset` aggregator; only the former return a task dict.
        if not (attr.startswith("get_") and attr.endswith("_task")):
            continue
        fn = getattr(module, attr)
        if not callable(fn):
            continue
        try:
            task = fn()
        except Exception:  # noqa: BLE001 - skip builders that can't construct here
            continue
        if not isinstance(task, dict) or task.get("task") != task_name:
            continue
        # Normalize: some rows pack `info` as a JSON string; keep it a nested dict.
        if isinstance(task.get("info"), str):
            task["info"] = json.loads(task["info"])
        return task

    raise SystemExit(f"no AutomationBench task matching {task_name!r} in {module_name}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "task", help="AutomationBench task name, e.g. simple.email_sf_contact_phone_update"
    )
    p.add_argument("--out", default=None, help="Output path (default: stdout).")
    args = p.parse_args()

    task = extract(args.task)
    text = json.dumps(task, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
