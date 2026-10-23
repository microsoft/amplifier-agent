"""Ingest, query, and lint. Code does the bookkeeping; each model session, run through llm_wiki.agent, reads and
writes one thing. Sessions run in parallel, bounded by an asyncio.Semaphore of --jobs.

ingest
  1. sync      code    clone or update each repository in raw/, compare its SHA with .state/sources.json
  2. map       model   one session per changed repository: write its page, propose updates for other pages
  3. group     code    collect proposals by target page, drop duplicates, fold near-match slugs
  4. reduce    model   one session per page with new proposals, the only writer of that page
  5. overview  model   one session refreshes overview.md
  6. index     code    rebuild index.md from frontmatter and append to log.md
query          model   one durable, read-only session in wiki/, with search_wiki
lint           code    link, orphan, source, and citation checks, then one model session writes the report
"""

import asyncio
from dataclasses import dataclass
import datetime
import json
from pathlib import Path
import sys
import time

from llm_wiki import prompts
from llm_wiki.agent.session import DEFAULT_TIMEOUT, AgentConfig, Outcome, SessionSpec, print_event, run_turn, with_retry
from llm_wiki.agent.tools import READ_TOOLS, WRITE_TOOLS, map_tools, search_tool
from llm_wiki.agent.usage import Tally
from llm_wiki.console import say
from llm_wiki.sources import RepoState, Source, SourceState, git, short, sync
from llm_wiki.wiki.index import LogEntry, append_log, render_index
from llm_wiki.wiki.lint import lint, lint_sample
from llm_wiki.wiki.pages import (
    Page,
    SlugRegistry,
    citation_sources,
    linked_from,
    links,
    load_pages,
    merge_sources,
    normalize_page,
    read_page,
    rebase_links,
    resolve,
    slugify,
    split_frontmatter,
)
from llm_wiki.wiki.proposals import MapOutput, Proposal

APP_DIR = Path(__file__).resolve().parents[1]
SCHEMA_FILE = APP_DIR / "WIKI.md"
SKILLS_DIR = APP_DIR / "skills"


@dataclass(frozen=True)
class Settings:
    home: Path
    provider: str
    model: str
    reasoning_effort: str
    jobs: int = 8
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def raw(self) -> Path:
        return self.home / "raw"

    @property
    def wiki(self) -> Path:
        return self.home / "wiki"

    @property
    def state(self) -> Path:
        return self.home / ".state"

    @property
    def proposals(self) -> Path:
        return self.state / "proposals"

    @property
    def sessions(self) -> Path:
        return self.state / "sessions"

    @property
    def applied(self) -> Path:
        return self.state / "applied.json"

    async def ensure(self) -> None:
        for directory in (self.raw, self.wiki, self.proposals, self.sessions):
            directory.mkdir(parents=True, exist_ok=True)
        # A repository boundary keeps the enclosing checkout's AGENTS.md out of wiki sessions, and gives the wiki
        # its own history.
        if not (self.wiki / ".git").exists():
            await git("init", "--quiet", str(self.wiki))


def agent_config(settings: Settings, paths: Paths) -> AgentConfig:
    return AgentConfig(
        provider=settings.provider,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        instructions=SCHEMA_FILE.read_text(encoding="utf-8"),
        skills_directory=SKILLS_DIR,
        sessions_directory=paths.sessions,
        timeout=settings.timeout,
    )


def today() -> str:
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def page_paths(wiki: Path) -> list[str]:
    return [page.path for page in load_pages(wiki)]


async def map_repo(
    config: AgentConfig, paths: Paths, source: Source, head: str, tally: Tally, limit: asyncio.Semaphore
) -> MapOutput | str:
    """One session reads one repository and reports through caller tools. Returns its output, or the error."""
    sha = short(head)
    checkout = paths.raw / source.slug
    label = f"map {source.name}@{sha}"

    output = MapOutput()

    async def attempt() -> Outcome:
        nonlocal output
        output = MapOutput()
        spec = SessionSpec(
            phase="map",
            prompt=prompts.map_prompt(
                source, sha, read_text(paths.wiki / "index.md"), read_text(paths.wiki / "repos" / f"{source.slug}.md")
            ),
            working_directory=checkout,
            tools=[*READ_TOOLS, *map_tools(output, source.name, sha, checkout, page_paths(paths.wiki))],
            read_roots=(paths.raw, paths.wiki),
        )
        outcome = await run_turn(config, spec, tally)
        if outcome.ok and output.repo_page is None:
            return Outcome(False, error="the session ended without calling write_repo_page")
        return outcome

    async with limit:
        say("ingest", f"{label} started")
        outcome = await with_retry(label, attempt)
    if not outcome.ok:
        say("ingest", f"{label} failed: {outcome.error}")
        return outcome.error
    say("ingest", f"{label} ok, {len(output.proposals)} proposals")
    return output


