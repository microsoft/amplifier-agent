"""An always-on personal assistant in the terminal, with memory kept as plain Markdown in a home directory.

Usage:
    assistant [--home PATH] [--heartbeat-minutes N] [--model ID]

Chat commands:
    /heartbeat    run a heartbeat now
    /quit         exit (so do Ctrl+D and Ctrl+C)

Chat lines and heartbeats share one queue, and one worker takes one turn at a time.
"""

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import shutil
import sys
import threading

from personal_assistant.agent.providers import check_credentials
from personal_assistant.agent.session import DEFAULT_MODEL, PROVIDER, Assistant
from personal_assistant.console import note, say
from personal_assistant.heartbeat import HEARTBEAT, QUIET, cheap_check, heartbeat_prompt, tick
from personal_assistant.memory import Home

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
QUIT = "/quit"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="assistant", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    root.add_argument("--home", type=Path, default=Path("home"), help="the memory directory (default: ./home)")
    root.add_argument("--heartbeat-minutes", type=float, default=30.0, help="minutes between heartbeats (default: 30)")
    root.add_argument("--model", default=DEFAULT_MODEL, help=f"the {PROVIDER} model (default: {DEFAULT_MODEL})")
    return root


def seed(home: Home) -> None:
    """Create what is missing in the home directory. Existing files are the user's and are never overwritten."""
    home.logs.mkdir(parents=True, exist_ok=True)
    for template in sorted(TEMPLATES.glob("*.md")):
        if not (home.root / template.name).exists():
            shutil.copyfile(template, home.root / template.name)
    if not home.memory.exists():
        home.memory.write_text("# Memory\n\n", encoding="utf-8")
    if not home.reminders.exists():
        home.save_reminders([])


def read_chat(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
    # A daemon thread, because a thread blocked in input() would otherwise keep the process alive after Ctrl+C.
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            line = QUIT
        if line:
            asyncio.run_coroutine_threadsafe(submit(queue, line), loop).result()
        if line == QUIT:
            return


async def submit(queue: asyncio.Queue[str], line: str) -> None:
    """Queue a line, then wait until the worker has finished everything queued so the prompt shows after the reply."""
    queue.put_nowait(line)
    await queue.join()


async def heartbeat(home: Home, assistant: Assistant) -> None:
    now = datetime.now()
    due = cheap_check(home, now)
    if due is None:
        return
    reply = await assistant.heartbeat(heartbeat_prompt(due, now))
    if reply is None:
        return
    home.remove_reminders(due.reminders)
    if reply.strip() != QUIET:
        say(f"\n[heartbeat] {reply.strip()}")
        home.append_log("heartbeat", reply)


async def work(queue: asyncio.Queue[str], home: Home, assistant: Assistant) -> None:
    while True:
        line = await queue.get()
        try:
            if line == QUIT:
                return
            if line == HEARTBEAT:
                await heartbeat(home, assistant)
            else:
                home.append_log("user", line)
                reply = await assistant.chat(line)
                if reply is not None:
                    home.append_log("assistant", reply)
        finally:
            queue.task_done()


async def run(arguments: argparse.Namespace) -> int:
    problem = await check_credentials(PROVIDER)
    if problem is not None:
        note(problem)
        return 2
    home = Home(arguments.home.resolve())
    seed(home)
    note(f"Memory is in {home.root}. Type {HEARTBEAT} to run a heartbeat now, {QUIT} to exit.")
    queue: asyncio.Queue[str] = asyncio.Queue()
    timer = asyncio.create_task(tick(queue, arguments.heartbeat_minutes))
    threading.Thread(target=read_chat, args=(asyncio.get_running_loop(), queue), daemon=True).start()
    try:
        await work(queue, home, Assistant(home, arguments.model))
    finally:
        timer.cancel()
    return 0


def main() -> None:
    arguments = parser().parse_args()
    if arguments.heartbeat_minutes <= 0:
        parser().error("--heartbeat-minutes must be greater than 0")
    try:
        sys.exit(asyncio.run(run(arguments)))
    except KeyboardInterrupt:
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
