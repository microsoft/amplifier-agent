import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import ModuleType
from typing import Any

import pytest
import yaml

from amplifier_agent_evaluations import (
    bake,
    metrics,
    preflight,
    profile,
    provenance,
    runner,
    snapshot,
    summarize,
    trial,
    universe,
)

EVAL_ROOT = Path(__file__).resolve().parents[1]
SID = "s-1"


def envelope(turn: str, sequence: int, kind: str, payload: dict) -> dict:
    return {
        "contract_version": "turn-events/1",
        "session_id": SID,
        "turn_id": turn,
        "sequence": sequence,
        "type": kind,
        "payload": payload,
    }


USAGE = {
    "entries": [
        {
            "provider": "openai",
            "model": "gpt-6-sol",
            "tokens_in": 100,
            "tokens_out": 7,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost": {"USD": "0.0142"},
        }
    ]
}


def good_turn(turn: str) -> list[dict]:
    return [
        envelope(
            turn,
            1,
            "turn_started",
            {"continuation": "fresh", "primary_actual": {"provider": "openai", "model": "gpt-6-sol"}},
        ),
        envelope(
            turn, 2, "tool_call", {"call": {"call_id": "c1", "name": "delegate", "source": "built-in", "arguments": {}}}
        ),
        envelope(turn, 3, "tool_result", {"resolution": {"call_id": "c1", "outcome": "completed", "content": "x"}}),
        envelope(turn, 4, "output_delta", {"content": [{"type": "text", "text": "rea"}]}),
        envelope(turn, 5, "output_delta", {"content": [{"type": "text", "text": "dy"}]}),
        envelope(turn, 6, "usage", {"snapshot": USAGE}),
        envelope(
            turn, 7, "terminal", {"state": "success", "content": [{"type": "text", "text": "ready"}], "usage": USAGE}
        ),
    ]


