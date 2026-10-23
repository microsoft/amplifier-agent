"""Lint checks that need no model: broken and missing links, orphans, pages without sources, stale citations."""

from collections import Counter
from dataclasses import dataclass, field

from llm_wiki.wiki.pages import SOURCE, Page, citations, links, resolve

GENERATED = frozenset({"index.md", "log.md"})


@dataclass
class LintReport:
    broken_links: list[tuple[str, str]] = field(default_factory=list)
    missing_pages: list[tuple[str, str]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    no_sources: list[str] = field(default_factory=list)
    stale_citations: list[tuple[str, str, str]] = field(default_factory=list)

    def affected(self) -> dict[str, list[str]]:
        """Findings by the page that holds them, for pages a reduce pass can repair."""
        found: dict[str, list[str]] = {}
        for page, target in self.broken_links:
            found.setdefault(page, []).append(f"Broken link to {target}.")
        for page, target in self.missing_pages:
            found.setdefault(page, []).append(f"Links to {target}, which does not exist.")
        for page in self.no_sources:
            found.setdefault(page, []).append("Lists no sources.")
        for page, cited, current in self.stale_citations:
            found.setdefault(page, []).append(f"Cites {cited}; the repository is now at {current}.")
        return found

    def count(self) -> int:
        return sum(
            len(items)
            for items in (self.broken_links, self.missing_pages, self.orphans, self.no_sources, self.stale_citations)
        )

    def render(self) -> str:
        sections = [
            ("Broken links", [f"{page} -> {target}" for page, target in self.broken_links]),
            ("Missing pages", [f"{page} -> {target}" for page, target in self.missing_pages]),
            ("Orphan pages", self.orphans),
            ("Pages without sources", self.no_sources),
            ("Stale citations", [f"{page}: {cited} (now {current})" for page, cited, current in self.stale_citations]),
        ]
        lines = []
        for heading, items in sections:
            lines += [f"### {heading}", ""]
            lines += [f"- {item}" for item in items] if items else ["- none"]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def lint(pages: list[Page], heads: dict[str, str]) -> LintReport:
    """Code checks that need no model. heads maps owner/repo to the SHA most recently fetched."""
    report = LintReport()
    existing = {page.path for page in pages} | GENERATED
    inbound: Counter[str] = Counter()
    lowered_heads = {repo.lower(): sha for repo, sha in heads.items()}
    for page in pages:
        for href in links(page.body):
            target = resolve(page.path, href)
            if target in existing:
                if target != page.path:
                    inbound[target] += 1
            elif target.split("/", 1)[0] in ("concepts", "entities"):
                report.missing_pages.append((page.path, target))
            else:
                report.broken_links.append((page.path, target))
        if not page.sources:
            report.no_sources.append(page.path)
        cited = {(repo, sha) for repo, sha, _ in citations(page.body)}
        for entry in page.sources:
            match = SOURCE.match(entry)
            if match is not None:
                cited.add((match.group(1), match.group(2)))
        for repo, sha in sorted(cited):
            current = lowered_heads.get(repo.lower())
            if current is not None and not current.startswith(sha):
                report.stale_citations.append((page.path, f"{repo}@{sha}", f"{repo}@{current[: len(sha)]}"))
    for page in pages:
        if page.type not in ("overview", "comparison") and inbound[page.path] == 0:
            report.orphans.append(page.path)
    return report


def lint_sample(pages: list[Page], size: int = 12) -> list[Page]:
    """The overview and the concept and entity pages with the most sources, where contradictions are most likely."""
    shared = sorted(
        (page for page in pages if page.type in ("concept", "entity")), key=lambda page: (-len(page.sources), page.path)
    )
    overview = [page for page in pages if page.path == "overview.md"]
    return (overview + shared)[:size]