def commit_map(paths: Paths, source: Source, head: str, output: MapOutput) -> str:
    """Write the repository page and the proposals. Returns the page path."""
    assert output.repo_page is not None
    relative = f"repos/{source.slug}.md"
    meta, body = split_frontmatter(output.repo_page)
    meta = {**meta, "type": "repo", "repo": source.name, "kind": source.kind, "sources": []}
    meta.setdefault("title", source.name)
    page = Page(relative, meta, body)
    target = paths.wiki / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalize_page(page, [f"{source.name}@{short(head)}"], today()), encoding="utf-8")
    lines = [proposal.to_json() for proposal in output.proposals]
    (paths.proposals / f"{source.slug}.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return relative


def load_applied(paths: Paths) -> set[str]:
    return set(json.loads(paths.applied.read_text(encoding="utf-8"))) if paths.applied.is_file() else set()


def save_applied(paths: Paths, applied: set[str], live: set[str]) -> None:
    paths.applied.write_text(json.dumps(sorted(applied & live), indent=0) + "\n", encoding="utf-8")


def group(paths: Paths, applied: set[str]) -> tuple[dict[str, list[Proposal]], set[str]]:
    """Pending proposals by target page, duplicates dropped, near-match slugs folded. Also every live proposal id."""
    registry = SlugRegistry(page_paths(paths.wiki))
    pending: dict[str, dict[str, Proposal]] = {}
    live: set[str] = set()
    for path in sorted(paths.proposals.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            proposal = Proposal.from_json(line)
            live.add(proposal.id)
            target = registry.target(proposal.page)
            if target is not None and proposal.id not in applied:
                pending.setdefault(target, {})[proposal.id] = proposal
    return {target: list(items.values()) for target, items in sorted(pending.items())}, live


async def reduce_page(
    config: AgentConfig,
    paths: Paths,
    target: str,
    proposals: list[Proposal],
    issues: list[str],
    written: list[str],
    tally: Tally,
    limit: asyncio.Semaphore,
) -> bool:
    """One session, the only writer of the page, merges its proposals or fixes its lint findings."""
    label = f"reduce {target}"
    file = paths.wiki / target
    file.parent.mkdir(parents=True, exist_ok=True)

    async def attempt() -> Outcome:
        spec = SessionSpec(
            phase="reduce",
            prompt=prompts.reduce_prompt(
                target,
                read_text(file),
                proposals,
                issues,
                read_text(paths.wiki / "index.md"),
                linked_from(load_pages(paths.wiki), target),
                written,
            ),
            working_directory=paths.wiki,
            tools=[*READ_TOOLS, *WRITE_TOOLS],
            read_roots=(paths.raw, paths.wiki),
            write_targets=frozenset({file}),
            skills=True,
        )
        outcome = await run_turn(config, spec, tally)
        if outcome.ok and not split_frontmatter(read_text(file))[0]:
            return Outcome(False, error=f"{target} is missing or has no frontmatter")
        return outcome

    async with limit:
        outcome = await with_retry(label, attempt)
    if not outcome.ok:
        say("ingest", f"{label} failed: {outcome.error}")
        return False
    page = read_page(paths.wiki, target)
    assert page is not None
    file.write_text(normalize_page(page, [proposal.source for proposal in proposals], today()), encoding="utf-8")
    say("ingest", f"{label} ok")
    return True


async def refresh_overview(config: AgentConfig, paths: Paths, tally: Tally) -> bool:
    file = paths.wiki / "overview.md"
    spec = SessionSpec(
        phase="overview",
        prompt=prompts.OVERVIEW_PROMPT,
        working_directory=paths.wiki,
        tools=[*READ_TOOLS, *WRITE_TOOLS],
        read_roots=(paths.raw, paths.wiki),
        write_targets=frozenset({file}),
        skills=True,
    )
    outcome = await with_retry("overview", lambda: run_turn(config, spec, tally))
    page = read_page(paths.wiki, "overview.md")
    if not outcome.ok or page is None:
        say("ingest", f"overview failed: {outcome.error or 'overview.md was not written'}")
        return False
    repo_sources = [source for other in load_pages(paths.wiki) if other.type == "repo" for source in other.sources]
    page.meta.setdefault("title", "Overview")
    file.write_text(normalize_page(page, repo_sources, today()), encoding="utf-8")
    say("ingest", "overview ok")
    return True


def write_index(paths: Paths) -> int:
    pages = load_pages(paths.wiki)
    (paths.wiki / "index.md").write_text(render_index(pages), encoding="utf-8")
    return len(pages)


async def ingest(settings: Settings, mode: str, repos: list[str], rebuild: bool) -> int:
    paths = Paths(settings.home)
    await paths.ensure()
    config = agent_config(settings, paths)
    tally = Tally()
    state = SourceState(paths.state / "sources.json")
    with tally.timed("sync"):
        fetched, skipped = await sync(paths.raw, mode, repos, settings.jobs)
    failures: list[str] = []
    for source, head in fetched:
        previous = state.repos.get(source.name)
        state.repos[source.name] = RepoState(head, previous.ingested if previous else None)
    changed = [(source, head) for source, head in fetched if rebuild or state.changed(source.name)]
    say("sync", f"{len(fetched)} sources, {len(changed)} to ingest, {len(skipped)} skipped")
    for item in skipped:
        say("sync", f"skipped {item}")

    limit = asyncio.Semaphore(settings.jobs)
    with tally.timed("map"):
        results = await asyncio.gather(*(map_repo(config, paths, s, h, tally, limit) for s, h in changed))
    log: list[LogEntry] = []
    mapped = 0
    for (source, head), output in zip(changed, results, strict=True):
        subject = f"{source.name}@{short(head)}"
        if isinstance(output, str):
            failures.append(f"map {subject}: {output}")
            log.append(LogEntry(today(), "ingest", f"{subject} (failed)", [output]))
            continue
        relative = commit_map(paths, source, head, output)
        state.repos[source.name].ingested = head
        mapped += 1
        pages = sorted({proposal.page for proposal in output.proposals})
        details = [f"[{relative}]({relative})", f"{len(output.proposals)} proposals for {len(pages)} pages"]
        log.append(LogEntry(today(), "ingest", subject, details))
    state.save()

    applied = set() if rebuild else load_applied(paths)
    groups, live = group(paths, applied)
    count = sum(len(items) for items in groups.values())
    say("ingest", f"group {count} proposals for {len(groups)} pages")
    with tally.timed("reduce"):
        reduced = await asyncio.gather(
            *(
                reduce_page(config, paths, target, items, [], list(groups), tally, limit)
                for target, items in groups.items()
            )
        )
    for (target, items), ok in zip(groups.items(), reduced, strict=True):
        if ok:
            applied.update(proposal.id for proposal in items)
        else:
            failures.append(f"reduce {target}")
    save_applied(paths, applied, live)

    if mapped or any(reduced):
        write_index(paths)
        with tally.timed("overview"):
            if not await refresh_overview(config, paths, tally):
                failures.append("overview")
    pages = write_index(paths)
    say("ingest", f"index {pages} pages")

    minutes = (time.monotonic() - tally.started) / 60
    summary = [
        f"{mapped} of {len(changed)} repositories mapped",
        f"{sum(reduced)} of {len(groups)} pages reduced",
        f"{len(skipped)} skipped",
        f"{len(failures)} failures",
        f"{minutes:.1f} minutes",
    ]
    log.append(
        LogEntry(
            today(),
            "ingest",
            f"run ({mode if not repos else ', '.join(repos)})",
            [*summary, *skipped, *(f"failed: {item}" for item in failures)],
        )
    )
    append_log(paths.wiki, log)
    for failure in failures:
        say("failed", failure)
    tally.report()
    say("ingest", "; ".join(summary))
    return 1 if failures else 0


async def query(settings: Settings, question: str, file: bool, resume: str | None) -> int:
    paths = Paths(settings.home)
    if not (paths.wiki / "index.md").is_file():
        print("The wiki is empty. Run `llm-wiki ingest` first.", file=sys.stderr)
        return 2
    tally = Tally()
    spec = SessionSpec(
        phase="query",
        prompt=prompts.query_prompt(question),
        working_directory=paths.wiki,
        tools=[*READ_TOOLS, search_tool(paths.wiki)],
        read_roots=(paths.raw, paths.wiki),
    )
    with tally.timed("query"):
        outcome = await run_turn(
            agent_config(settings, paths), spec, tally, print_event, persistence="durable", resume=resume
        )
    print(flush=True)
    if not outcome.ok:
        say("failed", f"query: {outcome.error}")
        tally.report()
        return 1
    entries = [LogEntry(today(), "query", " ".join(question.split())[:100])]
    if file:
        relative = file_answer(paths, question, outcome.answer)
        entries[0].details.append(f"filed as [{relative}]({relative})")
        say("query", f"filed {relative}")
        write_index(paths)
    append_log(paths.wiki, entries)
    tally.report()
    return 0


def file_answer(paths: Paths, question: str, answer: str) -> str:
    """Save the answer as a comparisons/ page. Its sources are the answer's citations, else those of the pages it
    links."""
    relative = f"comparisons/{slugify(question)[:60].rstrip('-') or 'answer'}.md"
    body = rebase_links(answer, "", "comparisons")
    cited = citation_sources(body)
    if not cited:
        for href in links(body):
            page = read_page(paths.wiki, resolve(relative, href))
            if page is not None:
                cited += page.sources
    meta = {"title": question.strip().rstrip("?") + "?", "type": "comparison", "tags": ["query"]}
    page = Page(relative, meta, f"# {meta['title']}\n\n{body}")
    target = paths.wiki / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalize_page(page, cited, today()), encoding="utf-8")
    return relative


async def lint_wiki(settings: Settings, fix: bool) -> int:
    paths = Paths(settings.home)
    pages = load_pages(paths.wiki)
    if not pages:
        print("The wiki is empty. Run `llm-wiki ingest` first.", file=sys.stderr)
        return 2
    config = agent_config(settings, paths)
    state = SourceState(paths.state / "sources.json")
    report = lint(pages, state.heads())
    say(
        "lint",
        f"{len(report.broken_links)} broken links, {len(report.missing_pages)} missing pages, "
        f"{len(report.orphans)} orphans, {len(report.no_sources)} without sources, "
        f"{len(report.stale_citations)} stale citations",
    )
    tally = Tally()
    relative = f"comparisons/lint-{today()}.md"
    file = paths.wiki / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    sample = lint_sample(pages)
    spec = SessionSpec(
        phase="lint",
        prompt=prompts.lint_prompt(report, sample, relative),
        working_directory=paths.wiki,
        tools=[*READ_TOOLS, "write_file"],
        read_roots=(paths.raw, paths.wiki),
        write_targets=frozenset({file}),
        skills=True,
    )
    with tally.timed("lint"):
        outcome = await with_retry("lint", lambda: run_turn(config, spec, tally))
    failures = 0
    entry = LogEntry(today(), "lint", f"code findings: {report.count()}")
    page = read_page(paths.wiki, relative)
    if outcome.ok and page is not None:
        page.meta.setdefault("title", f"Lint report {today()}")
        page.meta["tags"] = ["lint"]
        sampled = [source for item in sample for source in item.sources]
        page.body = f"{page.body.rstrip()}\n\n## Code checks\n\n{report.render()}"
        file.write_text(normalize_page(page, merge_sources(sampled), today()), encoding="utf-8")
        entry.details.append(f"report [{relative}]({relative})")
        say("lint", f"report {relative}")
    else:
        failures += 1
        say("failed", f"lint: {outcome.error or 'the report was not written'}")
    if fix:
        limit = asyncio.Semaphore(settings.jobs)
        affected = report.affected()
        with tally.timed("reduce"):
            fixed = await asyncio.gather(
                *(
                    reduce_page(config, paths, target, [], issues, list(affected), tally, limit)
                    for target, issues in affected.items()
                )
            )
        failures += fixed.count(False)
        entry.details.append(f"fixed {sum(fixed)} of {len(affected)} pages")
    write_index(paths)
    append_log(paths.wiki, [entry])
    tally.report()
    return 1 if failures else 0
