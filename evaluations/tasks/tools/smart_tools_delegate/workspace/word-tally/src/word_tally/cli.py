"""word-tally: count the words in a piece of text."""

import argparse
import json
from pathlib import Path
import sys

NAME = "word-tally"
MANIFEST = Path(__file__).resolve().parent / "SMART_TOOL.md"


def frontmatter() -> tuple[list[str], str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    return lines[fences[0] + 1 : fences[1]], "\n".join(lines[fences[1] + 1 :]).strip()


def version() -> str:
    for line in frontmatter()[0]:
        key, _, value = line.partition(":")
        if key.strip() == "version":
            return value.strip()
    raise SystemExit("SMART_TOOL.md has no version")


def skill() -> str:
    return "\n".join(
        [
            f'<skill_content name="{NAME}">',
            f"# {NAME}",
            "",
            frontmatter()[1],
            "",
            "## Capabilities",
            "",
            f"- `count` [deterministic] -- Count the words in TEXT. Details: `{NAME} count --help`.",
            "</skill_content>",
        ]
    )


def summary() -> str:
    return f"usage: {NAME} <capability> [options]\n\ncapabilities:\n  count  [deterministic]  Count the words in TEXT."


class Show(argparse.Action):
    def __init__(self, option_strings, render, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)
        self.render = render

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stdout.write(self.render() + "\n")
        parser.exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=NAME, add_help=False)
    parser.add_argument("-h", action=Show, render=summary)
    parser.add_argument("--help", action=Show, render=skill)
    parser.add_argument("-V", "--version", action=Show, render=version)
    commands = parser.add_subparsers(dest="command")
    count = commands.add_parser(
        "count",
        description='[deterministic] Count the words in TEXT. Prints {"words": int}. Exit codes: 0 success, 2 bad invocation.',
    )
    count.add_argument("--text", required=True, help="The text to count.")
    args = parser.parse_args(argv)
    if args.command != "count":
        parser.error("name a capability: count")
    json.dump({"words": len(args.text.split())}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
