import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from amplifier_agent_evaluations import metrics, profile, provenance, snapshot, summarize, trial

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


def test_shipped_profiles_load() -> None:
    for name in ("smoke-checkout", "smoke-github", "regression-checkout", "regression-github"):
        loaded = profile.load_profile(EVAL_ROOT / "runs" / f"{name}.yaml")
        assert loaded["name"] == name
        assert loaded["output"] == str((EVAL_ROOT / "output").resolve())
    smoke = profile.load_profile(EVAL_ROOT / "runs" / "smoke-github.yaml")
    assert [t["id"] for t in profile.select_tasks(smoke)] == ["core/hello"]
    regression = profile.load_profile(EVAL_ROOT / "runs" / "regression-github.yaml")
    ids = [t["id"] for t in profile.select_tasks(regression)]
    assert len(ids) == 21
    assert {task_id.split("/")[0] for task_id in ids} == {"core", "provider", "tools"}


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

    destination = tmp_path / "snapshot"
    snap = snapshot.create(repo, destination)

    files = subprocess.run(
        ["git", "-C", str(destination), "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert files == [".gitignore", "added", "committed", "staged", "untracked"]
    assert all((destination / name).read_text() == "new" for name in files[1:])
    branch = subprocess.run(["git", "-C", str(destination), "branch", "--show-current"], capture_output=True, text=True)
    assert branch.stdout.strip() == provenance.BRANCH
    status = subprocess.run(["git", "-C", str(destination), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""
    assert snap["files"] == 5


def test_provenance() -> None:
    assert provenance.verdict(installed("abc", "abc"), "github", "abc")["ok"]
    assert not provenance.verdict(installed("abc", "abc"), "checkout", "def")["ok"]
    mixed = provenance.verdict(installed("abc", "def"), "github", "abc")
    assert not mixed["ok"]
    assert "different" in mixed["reason"]
    assert not provenance.verdict({"packages": {"amplifier-agent": None}}, "checkout", "abc")["ok"]


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
