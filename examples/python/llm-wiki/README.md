# LLM wiki example

> An example application, not a maintained tool. Read it to learn how to build on Amplifier Agent, and copy what you
> need.

`llm-wiki` builds a cross-linked markdown wiki of the Amplifier repositories, after Andrej Karpathy's
[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern. Model sessions read the
repositories and write the pages, many at once. Plain code does the rest.

## What it shows

Every use of `amplifier_agent` is in [`llm_wiki/agent/`](llm_wiki/agent/), and only its public exports are used. Each
file starts with a docstring that lists what it shows.

- [`session.py`](llm_wiki/agent/session.py): `AgentOptions`, `create_agent`, ephemeral and durable sessions,
  `start_turn` and its events, cancelling a turn, `AgentError`
- [`tools.py`](llm_wiki/agent/tools.py): caller tools, and tool errors the model corrects
- [`approvals.py`](llm_wiki/agent/approvals.py): an approval handler that decides on `request.arguments`, with
  denials returned to the model
- [`usage.py`](llm_wiki/agent/usage.py): tokens and cost from each `TurnResult`
- [`providers.py`](llm_wiki/agent/providers.py): a credential check with `list_providers`

The rest is application code that does not import the library. [`pipeline.py`](llm_wiki/pipeline.py) runs many
sessions in parallel, one per repository or page, and lists the ingest steps. [`WIKI.md`](WIKI.md) is the agent's
instructions, and [`skills/`](skills/) holds the skill some sessions load.

## Run it

You need [uv](https://docs.astral.sh/uv/), Git 2.36 or later, and `OPENAI_API_KEY`. From this folder:

```bash
uv run llm-wiki ingest --mode fast
uv run llm-wiki query "What does the tmux Smart Tool do?"
```

Fast mode ingests 3 repositories in about 5 minutes with the default model, `gpt-6-luna`.
The wiki is written to `wiki/`. Full mode asks the GitHub API whether each repository is archived; set `GITHUB_TOKEN`
to avoid its rate limit for unauthenticated requests. Run `uv run llm-wiki --help` for every command and option.

## Run against the latest commit

`pyproject.toml` pins `amplifier-agent` to a revision of this repository. To use the library in your checkout
instead, run from the repository root:

```bash
uv run --directory examples/python/llm-wiki --with-editable ../../../packages/python llm-wiki ingest --mode fast
```

## Test

```bash
uv run pytest
LLM_WIKI_LIVE=1 uv run pytest tests/test_live.py
```

The live test makes real model calls.
