#!/usr/bin/env python3
"""ab-tool: AutomationBench API-mode tool surface exposed as a CLI.

This script is pushed into the evaluation DTU and invoked by the agent-under-test.
It exposes the AutomationBench `api` toolset (the surface the official leaderboard
uses) as shell subcommands, backed by a per-task world state persisted to disk.

Subcommands
-----------
seed    Build the simulated world from a task_info.json and write it to the world file.
search  Search available API endpoints by keyword (read-only).
fetch   Make a REST-style call that reads/mutates the world (persisted with locking).
encode  Base64-encode text (utility tool).
grade   Score the final world against the task assertions (deterministic).

The world lives in a single JSON file (``--world`` or ``$AB_WORLD_FILE``). Every
mutating call does a locked read-modify-write so parallel tool calls stay safe.

Imports are kept light at module load (schema + api tools only). The heavy,
``verifiers``-dependent grading stack is imported lazily inside `cmd_grade`, so the
per-turn `fetch`/`search` path never pays that cost.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
from typing import Any

# Light imports only. WorldState is pure-pydantic; automationbench.tools.api holds
# the 3 API-mode tools (api_search, api_fetch, base64_encode) and pulls no heavy deps.
from automationbench.schema.world import WorldState
from automationbench.tools.api.search import api_search
from automationbench.tools.api.fetch import api_fetch
from automationbench.tools.api.encode import base64_encode


# ---------------------------------------------------------------------------
# Helpers copied verbatim from automationbench.runner so we avoid importing that
# module (it pulls in the verifiers/agents evaluation stack). Keep in sync.
# ---------------------------------------------------------------------------
def strip_none_values(obj: Any) -> Any:
    """Recursively strip None values from nested dicts and lists.

    HuggingFace Dataset normalization injects None for absent keys, which breaks
    pydantic default_factory. AutomationBench strips these before building the world.
    """
    if isinstance(obj, dict):
        return {k: strip_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [strip_none_values(item) for item in obj if item is not None]
    return obj


_SERVICE_FIELDS = sorted(
    (str(f) for f in WorldState.model_fields if f != "meta"), key=len, reverse=True
)


def _service_for_name(name: str) -> str | None:
    for field in _SERVICE_FIELDS:
        if name == field or name.startswith(field + "_"):
            return field
    return None


def compute_allowed_services(
    initial_state: dict, assertions: list[dict], zapier_tools: list[str]
) -> list[str]:
    """Derive the services a task's world is subscribed to (matches runner.py)."""
    allowed: set[str] = set()
    for key in initial_state:
        if key != "meta" and key in WorldState.model_fields:
            allowed.add(key)
    for a in assertions or []:
        service = _service_for_name(str(a.get("type", "")))
        if service:
            allowed.add(service)
    for tool_name in zapier_tools or []:
        service = _service_for_name(tool_name)
        if service:
            allowed.add(service)
    return sorted(allowed)


# ---------------------------------------------------------------------------
# World persistence
# ---------------------------------------------------------------------------
def _world_path(args: argparse.Namespace) -> str:
    path = args.world or os.environ.get("AB_WORLD_FILE")
    if not path:
        _fail("no world file: pass --world or set AB_WORLD_FILE")
    return path


@contextlib.contextmanager
def _locked(path: str):
    """Exclusive lock around a world-file read-modify-write."""
    lock_path = path + ".lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _load_world(path: str) -> WorldState:
    with open(path) as f:
        data = json.load(f)
    return WorldState(**data)


def _save_world(path: str, world: WorldState) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(world.model_dump(mode="json"), f)
    os.replace(tmp, path)


def _load_info(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    # task_info may store `info` as a JSON string (AutomationBench dataset packing).
    if isinstance(data.get("info"), str):
        data["info"] = json.loads(data["info"])
    return data


def _fail(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_seed(args: argparse.Namespace) -> None:
    info = _load_info(args.info)
    task_info = info.get("info", info)
    initial_state = strip_none_values(task_info.get("initial_state", {}))
    assertions = task_info.get("assertions", [])
    zapier_tools = task_info.get("zapier_tools", [])

    world = WorldState(**initial_state)
    world.meta.allowed_services = compute_allowed_services(
        initial_state, assertions, zapier_tools
    )
    path = _world_path(args)
    _save_world(path, world)
    print(json.dumps({"seeded": path, "allowed_services": world.meta.allowed_services}))


def cmd_search(args: argparse.Namespace) -> None:
    # Read-only: no world needed.
    print(api_search(args.query, args.top_k))


def cmd_fetch(args: argparse.Namespace) -> None:
    path = _world_path(args)
    with _locked(path):
        world = _load_world(path)
        result = api_fetch(world, args.method, args.url, args.params, args.body)
        _save_world(path, world)
    print(result)


def cmd_encode(args: argparse.Namespace) -> None:
    print(base64_encode(args.text))


def cmd_grade(args: argparse.Namespace) -> None:
    # Heavy import isolated to grading only.
    from automationbench.rubric import partial_credit, task_completed_correctly

    info = _load_info(args.info)
    task_info = info.get("info", info)
    initial_state = strip_none_values(task_info.get("initial_state", {}))
    assertions = task_info.get("assertions", [])

    world = _load_world(_world_path(args))
    state: dict[str, Any] = {
        "world": world,
        "info": {"assertions": assertions},
        "initial_state": initial_state,
    }
    score = partial_credit(state)
    passed = task_completed_correctly(state)
    out = {
        "partial_credit": score,
        "task_completed_correctly": passed,
        "assertion_results": state.get("_assertion_results"),
    }
    text = json.dumps(out, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ab-tool", description=__doc__)
    p.add_argument("--world", help="Path to the world-state JSON file (or $AB_WORLD_FILE).")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("seed", help="Build the world from a task_info.json.")
    s.add_argument("--info", required=True, help="Path to task_info.json.")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("search", help="Search available API endpoints by keyword.")
    s.add_argument("--query", required=True)
    s.add_argument("--top-k", type=int, default=5)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("fetch", help="Make a REST-style API call against the world.")
    s.add_argument("--method", required=True, help="HTTP method, e.g. GET, POST, PATCH.")
    s.add_argument("--url", required=True, help="Endpoint URL (find one via `search`).")
    s.add_argument("--params", default=None, help="Query params as a JSON string.")
    s.add_argument("--body", default=None, help="Request body as a JSON string.")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("encode", help="Base64-encode text.")
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_encode)

    s = sub.add_parser("grade", help="Score the final world against task assertions.")
    s.add_argument("--info", required=True, help="Path to task_info.json.")
    s.add_argument("--out", default=None, help="Optional path to write the JSON result.")
    s.set_defaults(func=cmd_grade)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
