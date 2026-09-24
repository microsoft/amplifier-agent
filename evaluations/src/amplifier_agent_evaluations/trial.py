"""One trial end to end: launch, provenance, seed, run, pull, metrics, grade, pull-grader, destroy."""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import secrets
import shlex
import shutil
import time
import tomllib
import traceback
from typing import Any

from amplifier_agent_evaluations import EVAL_ROOT, metrics, provenance, universe

DRIVER = EVAL_ROOT / "driver"
APP = "/home/agent/app"
HOST_DIR = f"{APP}/host"
OUT_DIR = f"{APP}/out"
PROFILES = EVAL_ROOT / "profiles"
STAGES = ("launch", "provenance", "seed", "run", "pull", "metrics", "grade", "pull-grader", "destroy")
GRADER = EVAL_ROOT / "grader"
GRADER_FILES = ("pyproject.toml", "uv.lock", "src")
GRADER_HOME = "/home/agent/grader"
GRADER_PROJECT = f"{GRADER_HOME}/project"
GRADER_OUT = f"{GRADER_HOME}/out"
GRADER_VENV = f"{GRADER_HOME}/.venv"
# The grader's own uv cache: the twin's default cache holds what the agent's install fetched through the gateway.
GRADER_CACHE = f"{GRADER_HOME}/cache"
GRADER_INSTALL_SECONDS = 600
# What dtu-lite adds to the twin's environment to route it through its gateway (dtu_lite's universe overlay:
# the proxy, the gateway's CA in every client's dialect, and uv's switches). The checkout profile's gateway serves
# github.com/microsoft/amplifier-agent from the snapshot, so the grader installs and runs without all of them.
DTU_GATEWAY_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "NODE_EXTRA_CA_CERTS",
    "PIP_CERT",
    "CURL_CA_BUNDLE",
    "UV_SYSTEM_CERTS",
    "UV_NATIVE_TLS",
    "NODE_USE_ENV_PROXY",
    "UV_NO_GITHUB_FAST_PATH",
)
GRADER_TIMEOUT = 900
# Where the grader finds the evidence in the container; written to <trial>/layout.json and pushed with the rubric.
LAYOUT = {
    "workspace": "/workspace",
    "driver": OUT_DIR,
    "task": f"{GRADER_HOME}/task.json",
    "sessions": "/home/agent/.amplifier-agent",
    "grader_data": f"{GRADER_HOME}/data",
    "scratch": f"{GRADER_HOME}/scratch",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def substitute(value: Any, nonce: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{nonce}}", nonce)
    if isinstance(value, list):
        return [substitute(item, nonce) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, nonce) for key, item in value.items()}
    return value


def segment_count(turns: list[Any]) -> int:
    return 1 + sum(1 for turn in turns if isinstance(turn, dict) and turn.get("restart"))


def task_json(task: dict[str, Any], n: int, profile: dict[str, Any], nonce: str) -> dict[str, Any]:
    spec = substitute(task["spec"], nonce)
    spec["agent"] = spec.get("agent") or profile["agent"]
    spec["_trial"] = {"task": task["id"], "trial": n, "run": profile["name"], "install": profile["install"]}
    return spec


def direct(command: str) -> str:
    """`command` run by bash without DTU_GATEWAY_ENV, so it and every process it starts reach the internet directly."""
    unset = " ".join(f"-u {name}" for name in DTU_GATEWAY_ENV)
    return f"env {unset} bash -c {shlex.quote(command)}"


def locked_commit(lock: Path = GRADER / "uv.lock") -> str:
    """The amplifier-agent commit the grader's uv.lock pins."""
    packages = tomllib.loads(lock.read_text())["package"]
    source = next(package["source"] for package in packages if package["name"] == "amplifier-agent")
    url, _, commit = source.get("git", "").partition("#")
    if not url or len(commit) != 40:
        raise ValueError(f"{lock}: amplifier-agent is not locked to a git commit")
    return commit


