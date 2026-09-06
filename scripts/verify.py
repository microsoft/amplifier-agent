"""Verify the checkout with controlled providers, or explicitly smoke-test a live model."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ("Python binding units", ["packages/python/tests"]),
    ("Python public APIs", ["tests/e2e/python"]),
    ("HTTP projection units", ["packages/http/tests"]),
    ("HTTP public APIs", ["tests/e2e/http"]),
    ("Engine units", ["packages/engine/tests"]),
    ("Python provider integrations", ["tests/integration"]),
    ("Verification runner", ["conformance/tests/test_verification_script.py"]),
]
CREDENTIALS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}
URLS = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "gemini": "GOOGLE_GEMINI_BASE_URL",
}


def isolated_environment(directory: Path) -> dict[str, str]:
    """Use temporary agent settings without inheriting provider credentials or test filters."""
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith((
            "AMPLIFIER_", "ANTHROPIC_", "OPENAI_", "GOOGLE_", "GEMINI_", "AZURE_", "E2E_",
            "PYTEST_",
        )) and key not in {"PYTHONPATH", "PYTHONOPTIMIZE"}
    }
    config = directory / "config.json"
    config.write_text("{}\n")
    environment.update(
        AMPLIFIER_AGENT_CONFIG=str(config),
        AMPLIFIER_AGENT_STORAGE=str(directory / "sessions"),
        PYTHONUNBUFFERED="1",
    )
    return environment


def run(command: list[str], log: Path, environment: dict[str, str], *, cwd: Path = ROOT) -> bool:
    """Keep diagnostics on disk and stop the process group if a check exceeds 115 seconds."""
    with log.open("w") as output:
        output.write("Command: " + shlex.join(command) + "\n\n")
        output.flush()
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
        except OSError as error:
            output.write(f"Cannot start check: {error}\n")
            return False
        try:
            return process.wait(timeout=115) == 0
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=5)
            if isinstance(error, KeyboardInterrupt):
                raise
            output.write("\nCheck exceeded 115 seconds.\n")
            return False


def passed_cases(report: Path) -> int:
    """A missing, empty, skipped, or failing report cannot count as a passing check."""
    try:
        cases = list(ET.parse(report).iter("testcase"))
    except (OSError, ET.ParseError):
        return 0
    if not cases or any(
        case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
    ):
        return 0
    return len(cases)


def deterministic(logs: Path, environment: dict[str, str]) -> bool:
    results = []
    for number, (label, paths) in enumerate(GROUPS, start=1):
        log, report = logs / f"{number}.log", logs / f"{number}.xml"
        print(f"RUN   {label}", flush=True)
        started = time.monotonic()
        succeeded = run([
            sys.executable, "-m", "pytest", "-q", "--color=no", "--tb=short",
            f"--junitxml={report}", *paths,
        ], log, environment)
        count = passed_cases(report)
        succeeded = succeeded and count > 0
        results.append(succeeded)
        detail = f"{count} tests" if succeeded else f"see {log}"
        print(f"{'PASS' if succeeded else 'FAIL'}  {label}: {detail} "
              f"({time.monotonic() - started:.1f}s)", flush=True)
    print(f"\n{sum(results)}/{len(results)} groups passed.")
    print("Controlled local services; no live model calls. TypeScript and full conformance are separate.")
    return all(results)


def live(logs: Path, directory: Path, environment: dict[str, str], provider: str, model: str) -> bool:
    environment.update(AMPLIFIER_AGENT_PROVIDER=provider, AMPLIFIER_AGENT_MODEL=model)
    for name in CREDENTIALS[provider]:
        if os.environ.get(name):
            environment[CREDENTIALS[provider][0]] = os.environ[name]
            break
    url = URLS[provider]
    if os.environ.get(url):
        environment[url] = os.environ[url]
        print(f"Using the configured {url} endpoint.")
    environment["E2E_EXPECTED_HISTORY"] = str(directory / "history.json")
    print(f"Live Python smoke test: {provider} / {model}. Model calls may incur charges.")
    print("Only record_probe is approved; its effect is held in memory.")
    pids = []
    for mode, label in (
        ("create", "Stream a turn and execute one approved caller tool"),
        ("resume", "Reopen in a new process and recall the durable conversation"),
    ):
        print(f"RUN   {label}", flush=True)
        environment["E2E_MODE"] = mode
        log = logs / f"live-{mode}.log"
        succeeded = run(
            [sys.executable, str(ROOT / "tests/e2e/live.py")], log, environment, cwd=directory,
        )
        reports = []
        for line in log.read_text(errors="replace").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("kind") == "result":
                reports.append(value)
        expected = {
            "state": "success", "streamed": True,
            "effects": 1 if mode == "create" else 0,
            "history_count": 1 if mode == "create" else 2,
        }
        succeeded = succeeded and len(reports) == 1 and all(
            reports[0].get(key) == value for key, value in expected.items()
        ) and type(reports[0].get("pid")) is int and reports[0]["pid"] not in pids
        if not succeeded:
            print(f"FAIL  {label}: see {log}")
            return False
        pids.append(reports[0]["pid"])
        print(f"PASS  {label}", flush=True)
    print("\n2/2 live checks passed. Temporary session data will be removed.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Run from the checkout with: uv run --all-packages python scripts/verify.py",
    )
    parser.add_argument("--live", choices=CREDENTIALS, metavar="PROVIDER",
                        help="Run only a live Python smoke test: anthropic, openai, or gemini.")
    parser.add_argument("--model", help="Exact model to use with --live (required; no default).")
    args = parser.parse_args()
    if bool(args.live) != bool(args.model):
        parser.error("--live PROVIDER and --model MODEL must be supplied together.")
    if args.live == "anthropic" and args.model not in {"claude-sonnet-5", "claude-opus-5"}:
        parser.error("Anthropic verification requires claude-sonnet-5 or claude-opus-5.")
    if args.live and not any(os.environ.get(name) for name in CREDENTIALS[args.live]):
        parser.error("Set " + " or ".join(CREDENTIALS[args.live]) + " for the selected provider.")

    logs_root = ROOT / "build/verification"
    logs_root.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-")
    logs = Path(tempfile.mkdtemp(prefix=prefix, dir=logs_root))
    print(f"Diagnostics: {logs}\n", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="amplifier-agent-verify-") as temporary:
            directory = Path(temporary)
            environment = isolated_environment(directory)
            succeeded = (
                live(logs, directory, environment, args.live, args.model)
                if args.live else deterministic(logs, environment)
            )
    except KeyboardInterrupt:
        print(f"\nInterrupted. Diagnostics: {logs}")
        return 130
    print(f"Diagnostics: {logs}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