def write_trial(root: Path, events: list[dict], result: dict | None) -> Path:
    driver_dir = root / "driver"
    driver_dir.mkdir(parents=True)
    (driver_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    if result is not None:
        (driver_dir / "result.json").write_text(json.dumps(result))
    return root


RESULT = {
    "segments": [
        {
            "pid": 1,
            "started_at": "2026-09-23T10:00:00+00:00",
            "ended_at": "2026-09-23T10:00:09+00:00",
            "turns": [
                {
                    "index": 0,
                    "state": "success",
                    "content": [{"type": "text", "text": "ready"}],
                    "error": None,
                    "usage": USAGE,
                    "started_at": "2026-09-23T10:00:01+00:00",
                    "ended_at": "2026-09-23T10:00:04.500000+00:00",
                }
            ],
        },
        {
            "pid": 2,
            "started_at": "2026-09-23T10:00:10+00:00",
            "ended_at": "2026-09-23T10:00:20+00:00",
            "turns": [
                {
                    "index": 1,
                    "state": "success",
                    "content": [],
                    "error": None,
                    "usage": USAGE,
                    "started_at": "2026-09-23T10:00:11+00:00",
                    "ended_at": "2026-09-23T10:00:13+00:00",
                }
            ],
        },
    ],
    "host": {},
    "driver_error": None,
}


def test_metrics(tmp_path: Path) -> None:
    t = write_trial(tmp_path, good_turn("t1") + good_turn("t2"), RESULT)
    (tmp_path / "install.log").write_text("install started x\ninstall exit 0 after 87s\n")
    m = metrics.compute(t, 123.4567)
    assert m["input_tokens"] == 200
    assert m["output_tokens"] == 14
    assert m["total_tokens"] == 214
    assert m["cache_read"] == 0
    assert m["cost_usd"] == 0.0284
    assert m["llm_responses"] == 2
    assert m["tool_calls"] == 2
    assert m["delegations"] == 2
    assert m["tool_calls_by_source"] == {"built-in": 2}
    assert m["agent_wallclock_s"] == 5.5
    assert m["total_wallclock_s"] == 123.457
    assert m["install_s"] == 87
    assert m["turns"] == 2
    assert m["turn_states"] == {"success": 2}


def test_metrics_not_available(tmp_path: Path) -> None:
    result = json.loads(json.dumps(RESULT))
    result["segments"][1]["turns"][0]["usage"] = None
    del result["segments"][0]["turns"][0]["usage"]["entries"][0]["cost"]
    t = write_trial(tmp_path, [], result)
    m = metrics.compute(t, None)
    assert m["input_tokens"] == "not_available"
    assert m["cost_usd"] == "not_available"
    assert m["install_s"] == "not_available"
    assert m["total_wallclock_s"] == "not_available"
    empty = metrics.compute(tmp_path / "nothing", 1.0)
    assert empty["turns"] == "not_available"
    assert empty["tool_calls"] == "not_available"


GRADER_YAML = """
evaluations:
  - name: reply
    steps: Read driver/result.json.
    rubric:
      a: {points: 10, description: first}
      b: {points: 5, description: second}
"""


def test_profile_requires_grader_yaml(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    for name in ("alpha", "beta", "gamma"):
        (tasks / name).mkdir(parents=True)
        (tasks / name / "task.yaml").write_text("turns: [{user: hi}]\n")
    (tasks / "alpha" / "grader.yaml").write_text(GRADER_YAML)
    loaded = profile.load_profile(write_profile(tmp_path, tasks={"include": ["*"], "exclude": []}))
    with pytest.raises(profile.ProfileError, match="missing for: beta, gamma"):
        profile.select_tasks(loaded, tasks)
    (tasks / "beta" / "grader.yaml").write_text(GRADER_YAML)
    (tasks / "gamma" / "grader.yaml").write_text("pass_score: 2\nevaluations: []\n")
    with pytest.raises(profile.ProfileError, match="pass_score must be a number from 0 to 1"):
        profile.select_tasks(loaded, tasks)


def write_profile(tmp_path: Path, **changes) -> Path:
    base = {
        "name": "t",
        "description": "d",
        "install": "github",
        "agent": {"provider": "openai", "model": "m"},
        "tasks": {"include": ["*"], "exclude": ["b*"]},
        "trials": 1,
        "parallel": 2,
        "timeouts": {"launch_seconds": 900, "task_seconds": 120},
        "grader": {"provider": "openai", "model": "m"},
        "output": "evaluations/output/x",
    } | changes
    path = tmp_path / "p.yaml"
    path.write_text(json.dumps(base))
    return path


def test_profile_parallel_and_globs(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    for name in ("alpha", "beta", "core/gamma", "provider/a", "provider/b", "provider/a/workspace/nested"):
        (tasks / name).mkdir(parents=True)
        (tasks / name / "task.yaml").write_text("turns: [{user: hi}]\n")
        (tasks / name / "grader.yaml").write_text(GRADER_YAML)
    loaded = profile.load_profile(write_profile(tmp_path), parallel=5)
    assert loaded["parallel"] == 5
    assert profile.load_profile(write_profile(tmp_path))["parallel"] == 2
    assert loaded["output"] == str((EVAL_ROOT / "output" / "x").resolve())
    assert [t["id"] for t in profile.select_tasks(loaded, tasks)] == ["alpha", "core/gamma", "provider/a", "provider/b"]
    narrowed = profile.load_profile(write_profile(tmp_path, tasks={"include": ["provider/*"]}))
    selected = profile.select_tasks(narrowed, tasks)
    assert [t["id"] for t in selected] == ["provider/a", "provider/b"]
    assert selected[0]["dir"] == tasks / "provider" / "a"
    assert selected[0]["spec"]["timeout_seconds"] == 120
    exact = profile.load_profile(write_profile(tmp_path, tasks={"include": ["core/gamma"]}))
    assert [t["id"] for t in profile.select_tasks(exact, tasks)] == ["core/gamma"]
    with pytest.raises(profile.ProfileError):
        profile.load_profile(write_profile(tmp_path, install="pypi"))
    with pytest.raises(profile.ProfileError):
        profile.load_profile(write_profile(tmp_path, agent={"provider": "openai"}))


SHIPPED_RUNS = (
    "smoke-checkout",
    "smoke-github",
    "smoke-typescript-checkout",
    "smoke-http-checkout",
    "regression-checkout",
    "regression-github",
)


def test_shipped_profiles_load() -> None:
    for name in SHIPPED_RUNS:
        loaded = profile.load_profile(EVAL_ROOT / "runs" / f"{name}.yaml")
        assert loaded["name"] == name
        assert loaded["output"] == str((EVAL_ROOT / "output").resolve())
        tasks = profile.select_tasks(loaded)
        assert preflight.surface_problems(loaded["install"], tasks) == []

    def selected(name: str) -> dict[str, str]:
        loaded = profile.load_profile(EVAL_ROOT / "runs" / f"{name}.yaml")
        return {t["id"]: t["spec"]["surface"] for t in profile.select_tasks(loaded)}

    assert selected("smoke-checkout") == selected("smoke-github") == {"core/hello": "python"}
    assert selected("smoke-typescript-checkout") == {"typescript/hello": "typescript"}
    assert selected("smoke-http-checkout") == {"http/hello": "http", "http/image": "http", "http/streaming": "http"}
    regression = selected("regression-github")
    assert {task_id.split("/")[0] for task_id in regression} == {"core", "provider", "tools", "typescript", "http"}
    assert all(
        surface == ("python" if task_id.split("/")[0] in {"core", "provider", "tools"} else task_id.split("/")[0])
        for task_id, surface in regression.items()
    )


def test_task_surface_defaults_to_python_and_is_validated(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    for name, surface in (("plain", None), ("node", "typescript"), ("face", "http")):
        (tasks / name).mkdir(parents=True)
        line = f"surface: {surface}\n" if surface else ""
        (tasks / name / "task.yaml").write_text(line + "turns: [{user: hi}]\n")
        (tasks / name / "grader.yaml").write_text(GRADER_YAML)
    loaded = profile.load_profile(write_profile(tmp_path, tasks={"include": ["*"], "exclude": []}))
    surfaces = {t["id"]: t["spec"]["surface"] for t in profile.select_tasks(loaded, tasks)}
    assert surfaces == {"face": "http", "node": "typescript", "plain": "python"}
    (tasks / "bad").mkdir()
    (tasks / "bad" / "task.yaml").write_text("surface: rust\nturns: [{user: hi}]\n")
    (tasks / "bad" / "grader.yaml").write_text(GRADER_YAML)
    with pytest.raises(profile.ProfileError, match="surface must be one of python, typescript, http"):
        profile.select_tasks(loaded, tasks)


def test_profile_and_driver_follow_surface_and_install() -> None:
    for surface in profile.SURFACES:
        for install in profile.INSTALLS:
            compose = trial.compose_file(surface, install)
            assert compose == EVAL_ROOT / "profiles" / surface / install / "compose.yaml"
            assert compose.is_file()
            text = compose.read_text()
            assert f"name: amplifier-agent-{surface}-{install}" in text
            assert ("../../../.snapshot/amplifier-agent" in text) == (install == "checkout")
        assert (EVAL_ROOT / "profiles" / surface / "install.sh").is_file()
        assert (trial.DRIVER / trial.DRIVERS[surface][1]).is_file()
    for surface in ("python", "http"):
        dockerfile = (EVAL_ROOT / "profiles" / surface / "Dockerfile").read_text()
        assert "node" not in dockerfile.lower()
    assert "node" in (EVAL_ROOT / "profiles" / "typescript" / "Dockerfile").read_text()
    out = trial.OUT_DIR
    assert (
        trial.driver_command("python", 1)
        == f"cd ~/app && uv run host/drive.py --task host/task.json --out {out} --segment 1"
    )
    assert trial.driver_command("typescript", 0) == (
        f"cd ~/app && node host/drive.mjs --task host/task.json --out {out} --segment 0"
    )
    assert trial.driver_command("http", 0) == (
        f"cd ~/app && uv run host/drive_http.py --task host/task.json --out {out} --segment 0"
    )


def load_drive_http() -> ModuleType:
    spec = importlib.util.spec_from_file_location("drive_http", EVAL_ROOT / "driver" / "drive_http.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("stream", [False, True])
def test_http_driver_records_face_usage(stream: bool) -> None:
    import httpx
    import openai

    drive_http = load_drive_http()
    identity = {"id": "chatcmpl-1", "created": 1, "model": "amplifier"}
    usage = {
        "prompt_tokens": 105,
        "completion_tokens": 10,
        "total_tokens": 115,
        "prompt_tokens_details": {"cached_tokens": 60},
        "cost_usd": "0.0121",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if not json.loads(request.content)["stream"]:
            message = {"role": "assistant", "content": "ready"}
            choice = {"index": 0, "message": message, "finish_reason": "stop"}
            return httpx.Response(
                200, json=identity | {"object": "chat.completion", "choices": [choice], "usage": usage}
            )
        chunk = identity | {"object": "chat.completion.chunk"}
        frames = [
            chunk
            | {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ready"}, "finish_reason": None}]},
            chunk | {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage},
        ]
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = openai.OpenAI(
        base_url="http://face/v1", api_key="token", http_client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    record: dict[str, Any] = {"index": 0}
    drive_http.run_turn(client, "amplifier", [{"role": "user", "content": "hi"}], {"stream": stream}, record)
    assert record["state"] == "success"
    assert record["content"] == "ready"
    assert record["http"]["usage"]["cost_usd"] == "0.0121"
    assert record["usage"] == {
        "entries": [
            {
                "provider": None,
                "model": "amplifier",
                "tokens_in": 105,
                "tokens_out": 10,
                "cache_read_tokens": 60,
                "cost": {"USD": "0.0121"},
            }
        ]
    }
    turns = [record]
    assert metrics._sum_field(turns, "tokens_in") == 105
    assert metrics._sum_cost(turns) == 0.0121


def test_http_driver_usage_omits_unknowns() -> None:
    drive_http = load_drive_http()
    assert drive_http.turn_usage(None, "amplifier") is None
    usage = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9, "prompt_tokens_details": None}
    assert drive_http.turn_usage(usage, "amplifier") == {
        "entries": [{"provider": None, "model": "amplifier", "tokens_in": 7, "tokens_out": 2}]
    }


def test_http_driver_user_message(tmp_path: Path) -> None:
    drive_http = load_drive_http()
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "b.JPEG").write_bytes(b"jpeg")
    assert drive_http.user_message({"user": "hi"}, tmp_path) == {"role": "user", "content": "hi"}
    assert drive_http.user_message({"user": "hi", "images": ["a.png", "b.JPEG"]}, tmp_path) == {
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,anBlZw=="}},
        ],
    }
    assert drive_http.user_message({"images": ["a.png"]}, tmp_path)["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.png", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.jpeg", "image/jpeg"),
        ("a.gif", "image/gif"),
        ("a.WEBP", "image/webp"),
    ],
)
def test_http_driver_media_type(path: str, expected: str) -> None:
    assert load_drive_http().media_type(path) == expected


def test_http_driver_media_type_refuses_other_extensions(tmp_path: Path) -> None:
    drive_http = load_drive_http()
    (tmp_path / "a.bmp").write_bytes(b"bmp")
    with pytest.raises(ValueError, match=r"image a\.bmp: unsupported extension '\.bmp'"):
        drive_http.user_message({"user": "hi", "images": ["a.bmp"]}, tmp_path)


def test_surface_problems() -> None:
    def task(surface: str, **spec: Any) -> dict[str, Any]:
        return {"id": f"{surface}/t", "spec": {"surface": surface, "turns": [{"user": "hi"}]} | spec}

    assert preflight.surface_problems("checkout", [task("python", host="caller_tool", approvals="host")]) == []
    assert preflight.surface_problems("github", [task("typescript", tools=[], approvals="allow")]) == []
    (problem,) = preflight.surface_problems("checkout", [task("typescript", host="caller_tool")])
    assert "hosts/ modules are Python" in problem
    assert preflight.surface_problems("checkout", [task("http", agent={"provider": "openai", "model": "m"})]) == []
    images = [{"user": "hi", "images": ["a.png"], "stream": True}]
    assert preflight.surface_problems("checkout", [task("http", turns=images)]) == []
    problems = preflight.surface_problems(
        "checkout", [task("http", tools=[], approvals="allow", turns=[{"user": "hi", "restart": True}])]
    )
    assert problems == [
        "task http/t (surface http): the HTTP face cannot honor approvals, tools",
        "task http/t (surface http): turn 0: the HTTP face cannot honor restart",
    ]
    effort = {"agent_options": {"reasoning_effort": "low"}, "session": {"reasoning_effort": "low"}}
    turns = [{"user": "hi", "reasoning_effort": "low"}]
    assert preflight.surface_problems("checkout", [task("typescript", turns=turns, **effort)]) == []
    assert preflight.surface_problems("checkout", [task("http", turns=turns, **effort)]) == [
        "task http/t (surface http): the HTTP face cannot honor agent_options, session",
        "task http/t (surface http): turn 0: the HTTP face cannot honor reasoning_effort",
    ]
    (problem,) = preflight.surface_problems("pypi", [task("python")])
    assert "no container profile" in problem


def installed(a: str | None, b: str | None) -> dict:
    return {
        "python": "3.13",
        "packages": {
            "amplifier-agent": {"version": "1", "direct_url": {"url": "u", "vcs_info": {"commit_id": a}}},
            "amplifier-agent-engine": {"version": "1", "direct_url": {"url": "u", "vcs_info": {"commit_id": b}}},
        },
    }


def test_snapshot_copies_working_tree_minus_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    (repo / ".gitignore").write_text("ignored/\n")
    for name in ("committed", "staged", "deleted"):
        (repo / name).write_text("old")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c")
    git("checkout", "--detach")
    (repo / "staged").write_text("new")
    (repo / "added").write_text("new")
    git("add", "staged", "added")
    (repo / "committed").write_text("new")
    (repo / "untracked").write_text("new")
    (repo / "deleted").unlink()
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x").write_text("new")
    (repo / "evaluations").mkdir()
    (repo / "evaluations" / "grader.yaml").write_text("answer key")

    destination = tmp_path / "snapshot"
    snap = snapshot.create(repo, destination)

    files = subprocess.run(
        ["git", "-C", str(destination), "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert files == [".gitignore", "added", "committed", "staged", "untracked"]
    assert all((destination / name).read_text() == "new" for name in files[1:])
    tagged = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", f"{provenance.TAG}^{{commit}}"], capture_output=True, text=True
    )
    assert tagged.stdout.strip() == snap["head"]
    status = subprocess.run(["git", "-C", str(destination), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""
    assert snap["files"] == 5
    assert snapshot.create(repo, tmp_path / "again")["head"] == snap["head"]
    (repo / "untracked").write_text("changed")
    assert snapshot.create(repo, tmp_path / "changed")["head"] != snap["head"]


def test_provenance() -> None:
    assert provenance.verdict(installed("abc", "abc"), "github", "abc")["ok"]
    assert provenance.verdict(installed("abc", "abc"), "github", "abc")["surface"] == "python"
    assert not provenance.verdict(installed("abc", "abc"), "checkout", "def")["ok"]
    mixed = provenance.verdict(installed("abc", "def"), "github", "abc")
    assert not mixed["ok"]
    assert "different" in mixed["reason"]
    assert not provenance.verdict({"packages": {"amplifier-agent": None}}, "checkout", "abc")["ok"]


def test_provenance_per_surface() -> None:
    http = installed("abc", "abc")
    http["surface"] = "http"
    assert not provenance.verdict(http, "checkout", "abc", "http")["ok"]
    http["packages"]["amplifier-agent-http"] = {"direct_url": {"vcs_info": {"commit_id": "abc"}}}
    http["packages"]["openai"] = {"version": "2", "direct_url": None}
    assert provenance.verdict(http, "checkout", "abc", "http")["ok"]
    node = {"surface": "typescript", "packages": {"amplifier-agent-ts": {"version": "1", "commit": "abc"}}}
    assert provenance.verdict(node, "github", "abc", "typescript")["ok"]
    assert not provenance.verdict(node, "github", "def", "typescript")["ok"]
    mismatched = provenance.verdict(node | {"surface": "python"}, "github", "abc", "typescript")
    assert not mismatched["ok"]
    assert "surface" in mismatched["reason"]


def test_provenance_typescript_from_npm() -> None:
    package = {"version": "1", "integrity": "sha512-x", "tarball_sha256": "abc"}
    node = {"surface": "typescript", "packages": {"amplifier-agent-ts": package}}
    match = provenance.verdict(node, "github", "sha256:abc", "typescript")
    assert match["ok"]
    assert match["installed_identity"] == "sha256:abc"
    mismatch = provenance.verdict(node, "github", "sha256:def", "typescript")
    assert not mismatch["ok"]
    assert mismatch["reason"] == "installed sha256:abc != expected sha256:def"
    package["tarball_sha256"] = None
    missing = provenance.verdict(node, "github", "sha256:abc", "typescript")
    assert not missing["ok"]
    assert missing["installed_identity"] is None
    assert "no commit or tarball digest for amplifier-agent-ts" in missing["reason"]


def test_expected_identities_per_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(provenance, "github_tag_commit", lambda: calls.append("tag") or "abc")
    monkeypatch.setattr(provenance, "github_release_digest", lambda: calls.append("release") or "sha256:def")
    assert runner.expected_identities("github", {"python", "http"}, tmp_path) == {"http": "abc", "python": "abc"}
    assert calls == ["tag"]
    expected = runner.expected_identities("github", {"python", "typescript"}, tmp_path)
    assert expected == {"python": "abc", "typescript": "sha256:def"}
    upstream = json.loads((tmp_path / "upstream.json").read_text())
    assert upstream["sha"] == "abc"
    assert upstream["expected"] == expected
    monkeypatch.setattr(snapshot, "create", lambda: {"head": "123", "files": 1})
    calls.clear()
    assert runner.expected_identities("checkout", {"typescript"}, tmp_path) == {"typescript": "123"}
    assert calls == []
    assert json.loads((tmp_path / "snapshot.json").read_text())["head"] == "123"


def test_typescript_install_method_per_install(tmp_path: Path) -> None:
    for install, method in (("github", "npm"), ("checkout", "build")):
        assert (
            f'command: ["/opt/setup/install.sh", "{method}"]' in trial.compose_file("typescript", install).read_text()
        )
    script = (EVAL_ROOT / "profiles" / "typescript" / "install.sh").read_text().replace("exec sleep infinity", "")
    subprocess.run(
        ["bash", "-c", script, "install.sh", "other"],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        check=True,
    )
    app = tmp_path / "app"
    assert (app / ".setup-status").read_text().strip() == "1"
    assert "usage: install.sh npm|build, got 'other'" in (app / "setup.log").read_text()
    assert not (app / "installed.json").exists()


def test_task_json_and_segments() -> None:
    task = {
        "id": "core/resume",
        "dir": Path(),
        "spec": {
            "turns": [{"user": "code {{nonce}}"}, {"restart": True}, {"user": "?"}],
        },
    }
    prof = {"agent": {"provider": "openai", "model": "m"}, "name": "r", "install": "checkout"}
    out = trial.task_json(task, 2, prof, "deadbeef")
    assert out["turns"][0]["user"] == "code deadbeef"
    assert out["agent"] == prof["agent"]
    assert out["_trial"] == {"task": "core/resume", "trial": 2, "run": "r", "install": "checkout"}
    assert "{{nonce}}" in task["spec"]["turns"][0]["user"]
    assert trial.segment_count(task["spec"]["turns"]) == 2


def graded(passed: bool, score: float = 0.8) -> dict:
    return {
        "overall_score": score,
        "pass_score": 1.0 if not passed else score,
        "passed": passed,
        "evaluations": [
            {
                "name": "reply",
                "weight": 1,
                "points_awarded": 12,
                "points_possible": 15,
                "criteria": {
                    "a": {"points": 10, "max": 10, "reason": "ok"},
                    "b": {"points": 2, "max": 5, "reason": "only <partial>"},
                },
            }
        ],
    }


def test_derive_status() -> None:
    def derive(graded: dict | None, **overrides: Any) -> tuple[str, str | None]:
        ok: dict[str, Any] = {"provenance_ok": True, "exits": [0], "grader_error": None, "harness_error": False}
        return trial.derive_status(**(ok | overrides), graded=graded)

    assert derive(graded(True)) == ("passed", None)
    status, reason = derive(graded(False))
    assert status == "failed"
    assert "below pass_score" in str(reason)
    assert derive(graded(True), exits=[1])[0] == "passed"
    assert derive(graded(True), exits=[0, None])[0] == "timeout"
    assert derive(None, provenance_ok=False)[0] == "error"
    assert derive(None, grader_error="grader exited 1") == ("error", "grader exited 1")
    assert derive(graded(True), harness_error=True)[0] == "error"


def test_grader_block() -> None:
    block = trial.grader_block(graded(False))
    assert block is not None
    assert block["overall_score"] == 0.8
    assert block["pass_score"] == 1.0
    assert block["passed"] is False
    assert block["evaluations"] == [
        {"name": "reply", "score": 0.8, "failed_criteria": {"b": {"points": 2, "max": 5, "reason": "only <partial>"}}}
    ]
    assert trial.grader_block(None) is None


def test_read_grader_result(tmp_path: Path) -> None:
    out = tmp_path / "grader"
    out.mkdir()
    (out / "grader.log").write_text("starting\ngrading failed: RuntimeError: boom\n")
    assert trial.read_grader_result(out, 1) == (
        None,
        "grader exited 1: grading failed: RuntimeError: boom (see grader/grader.log)",
    )
    assert trial.read_grader_result(out, 0)[1] == "grader exited 0 without writing grader/grader_result.json"
    (out / "grader_result.json").write_text("{}")
    assert trial.read_grader_result(out, 0)[1] == "grader_result.json has no boolean passed"
    (out / "grader_result.json").write_text(json.dumps(graded(True)))
    assert trial.read_grader_result(out, 0) == (graded(True), None)


def test_direct_strips_the_gateway_environment() -> None:
    command = trial.direct("env | sort")
    gateway = dict.fromkeys(trial.DTU_GATEWAY_ENV, "x") | {"PATH": "/usr/bin:/bin", "KEEP": "1"}
    shown = subprocess.run(["bash", "-c", command], env=gateway, capture_output=True, text=True, check=True).stdout
    assert "KEEP=1" in shown
    assert not any(line.split("=", 1)[0] in trial.DTU_GATEWAY_ENV for line in shown.splitlines())


def test_locked_commit_reads_the_grader_lock(tmp_path: Path) -> None:
    commit = trial.locked_commit()
    assert len(commit) == 40
    assert commit in (trial.GRADER / "uv.lock").read_text()
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "amplifier-agent"\nsource = { registry = "https://pypi.org/simple" }\n')
    with pytest.raises(ValueError, match="not locked to a git commit"):
        trial.locked_commit(lock)


def test_install_command_syncs_the_lock_and_checks_the_commit() -> None:
    command = trial.install_command("a" * 40)
    assert f"cd {trial.GRADER_PROJECT} && " in command
    assert f"UV_CACHE_DIR={trial.GRADER_CACHE} UV_PROJECT_ENVIRONMENT={trial.GRADER_VENV} " in command
    assert "uv sync --frozen --no-dev --no-editable" in command
    assert "direct_url.json" in command
    assert command.endswith(f'test "$installed" = {"a" * 40}')
    assert all((trial.GRADER / name).exists() for name in trial.GRADER_FILES)
    assert "--offline" not in command
    offline = trial.install_command("a" * 40, offline=True)
    assert "uv sync --frozen --offline --no-dev --no-editable" in offline
    assert offline.endswith(f'test "$installed" = {"a" * 40}')


def copy_profiles(tmp_path: Path) -> Path:
    profiles = tmp_path / "profiles"
    for surface in ("python", "typescript"):
        for install in ("checkout", "github"):
            for path in bake.profile_files(surface, install):
                target = profiles / path.relative_to(trial.PROFILES)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    return profiles


def test_image_key_covers_identity_install_surface_and_profile_files(tmp_path: Path) -> None:
    profiles = copy_profiles(tmp_path)
    key = bake.image_key("python", "checkout", "abc", profiles)
    assert key == bake.image_key("python", "checkout", "abc", profiles)
    assert key == bake.image_key("python", "checkout", "abc")
    others = {
        bake.image_key("python", "checkout", "abd", profiles),
        bake.image_key("python", "github", "abc", profiles),
        bake.image_key("typescript", "checkout", "abc", profiles),
    }
    assert key not in others
    assert len(others) == 3
    for name in ("Dockerfile", "install.sh", "checkout/compose.yaml"):
        path = profiles / "python" / name
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        assert bake.image_key("python", "checkout", "abc", profiles) != key
        path.write_bytes(original)
    assert bake.image_key("python", "checkout", "abc", profiles) == key
    assert bake.image_tag("python", "checkout", key) == f"amplifier-agent-eval/python-checkout:{key[:16]}"


def test_grader_key_follows_pushed_files_but_not_bytecode(tmp_path: Path) -> None:
    grader = tmp_path / "grader"
    (grader / "src" / "pkg" / "__pycache__").mkdir(parents=True)
    (grader / "pyproject.toml").write_text("p")
    (grader / "uv.lock").write_text("l")
    (grader / "src" / "pkg" / "a.py").write_text("a")
    key = bake.grader_key(grader)
    (grader / "src" / "pkg" / "__pycache__" / "a.pyc").write_text("x")
    assert bake.grader_key(grader) == key
    (grader / "src" / "pkg" / "a.py").write_text("b")
    assert bake.grader_key(grader) != key
    assert bake.grader_cache_path(grader, tmp_path / "c") == tmp_path / "c" / bake.grader_key(grader) / "uv-cache.tar"
    assert len(bake.grader_key()) == 64


@pytest.mark.parametrize("surface", ["python", "typescript", "http"])
def test_baked_profile_runs_the_image_instead_of_installing(tmp_path: Path, surface: str) -> None:
    compose = trial.compose_file(surface, "checkout")
    written = bake.baked_profile(compose, "amplifier-agent-eval/x:1", tmp_path / "p" / "x.yaml")
    config = yaml.safe_load(written.read_text())
    original = yaml.safe_load(compose.read_text())
    twin = config["services"]["twin"]
    assert "build" not in twin
    assert twin["image"] == "amplifier-agent-eval/x:1"
    assert twin["pull_policy"] == "never"
    assert twin["command"] == ["sleep", "infinity"]
    assert twin["healthcheck"] == original["services"]["twin"]["healthcheck"] | {"start_interval": "1s"}
    assert twin["environment"] == original["services"]["twin"]["environment"]
    assert config["name"] == original["name"]
    (repository,) = config["x-dtu"]["repositories"]
    assert repository["path"] == str(snapshot.SNAPSHOT)
    assert repository["url"] == "https://github.com/microsoft/amplifier-agent"
    github = yaml.safe_load(
        bake.baked_profile(trial.compose_file(surface, "github"), "t", tmp_path / "g.yaml").read_text()
    )
    assert "repositories" not in github["x-dtu"]


def image(tag: str, id: str, created: str) -> dict[str, str]:
    return {"tag": f"amplifier-agent-eval/python-checkout:{tag}", "id": id, "created": created}


def test_select_prunable_keeps_the_newest_and_protected() -> None:
    images = [
        image("a", "i1", "2026-01-01T00:00:00Z"),
        image("b", "i2", "2026-01-04T00:00:00Z"),
        image("c", "i3", "2026-01-02T00:00:00Z"),
        image("d", "i4", "2026-01-03T00:00:00Z"),
        image("e", "i5", "2026-01-05T00:00:00Z"),
    ]
    assert [i["tag"][-1] for i in bake.select_prunable(images, 3, set())] == ["c", "a"]
    assert [i["tag"][-1] for i in bake.select_prunable(images, 3, {"i1"})] == ["c"]
    assert [i["tag"][-1] for i in bake.select_prunable(images, 1, {"i3", "i4"})] == ["b", "a"]
    assert bake.select_prunable(images, 0, set()) == []
    assert bake.select_prunable(images, 5, set()) == []


def test_select_prunable_caches_keeps_the_newest_and_current(tmp_path: Path) -> None:
    caches = [(tmp_path / name, mtime) for name, mtime in (("a", 1.0), ("b", 4.0), ("c", 2.0), ("d", 3.0))]
    assert bake.select_prunable_caches(caches, 2, tmp_path / "b") == [tmp_path / "c", tmp_path / "a"]
    assert bake.select_prunable_caches(caches, 2, tmp_path / "a") == [tmp_path / "c"]
    assert bake.select_prunable_caches(caches, 0, tmp_path / "a") == []


def test_prune_removes_old_images_and_caches_and_records_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = {
        "amplifier-agent-eval/python-checkout": [
            image("new", "i1", "2026-01-03T00:00:00Z"),
            image("old", "i2", "2026-01-01T00:00:00Z"),
            image("busy", "i3", "2026-01-02T00:00:00Z"),
        ]
    }
    monkeypatch.setattr(bake, "_repository_images", lambda repository: listing[repository])
    monkeypatch.setattr(bake, "_images_in_use", lambda: {"i3"})
    removed: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        removed.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "conflict: image is in use")

    monkeypatch.setattr(bake.subprocess, "run", run)
    for name, mtime in (("current", 1.0), ("newer", 3.0), ("older", 2.0)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "uv-cache.tar").write_text("")
        os.utime(tmp_path / name, (mtime, mtime))
    pruned = bake.prune({"amplifier-agent-eval/python-checkout"}, {"i1"}, 1, tmp_path / "current" / "uv-cache.tar")
    assert removed == [["docker", "image", "rm", "amplifier-agent-eval/python-checkout:old"]]
    assert pruned["images"] == [
        {"tag": "amplifier-agent-eval/python-checkout:old", "id": "i2", "error": "conflict: image is in use"}
    ]
    assert pruned["grader_caches"] == [{"path": str(tmp_path / "older"), "error": None}]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["current", "newer"]
    assert bake.prune({"amplifier-agent-eval/python-checkout"}, set(), 0, tmp_path / "current" / "uv-cache.tar") == {
        "images": [],
        "grader_caches": [],
    }


def test_injected_env_and_commit_changes() -> None:
    image = ["PATH=/usr/bin", "HOME=/home/agent", "LANG=C.UTF-8"]
    container = [
        *image,
        "OPENAI_API_KEY=key-value",
        "GEMINI_API_KEY=",
        "HTTPS_PROXY=http://gateway:3128",
        "LANG=C",
    ]
    cleared = bake.injected_env(container, image)
    assert cleared == ["GEMINI_API_KEY", "HTTPS_PROXY", "LANG", "OPENAI_API_KEY"]
    changes = bake.commit_changes(["OPENAI_API_KEY"], {"key": "k", "run": "r"})
    assert changes == [
        "--change",
        'CMD ["sleep", "infinity"]',
        "--change",
        "ENV OPENAI_API_KEY=",
        "--change",
        "LABEL amplifier-agent-eval.key=k",
        "--change",
        "LABEL amplifier-agent-eval.run=r",
    ]


def test_summarize_report(tmp_path: Path) -> None:
    (tmp_path / "run.yaml").write_text(
        json.dumps(
            {
                "name": "r",
                "install": "github",
                "agent": {"provider": "o", "model": "m"},
                "grader": {"provider": "o", "model": "m"},
            }
        )
    )
    (tmp_path / "upstream.json").write_text(json.dumps({"sha": "abc"}))
    for name, status, cost in (("provider/a-1", "passed", 0.01), ("core/b-1", "failed", 0.02)):
        d = tmp_path / "trials" / name
        (d / "grader").mkdir(parents=True)
        block = trial.grader_block(graded(status == "passed"))
        usage = {"entries": [{"provider": "o", "model": "g", "tokens_in": 100, "tokens_out": 20}]}
        (d / "grader" / "grader_result.json").write_text(
            json.dumps(graded(status == "passed") | {"grader": {"provider": "o", "model": "g", "usage": usage}})
        )
        (d / "trial_result.json").write_text(
            json.dumps(
                {
                    "task": name.rsplit("-", 1)[0],
                    "trial": 1,
                    "status": status,
                    "grader": block,
                    "reason": None if status == "passed" else "score 0.8 below pass_score 1.0",
                    "metrics": {"cost_usd": cost, "total_wallclock_s": 10.0},
                    "provenance_ok": True,
                }
            )
        )
    pending = tmp_path / "trials" / "tools" / "c-1"
    pending.mkdir(parents=True)
    (pending / "state.json").write_text(json.dumps({"trial": "tools/c-1", "status": "pending", "stages": {}}))
    (pending / "task.json").write_text(json.dumps({"_trial": {"task": "tools/c", "trial": 1}}))
    s = summarize.summarize(tmp_path)
    assert s["counts"] == {"passed": 1, "failed": 1, "timeout": 0, "error": 1, "total": 3}
    assert [r["dir"] for r in s["trials"]] == ["core/b-1", "provider/a-1", "tools/c-1"]
    assert [r["task"] for r in s["trials"]] == ["core/b", "provider/a", "tools/c"]
    assert s["total_cost_usd"] == "not_available"
    assert s["total_cost_usd_known_part"] == 0.03
    assert s["grader_models"] == ["o/g"]
    assert s["grader_total_tokens"] == 240
    row = next(r for r in s["trials"] if r["dir"] == "core/b-1")
    assert row["score"] == 0.8
    assert row["pass_score"] == 1.0
    assert row["passed"] is False
    page = (tmp_path / "report.html").read_text()
    assert 'href="trials/provider/a-1/"' in page
    assert "abc" in page
    assert "<link" not in page
    assert "<script" not in page
    assert "<td>0.80</td>" in page
    assert "2/5" in page
    assert "only &lt;partial&gt;" in page
    assert "no trial_result.json" in page
    assert "<details open>" in page


def test_sessions_dir_is_the_default_for_the_driver_cwd() -> None:
    assert trial.SESSIONS_DIR == "/home/agent/.amplifier-agent/projects/-workspace/sessions"
    assert trial.LAYOUT["sessions"] == trial.STORAGE == "/home/agent/.amplifier-agent"
    for surface in profile.SURFACES:
        for install in profile.INSTALLS:
            text = trial.compose_file(surface, install).read_text()
            assert "AMPLIFIER_AGENT_STORAGE" not in text
            assert "AMPLIFIER_AGENT_WORKSPACE" not in text


def test_substitute_reaches_nested_agent_options() -> None:
    spec = {"agent_options": {"environment": {"EVAL_MARK": "{{nonce}}"}, "additional_directories": ["/x/{{nonce}}"]}}
    assert trial.substitute(spec, "n1") == {
        "agent_options": {"environment": {"EVAL_MARK": "n1"}, "additional_directories": ["/x/n1"]}
    }


def test_footprint_command_lists_files_and_symlinks_newer_than_the_marker(tmp_path: Path) -> None:
    root = tmp_path / "root"
    for name in ("workspace", "home/agent/app/out", "home/agent/grader", "tmp", "proc"):
        (root / name).mkdir(parents=True)
    old = root / "workspace" / "old.txt"
    old.write_text("old")
    marker = root / "home/agent/app/out/.footprint-marker"
    marker.touch()
    os.utime(old, (1_000, 1_000))
    os.utime(marker, (2_000, 2_000))
    (root / "workspace" / "new.txt").write_text("new")
    (root / "workspace" / "new-dir").mkdir()
    (root / "tmp" / "link").symlink_to(root / "workspace" / "new.txt")
    (root / "home/agent/app/out/result.json").write_text("{}")
    (root / "home/agent/grader/scratch.txt").write_text("x")
    (root / "proc" / "status").write_text("x")
    out = tmp_path / "footprint.txt"
    prune = tuple(str(root / name) for name in ("proc", "home/agent/app", "home/agent/grader"))
    command = trial.footprint_command(str(marker), str(out), str(root), prune)
    subprocess.run(["bash", "-c", command], check=True)
    assert out.read_text().splitlines() == [str(root / "tmp" / "link"), str(root / "workspace" / "new.txt")]
    marker.unlink()
    assert subprocess.run(["bash", "-c", command]).returncode != 0


def test_footprint_command_defaults() -> None:
    command = trial.footprint_command()
    assert command.startswith(f"test -f {trial.FOOTPRINT_MARKER} && ")
    assert command.endswith(f"| LC_ALL=C sort > {trial.FOOTPRINT}")
    assert "-xdev" not in command
    for path in ("/proc", "/sys", "/dev", trial.APP, trial.GRADER_HOME):
        assert f"-path {path} " in command
    assert trial.FOOTPRINT_MARKER.startswith(f"{trial.OUT_DIR}/")
    assert trial.FOOTPRINT.startswith(f"{trial.OUT_DIR}/")


class FakeUniverse:
    """universe.execute / run_background stand-ins recording the commands a trial sends."""

    def __init__(self, footprint: int | Exception = 0) -> None:
        self.footprint = footprint
        self.calls: list[str] = []

    def execute(self, id: str, command: str, timeout_seconds: int = 300, workdir: str | None = None) -> Any:
        self.calls.append(command)
        if command.startswith(f"test -f {trial.FOOTPRINT_MARKER}"):
            if isinstance(self.footprint, Exception):
                raise self.footprint
            return universe.ExecResult(exit_code=self.footprint, stdout="", stderr="denied" if self.footprint else "")
        return universe.ExecResult(exit_code=0, stdout="", stderr="")

    def run_background(self, id: str, command: str, log: str, exit_file: str, timeout_seconds: int) -> int:
        self.calls.append(command)
        return 0


def run_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeUniverse) -> tuple[trial.Trial, list]:
    monkeypatch.setattr(universe, "execute", fake.execute)
    monkeypatch.setattr(universe, "run_background", fake.run_background)
    spec = {"surface": "python", "timeout_seconds": 60, "turns": [{"user": "a"}, {"restart": True}, {"user": "b"}]}
    t = trial.Trial({"id": "tools/x", "spec": spec}, 1, {}, tmp_path, "", "")
    exits = t._stage("run", lambda: t._run_segments("u1", spec))
    return t, exits


def test_run_segments_marks_before_segment_zero_and_lists_after_the_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeUniverse()
    t, exits = run_with(monkeypatch, tmp_path, fake)
    assert exits == [0, 0]
    assert fake.calls[0] == f"touch {trial.FOOTPRINT_MARKER}"
    assert fake.calls[1:-1] == [trial.driver_command("python", 0), trial.driver_command("python", 1)]
    assert fake.calls[-1] == trial.footprint_command()
    assert t.state["stages"]["run"]["error"] is None


@pytest.mark.parametrize(
    ("footprint", "noted"),
    [(1, "footprint failed (1): denied"), (RuntimeError("gone"), "footprint failed: RuntimeError: gone")],
)
def test_footprint_failure_is_noted_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, footprint: int | Exception, noted: str
) -> None:
    t, exits = run_with(monkeypatch, tmp_path, FakeUniverse(footprint))
    assert exits == [0, 0]
    assert t.state["stages"]["run"]["error"] == noted


def load_drive_py() -> ModuleType:
    spec = importlib.util.spec_from_file_location("drive", EVAL_ROOT / "driver" / "drive.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_driver_passes_directory_and_environment_options(monkeypatch: pytest.MonkeyPatch) -> None:
    drive = load_drive_py()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(drive, "AgentOptions", lambda **kwargs: captured.update(kwargs) or kwargs)
    agent_options = {
        "working_directory": "project",
        "additional_directories": ["/home/agent/shared"],
        "environment": {"EVAL_MARK": "n1"},
        "tool_error_policy": "continue",
    }
    drive.build_options({"tools": [], "agent_options": agent_options}, None)
    assert {key: captured[key] for key in agent_options} == agent_options
    captured.clear()
    drive.build_options({"tools": []}, None)
    assert not {"working_directory", "additional_directories", "environment", "reasoning_effort"} & set(captured)
    drive.build_options({"tools": [], "agent_options": {"reasoning_effort": "high"}}, None)
    assert captured["reasoning_effort"] == "high"


def test_python_driver_passes_turn_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    drive = load_drive_py()
    monkeypatch.setattr(drive, "TurnInput", lambda **kwargs: kwargs)
    assert drive.turn_input({"user": "hi", "reasoning_effort": "low"})["reasoning_effort"] == "low"
    assert "reasoning_effort" not in drive.turn_input({"user": "hi"})


def test_python_driver_process_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drive = load_drive_py()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVAL_LEAKED", "1")
    monkeypatch.delenv("EVAL_KEPT", raising=False)
    task = {"agent_options": {"environment": {"EVAL_LEAKED": "x", "EVAL_KEPT": "y"}}}
    assert drive.process_record(task) == {"cwd": str(tmp_path), "environment_leaked": ["EVAL_LEAKED"]}
    assert drive.process_record({}) == {"cwd": str(tmp_path), "environment_leaked": []}


def test_profile_validates_switch_steps(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    (tasks / "t").mkdir(parents=True)
    (tasks / "t" / "grader.yaml").write_text(GRADER_YAML)
    loaded = profile.load_profile(write_profile(tmp_path, tasks={"include": ["*"], "exclude": []}))
    good = [
        {"user": "hi"},
        {"switch": {"provider": "openai", "model": "gpt-6-sol"}},
        {"switch": {"provider": "anthropic", "model": "claude-sonnet-5", "reasoning_effort": "low"}},
        {"user": "again"},
    ]
    (tasks / "t" / "task.yaml").write_text(yaml.safe_dump({"turns": good}))
    (selected,) = profile.select_tasks(loaded, tasks)
    assert selected["spec"]["turns"] == good
    bad = [
        ({"switch": {"provider": "openai"}}, "nonempty string provider and model"),
        ({"switch": {"provider": "openai", "model": "m", "tools": []}}, "optional reasoning_effort"),
        ({"switch": {"provider": "openai", "model": "m"}, "user": "hi"}, "holds only switch"),
        ({"switch": "openai/m"}, "optional reasoning_effort"),
        ({"switch": {"provider": "openai", "model": "m", "reasoning_effort": 3}}, "must be a string"),
    ]
    for step, message in bad:
        (tasks / "t" / "task.yaml").write_text(yaml.safe_dump({"turns": [{"user": "hi"}, step]}))
        with pytest.raises(profile.ProfileError, match=f"turn 1: .*{message}"):
            profile.select_tasks(loaded, tasks)


def test_surface_problems_switch_step() -> None:
    turns = [{"user": "hi"}, {"switch": {"provider": "openai", "model": "m"}}, {"user": "again"}]

    def task(surface: str) -> dict[str, Any]:
        return {"id": f"{surface}/t", "spec": {"surface": surface, "turns": turns}}

    assert preflight.surface_problems("checkout", [task("python"), task("typescript")]) == []
    assert preflight.surface_problems("checkout", [task("http")]) == [
        "task http/t (surface http): turn 1: the HTTP face cannot honor switch",
    ]


class FakeSwitchSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def set_model(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class FakeAgentError(Exception):
    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(message)
        self.code, self.message, self.remedy = code, message, remedy


def test_python_driver_records_switch(tmp_path: Path) -> None:
    drive = load_drive_py()
    events_path = tmp_path / "events.jsonl"
    ok, failed = FakeSwitchSession(), FakeSwitchSession(FakeAgentError("provider_failed", "no key", "Set KEY."))
    ok_record: dict[str, Any] = {"index": 1}
    failed_record: dict[str, Any] = {"index": 2}
    with events_path.open("a", encoding="utf-8") as events_file:
        asyncio.run(drive.run_switch(ok, {"switch": {"provider": "openai", "model": "m"}}, ok_record, events_file))
        spec = {"switch": {"provider": "gemini", "model": "g", "reasoning_effort": "low"}}
        asyncio.run(drive.run_switch(failed, spec, failed_record, events_file))
    assert ok.calls == [{"provider": "openai", "model": "m"}]
    assert failed.calls == [{"provider": "gemini", "model": "g", "reasoning_effort": "low"}]
    assert ok_record | {"started_at": None, "ended_at": None} == {
        "index": 1,
        "provider": "openai",
        "model": "m",
        "reasoning_effort": None,
        "state": "success",
        "error": None,
        "started_at": None,
        "ended_at": None,
    }
    assert failed_record["state"] == "failure"
    assert failed_record["reasoning_effort"] == "low"
    assert failed_record["error"]["code"] == "provider_failed"
    assert failed_record["error"]["remedy"] == "Set KEY."
    assert failed_record["error"]["type"] == "FakeAgentError"
    lines = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert [line["type"] for line in lines] == ["driver_switch", "driver_switch"]
    assert [line["turn_id"] for line in lines] == [None, None]
    assert [line["payload"] for line in lines] == [ok_record, failed_record]


def test_python_driver_switch_without_set_model_is_a_failure(tmp_path: Path) -> None:
    drive = load_drive_py()
    record: dict[str, Any] = {"index": 0}
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as events_file:
        asyncio.run(drive.run_switch(object(), {"switch": {"provider": "openai", "model": "m"}}, record, events_file))
    assert record["state"] == "failure"
    assert record["error"]["type"] == "AttributeError"


def test_python_driver_passes_turn_model(monkeypatch: pytest.MonkeyPatch) -> None:
    drive = load_drive_py()
    monkeypatch.setattr(drive, "TurnInput", lambda **kwargs: kwargs)
    assert drive.turn_input({"user": "hi", "model": "claude-opus-5"})["model"] == "claude-opus-5"
    assert "model" not in drive.turn_input({"user": "hi"})
