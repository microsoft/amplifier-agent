# /// script
# requires-python = ">=3.13,<3.15"
# dependencies = [
#     "reality-check[amplifier-agent] @ git+https://github.com/microsoft/amplifier-smart-tool-reality-check@2408911ba70a19ef016623a0bf92a679ee5bf9c5",
# ]
# ///
"""Check the personal assistant the way its user would, in a Digital Twin Universe built from this working tree.

Usage, from the example folder:
    uv run tests/reality-check/check.py [--keep-running]

An agent launches the universe in profile/, chats with the assistant in its terminal, and writes a report with the
evidence for each item in request.md. Exits 0 when the check finished and the report ends with VERDICT: PASS.

This is a script of its own because reality-check's amplifier-agent extra pins another amplifier-agent than the app.
"""

import argparse
import asyncio
from pathlib import Path
import sys

from reality_check.lib import CheckEvent, CheckResult, CheckTimeoutError, check_software
from reality_check.schemas import RealityCheckError

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile"
REQUEST = HERE / "request.md"
CHECKS = HERE.parents[1] / ".state" / "reality-checks"
PASS = "VERDICT: PASS"
TIMEOUT_SECONDS = 2 * 60 * 60
SHOWN = ("progress", "tool", "steered", "failed")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="check", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    root.add_argument(
        "--keep-running", action="store_true", help="leave the universe running afterwards, to look around in it"
    )
    return root


def show(event: CheckEvent) -> None:
    if event.kind in SHOWN:
        line = " ".join(event.text.split())
        print(f"{event.kind}> {line[:160]}", file=sys.stderr, flush=True)


def verdict(report: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def summary(result: CheckResult) -> None:
    usage = result.usage
    billing = ", ".join(f"{amount:.2f} {unit}" for unit, amount in usage.billing.items())
    print(
        f"{result.status} in {result.elapsed_seconds / 60:.1f} minutes, {usage.model_calls} model calls, "
        f"{usage.fresh_input_tokens} fresh input, {usage.cache_read_tokens} cached input, "
        f"{usage.output_tokens} output tokens{f', {billing}' if billing else ''}",
        file=sys.stderr,
    )
    print(f"Next: {result.next}", file=sys.stderr)


async def run(keep_running: bool) -> int:
    try:
        check = await check_software(
            request=REQUEST.read_text(encoding="utf-8"),
            target=str(PROFILE),
            ask_first="never",
            keep_running=keep_running,
            checks_directory=CHECKS,
            agent_provider="amplifier-agent",
            model="openai/gpt-6.1-sol",
            reasoning_effort="medium",
            timeout_seconds=TIMEOUT_SECONDS,
            origin="personal-assistant:reality-check",
        )
    except RealityCheckError as error:
        print(f"error> {error.code}: {error}", file=sys.stderr)
        return 2
    print(f"Check {check.check_id} in {check.directory}", file=sys.stderr, flush=True)
    async for event in check.events():
        show(event)
    try:
        result = await check.result()
    except CheckTimeoutError as error:
        result = error.result
    except RealityCheckError as error:
        print(f"error> {error.code}: {error}", file=sys.stderr)
        return 2
    summary(result)
    print(result.directory / "report.md")
    return 0 if result.status == "done" and verdict(result.report) == PASS else 1


def main() -> None:
    arguments = parser().parse_args()
    sys.exit(asyncio.run(run(arguments.keep_running)))


if __name__ == "__main__":
    main()
