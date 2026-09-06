"""Run unchanged TypeScript cases through the independent source engine."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEST_FILES = (
    "conformance.test.ts", "sessions.test.ts", "contracts.test.ts", "ecosystem.test.ts",
    "policy-contracts.test.ts", "recovery-contracts.test.ts",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--test-file", action="append", choices=PUBLIC_TEST_FILES)
    parser.add_argument("--test-name-pattern")
    args = parser.parse_args()
    source = ROOT / "packages/typescript"
    if not (source / "dist/index.js").is_file():
        parser.error("Build the TypeScript binding before running replacement acceptance.")
    report = args.report.resolve()
    with tempfile.TemporaryDirectory(prefix="agent-replacement-") as directory:
        consumer = Path(directory)
        shutil.copy2(source / "package.json", consumer / "package.json")
        shutil.copytree(source / "dist", consumer / "dist")
        for name in ("connection", "events", "supervision"):
            for extension in ("js", "d.ts"):
                (consumer / "dist/internal" / f"{name}.{extension}").unlink(missing_ok=True)
        shutil.copytree(source / "test", consumer / "test")
        (consumer / "node_modules").symlink_to(source / "node_modules", target_is_directory=True)
        runtime = consumer / "runtime/linux-x64/amplifier-agent-engine"
        runtime.parent.mkdir(parents=True)
        runtime.write_text(
            f"#!{sys.executable}\n"
            "from conformance.fixtures.replacement_runtime import main\nmain()\n"
        )
        runtime.chmod(0o755)
        participant = runtime.parent / "node-host/index.mjs"
        participant.parent.mkdir()
        shutil.copy2(ROOT / "conformance/fixtures/replacement_host.mjs", participant)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT)
        environment["AMPLIFIER_AGENT_STORAGE"] = str(consumer / "state")
        environment["CONFORMANCE_SCENARIOS"] = str(ROOT / "conformance/scenarios/turns.json")
        environment["CONFORMANCE_SESSION_SCENARIOS"] = str(ROOT / "conformance/scenarios/sessions.json")
        environment["CONFORMANCE_RECORD_SCENARIOS"] = str(ROOT / "conformance/scenarios/records.json")
        command = [
            "node", "--import", "tsx", "--test",
            f"--test-reporter={ROOT / 'conformance/typescript_reporter.mjs'}",
            f"--test-reporter-destination={report}",
        ]
        if args.test_name_pattern:
            command.append(f"--test-name-pattern={args.test_name_pattern}")
        command.extend(str(consumer / "test" / name) for name in
                       args.test_file or PUBLIC_TEST_FILES)
        with subprocess.Popen(command, cwd=consumer, env=environment, start_new_session=True) as process:
            try:
                returncode = process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                print("Replacement acceptance exceeded its 55 second deadline.", file=sys.stderr)
                raise SystemExit(1) from None
        raise SystemExit(returncode)


if __name__ == "__main__":
    main()