def install_command(commit: str) -> str:
    """Install the pushed grader project into GRADER_VENV from its lock and check amplifier-agent is `commit`.

    Output goes to `<GRADER_OUT>/install.log`; the command exits non-zero when the sync fails or the commit differs.
    """
    log = f"{GRADER_OUT}/install.log"
    probe = (
        "import json, importlib.metadata as m; "
        "print(json.loads(m.distribution('amplifier-agent').read_text('direct_url.json'))['vcs_info']['commit_id'])"
    )
    return (
        f"cd {GRADER_PROJECT} && "
        f"UV_CACHE_DIR={GRADER_CACHE} UV_PROJECT_ENVIRONMENT={GRADER_VENV} "
        f"uv sync --frozen --no-dev --no-editable --python 3.13 >> {log} 2>&1 && "
        f"installed=$({GRADER_VENV}/bin/python -c {shlex.quote(probe)}) && "
        f'echo "amplifier-agent installed at $installed; grader/uv.lock pins {commit}" >> {log} && '
        f'test "$installed" = {commit}'
    )


def grader_command(grader: dict[str, str]) -> str:
    """The container command running the installed grader over the evidence LAYOUT names.

    AMPLIFIER_AGENT_CONFIG points at the empty settings file `_grade` writes, so no settings the agent under test
    left behind reach the grader.
    """
    return (
        f"cd {LAYOUT['scratch']} && AMPLIFIER_AGENT_CONFIG={GRADER_HOME}/config.json "
        f"{GRADER_VENV}/bin/amplifier-agent-grade --layout {GRADER_HOME}/layout.json "
        f"--rubric {GRADER_HOME}/grader.yaml --out {GRADER_OUT} "
        f"--provider {shlex.quote(grader['provider'])} --model {shlex.quote(grader['model'])}"
    )


def read_grader_result(out: Path, exit_code: int) -> tuple[dict[str, Any] | None, str | None]:
    """(grader_result, None) or (None, grader_error) from a pulled `<trial>/grader/` and the grader's exit code."""
    if exit_code != 0:
        log = out / "grader.log"
        lines = log.read_text(errors="replace").strip().splitlines() if log.is_file() else []
        tail = lines[-1] if lines else ""
        return None, f"grader exited {exit_code}: {tail[:300]} (see grader/grader.log)"
    result_path = out / "grader_result.json"
    if not result_path.is_file():
        return None, "grader exited 0 without writing grader/grader_result.json"
    try:
        graded = json.loads(result_path.read_text())
    except json.JSONDecodeError as error:
        return None, f"grader_result.json is not JSON: {error}"
    if not isinstance(graded, dict) or not isinstance(graded.get("passed"), bool):
        return None, "grader_result.json has no boolean passed"
    return graded, None


def grader_block(graded: dict[str, Any] | None) -> dict[str, Any] | None:
    """The trial_result.json view of a grader_result: score, verdict and the criteria that lost points."""
    if not graded:
        return None
    evaluations = []
    for evaluation in graded.get("evaluations") or []:
        possible = evaluation.get("points_possible") or 0
        criteria = evaluation.get("criteria") or {}
        evaluations.append(
            {
                "name": evaluation.get("name"),
                "score": round(evaluation.get("points_awarded", 0) / possible, 4) if possible else None,
                "failed_criteria": {
                    key: {"points": entry.get("points"), "max": entry.get("max"), "reason": entry.get("reason")}
                    for key, entry in criteria.items()
                    if not isinstance(entry.get("points"), int) or entry["points"] < entry.get("max", 0)
                },
            }
        )
    return {
        "overall_score": graded.get("overall_score"),
        "pass_score": graded.get("pass_score"),
        "passed": graded.get("passed"),
        "evaluations": evaluations,
    }


