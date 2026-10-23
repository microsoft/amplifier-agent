"""Build and maintain an LLM wiki of the Amplifier ecosystem.

Usage:
    llm-wiki ingest [--mode fast|full] [--repo OWNER/REPO ...] [--rebuild]
    llm-wiki query QUESTION [--file] [--resume SESSION_ID]
    llm-wiki lint [--fix]

Options for every command:
    --home DIR                 where raw/, wiki/, and .state/ live (LLM_WIKI_HOME, default: current directory)
    --provider ID              (LLM_WIKI_PROVIDER, default: openai)
    --model ID                 (LLM_WIKI_MODEL, default: gpt-6-luna)
    --reasoning-effort LEVEL   (LLM_WIKI_REASONING_EFFORT, default: high)
    --jobs N                   model sessions that run at the same time (default: 8)
    --timeout SECONDS          seconds before one session is cancelled (default: 900)

Examples:
    uv run llm-wiki ingest --mode fast
    uv run llm-wiki ingest --repo microsoft/amplifier-smart-tool-tmux
    uv run llm-wiki query "How do providers and orchestrators interact?" --file
    uv run llm-wiki lint
"""

import argparse
import asyncio
import os
from pathlib import Path
import sys

from llm_wiki.agent.providers import check_credentials
from llm_wiki.agent.session import DEFAULT_TIMEOUT
from llm_wiki.pipeline import Settings, ingest, lint_wiki, query


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", type=Path, default=Path(os.environ.get("LLM_WIKI_HOME", ".")))
    common.add_argument("--provider", default=os.environ.get("LLM_WIKI_PROVIDER", "openai"))
    common.add_argument("--model", default=os.environ.get("LLM_WIKI_MODEL", "gpt-6-luna"))
    common.add_argument("--reasoning-effort", default=os.environ.get("LLM_WIKI_REASONING_EFFORT", "high"))
    common.add_argument("--jobs", type=int, default=8, help="model sessions that run at the same time")
    common.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds before one session is cancelled"
    )

    root = argparse.ArgumentParser(
        prog="llm-wiki", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = root.add_subparsers(dest="command", required=True)

    run = commands.add_parser("ingest", parents=[common], help="fetch sources and update the wiki")
    run.add_argument("--mode", choices=["fast", "full"], default="fast")
    run.add_argument("--repo", action="append", default=[], metavar="OWNER/REPO", help="ingest only this repository")
    run.add_argument("--rebuild", action="store_true", help="ingest every selected repository again")

    ask = commands.add_parser("query", parents=[common], help="answer a question from the wiki")
    ask.add_argument("question")
    ask.add_argument("--file", action="store_true", help="save the answer as a comparisons/ page")
    ask.add_argument("--resume", metavar="SESSION_ID", help="continue an earlier query session")

    check = commands.add_parser("lint", parents=[common], help="check the wiki and write a lint report")
    check.add_argument("--fix", action="store_true", help="update the pages with findings")
    return root


async def run(arguments: argparse.Namespace) -> int:
    problem = await check_credentials(arguments.provider)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    settings = Settings(
        home=arguments.home.resolve(),
        provider=arguments.provider,
        model=arguments.model,
        reasoning_effort=arguments.reasoning_effort,
        jobs=arguments.jobs,
        timeout=arguments.timeout,
    )
    if arguments.command == "ingest":
        return await ingest(settings, arguments.mode, arguments.repo, arguments.rebuild)
    if arguments.command == "query":
        return await query(settings, arguments.question, arguments.file, arguments.resume)
    return await lint_wiki(settings, arguments.fix)


def main() -> None:
    sys.exit(asyncio.run(run(parser().parse_args())))


if __name__ == "__main__":
    main()
