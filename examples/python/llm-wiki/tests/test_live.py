"""Ingest one small repository with a real model, then query the result. Runs only with LLM_WIKI_LIVE=1."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LLM_WIKI_LIVE") != "1", reason="set LLM_WIKI_LIVE=1 to run")

REPO = "microsoft/amplifier-smart-tool-tmux"


def llm_wiki(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "llm_wiki", *arguments, "--home", str(home)],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )


def labelled(output: str, label: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(f"{label}> ")]


def test_ingest_then_query(tmp_path: Path) -> None:
    ingest = llm_wiki(tmp_path, "ingest", "--repo", REPO)

    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    assert any(
        line.startswith(f"ingest> map {REPO}@") and " ok, " in line for line in labelled(ingest.stdout, "ingest")
    )
    assert labelled(ingest.stdout, "usage")[-1].startswith("usage> total sessions=")
    wiki = tmp_path / "wiki"
    assert (wiki / "repos" / "microsoft__amplifier-smart-tool-tmux.md").is_file()
    assert "## Repositories" in (wiki / "index.md").read_text(encoding="utf-8")
    assert f"ingest | {REPO}@" in (wiki / "log.md").read_text(encoding="utf-8")

    query = llm_wiki(tmp_path, "query", "What does the tmux Smart Tool do?", "--file")

    assert query.returncode == 0, query.stdout + query.stderr
    assert labelled(query.stdout, "session")
    assert labelled(query.stdout, "query")[-1].startswith("query> filed comparisons/")
    assert labelled(query.stdout, "usage")