def derive_status(
    *,
    provenance_ok: bool,
    exits: list[int | None],
    graded: dict[str, Any] | None,
    grader_error: str | None,
    harness_error: bool = False,
) -> tuple[str, str | None]:
    """(status, reason). The grader decides pass or fail; the harness only overrides for things it can't grade."""
    if harness_error:
        return "error", "harness exception, see harness_error.txt"
    if not provenance_ok:
        return "error", "provenance mismatch, see provenance.json"
    if None in exits:
        return "timeout", f"segment {exits.index(None)} did not finish"
    if grader_error:
        return "error", grader_error
    if graded is None:
        return "error", "no grader result"
    if graded.get("passed") is True:
        return "passed", None
    return "failed", f"score {graded.get('overall_score')} below pass_score {graded.get('pass_score')}"


@dataclass
class Trial:
    task: dict[str, Any]
    n: int
    profile: dict[str, Any]
    run_dir: Path
    expected: str
    grader_commit: str
    report: Callable[[str, str, str, float | None], None] = lambda *_: None
    dir: Path = field(init=False)
    state: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.name = f"{self.task['id']}-{self.n}"
        self.dir = self.run_dir / "trials" / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = {"trial": self.name, "status": "pending", "stages": {}}
        self._write_state()

    def _write_state(self) -> None:
        (self.dir / "state.json").write_text(json.dumps(self.state, indent=2))

    def _stage(self, name: str, work: Callable[[], Any]) -> Any:
        """Run one stage, recording its transition; exceptions are recorded and re-raised."""
        record = {"started_at": now(), "ended_at": None, "error": None}
        self.state["stages"][name] = record
        self._write_state()
        self.report(self.name, name, "...", None)
        started = time.monotonic()
        try:
            value = work()
            outcome = "ok"
            return value
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            outcome = "error"
            raise
        finally:
            record["ended_at"] = now()
            record["seconds"] = round(time.monotonic() - started, 1)
            if outcome == "ok" and record["error"]:
                outcome = "failed"
            self._write_state()
            self.report(self.name, name, outcome, record["seconds"])

    def _note(self, stage: str, error: str) -> None:
        self.state["stages"][stage]["error"] = error
        self._write_state()

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        spec = self.task["spec"]
        nonce = secrets.token_hex(4)
        universe_id: str | None = None
        verdict: dict[str, Any] | None = None
        exits: list[int | None] = []
        graded: dict[str, Any] | None = None
        grader_error: str | None = None
        harness_error = False
        trial_metrics: dict[str, Any] | None = None
        compose = PROFILES / self.profile["install"] / "compose.yaml"
        try:
            shutil.copyfile(compose, self.dir / "profile.yaml")

            def launch() -> str:
                nonlocal universe_id
                try:
                    launched = universe.launch(
                        compose, self.profile["timeouts"]["launch_seconds"], self.dir / "launch.log"
                    ).id
                except universe.DtuLiteError as error:
                    universe_id = universe.leftover_id(error)
                    raise
                universe_id = launched
                return launched

            launched: str = self._stage("launch", launch)
            verdict = self._stage("provenance", lambda: self._provenance(launched))
            if verdict["ok"]:
                payload = task_json(self.task, self.n, self.profile, nonce)
                (self.dir / "task.json").write_text(json.dumps(payload, indent=2))
                self._stage("seed", lambda: self._seed(launched))
                exits = self._stage("run", lambda: self._run_segments(launched, spec))
                self._stage("pull", lambda: self._pull(launched))
                trial_metrics = self._metrics(started)
                if not (self.dir / "driver" / "result.json").is_file():
                    grader_error = "no driver/result.json to grade"
                else:
                    exit_code, grader_error = self._stage("grade", lambda: self._grade(launched))
                    graded, grader_error = self._stage(
                        "pull-grader", lambda: self._pull_grader(launched, exit_code, grader_error)
                    )
                    if grader_error:
                        self._note("pull-grader", grader_error)
        except Exception:
            harness_error = True
            (self.dir / "harness_error.txt").write_text(traceback.format_exc())
        finally:
            if "metrics" not in self.state["stages"]:
                trial_metrics = self._metrics(started)
            if universe_id is not None:
                destroy_id = universe_id
                with contextlib.suppress(Exception):
                    self._stage("destroy", lambda: universe.destroy(destroy_id))
        status, reason = derive_status(
            provenance_ok=bool(verdict and verdict["ok"]),
            exits=exits,
            graded=graded,
            grader_error=grader_error,
            harness_error=harness_error,
        )
        result = {
            "task": self.task["id"],
            "trial": self.n,
            "install": self.profile["install"],
            "agent": spec.get("agent") or self.profile["agent"],
            "status": status,
            "reason": reason,
            "segments": exits,
            "grader": grader_block(graded),
            "grader_error": grader_error,
            "metrics": trial_metrics,
            "provenance_ok": bool(verdict and verdict["ok"]),
            "durations": {name: stage.get("seconds") for name, stage in self.state["stages"].items()}
            | {"total": round(time.monotonic() - started, 1)},
        }
        self.state["status"] = status
        self._write_state()
        (self.dir / "trial_result.json").write_text(json.dumps(result, indent=2))
        return result

    def _provenance(self, id: str) -> dict[str, Any]:
        for source, name in ((f"{APP}/setup.log", "install.log"), (f"{APP}/installed.json", "installed.json")):
            with contextlib.suppress(universe.DtuLiteError):
                universe.pull(id, source, self.dir / name)
        status = universe.execute(id, f"cat {APP}/.setup-status", timeout_seconds=60).stdout.strip()
        installed: dict[str, Any] = {}
        installed_path = self.dir / "installed.json"
        if installed_path.is_file():
            installed = json.loads(installed_path.read_text())
            installed_path.unlink()
        verdict: dict[str, Any]
        if status != "0":
            verdict = {
                "install": self.profile["install"],
                "installed_commit": None,
                "expected": self.expected,
                "ok": False,
                "reason": f"install status {status!r}, see install.log",
            }
        else:
            verdict = provenance.verdict(installed, self.profile["install"], self.expected)
        (self.dir / "provenance.json").write_text(json.dumps({"installed": installed, "verdict": verdict}, indent=2))
        if not verdict["ok"]:
            self._note("provenance", verdict["reason"])
        return verdict

    def _seed(self, id: str) -> None:
        check = universe.execute(id, f"rm -rf {HOST_DIR} {OUT_DIR} && mkdir -p {OUT_DIR}", timeout_seconds=60)
        if check.exit_code != 0:
            raise RuntimeError(f"mkdir {OUT_DIR} failed: {check.stderr.strip()}")
        seed = self.task["dir"] / "workspace"
        if seed.is_dir():
            for child in sorted(seed.iterdir()):
                universe.push(id, child, "/workspace/")
        for command in self.task["spec"].get("setup") or []:
            outcome = universe.execute(id, command, timeout_seconds=300)
            if outcome.exit_code != 0:
                raise RuntimeError(
                    f"setup command failed ({outcome.exit_code}): {command}\n{outcome.stderr.strip()[-2000:]}"
                )
        universe.push(id, DRIVER, HOST_DIR)
        pushed = {
            key: value
            for key, value in json.loads((self.dir / "task.json").read_text()).items()
            if key not in ("_trial", "name", "description")
        }
        (self.dir / "task.pushed.json").write_text(json.dumps(pushed, indent=2))
        universe.push(id, self.dir / "task.pushed.json", f"{HOST_DIR}/task.json")

    def _run_segments(self, id: str, spec: dict[str, Any]) -> list[int | None]:
        exits: list[int | None] = []
        limit = int(spec["timeout_seconds"]) + 60
        for segment in range(segment_count(spec["turns"])):
            command = f"cd ~/app && uv run host/drive.py --task host/task.json --out {OUT_DIR} --segment {segment}"
            exit_file = f"{OUT_DIR}/segment-{segment}.exit"
            code = universe.run_background(id, command, f"{OUT_DIR}/segment-{segment}.log", exit_file, limit)
            exits.append(code)
            if code is None:
                killed = universe.kill_background(id, exit_file)
                self._note(
                    "run",
                    f"segment {segment} did not finish within {limit}s; kill: {killed.stdout.strip() or killed.stderr.strip()}",
                )
                break
            if code != 0:
                self._note("run", f"segment {segment} exited {code}")
                break
        return exits

    def _pull(self, id: str) -> None:
        universe.pull(id, "/workspace", self.dir / "workspace")
        try:
            universe.pull(id, "/home/agent/.amplifier-agent", self.dir / "sessions")
        except universe.DtuLiteError as error:
            if error.code != "source-not-found":
                raise
        universe.pull(id, OUT_DIR, self.dir / "driver")
        universe.pull(id, f"{APP}/setup.log", self.dir / "install.log")

    def _metrics(self, started: float) -> dict[str, Any] | None:
        """metrics.json from the pulled evidence; None when it cannot be computed."""
        try:
            computed = self._stage("metrics", lambda: metrics.compute(self.dir, time.monotonic() - started))
        except Exception:
            return None
        (self.dir / "metrics.json").write_text(json.dumps(computed, indent=2))
        return computed

    def _grade(self, id: str) -> tuple[int | None, str | None]:
        """Install the grader, push its inputs and run it; (exit code, None) or (None, grader_error)."""
        prepared = universe.execute(
            id,
            f"rm -rf {GRADER_HOME} && mkdir -p {LAYOUT['scratch']} {GRADER_OUT} && echo '{{}}' > {GRADER_HOME}/config.json",
            timeout_seconds=60,
        )
        if prepared.exit_code != 0:
            raise RuntimeError(f"mkdir {GRADER_HOME} failed: {prepared.stderr.strip()}")
        universe.execute(id, f"mkdir -p {GRADER_PROJECT}", timeout_seconds=60)
        for name in GRADER_FILES:
            universe.push(id, GRADER / name, f"{GRADER_PROJECT}/{name}")
        installed = universe.execute(
            id, direct(install_command(self.grader_commit)), timeout_seconds=GRADER_INSTALL_SECONDS
        )
        if installed.exit_code != 0:
            return None, (
                f"grader install exited {installed.exit_code} (sync failure or amplifier-agent not at "
                f"{self.grader_commit}), see grader/install.log"
            )
        grader_data = self.task["dir"] / "grader-data"
        if grader_data.is_dir():
            universe.push(id, grader_data, LAYOUT["grader_data"])
        else:
            universe.execute(id, f"mkdir -p {LAYOUT['grader_data']}", timeout_seconds=60)
        universe.push(id, self.task["dir"] / "grader.yaml", f"{GRADER_HOME}/grader.yaml")
        universe.push(id, self.dir / "task.json", LAYOUT["task"])
        (self.dir / "layout.json").write_text(json.dumps(LAYOUT, indent=2))
        universe.push(id, self.dir / "layout.json", f"{GRADER_HOME}/layout.json")
        exit_file = f"{GRADER_HOME}/grade.exit"
        code = universe.run_background(
            id, direct(grader_command(self.profile["grader"])), f"{GRADER_OUT}/grader.log", exit_file, GRADER_TIMEOUT
        )
        if code is None:
            universe.kill_background(id, exit_file)
            return None, f"grader timed out after {GRADER_TIMEOUT}s"
        return code, None

    def _pull_grader(
        self, id: str, exit_code: int | None, error: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        out = self.dir / "grader"
        if out.exists():
            shutil.rmtree(out)
        universe.pull(id, GRADER_OUT, out)
        if error or exit_code is None:
            return None, error or "grader did not finish"
        return read_grader_result(out, exit_code)
