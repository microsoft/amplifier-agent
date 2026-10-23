"""index.md and log.md, both written by code."""

from dataclasses import dataclass, field
from pathlib import Path

from llm_wiki.wiki.pages import Page, page_type

TYPE_ORDER: tuple[str, ...] = ("overview", "repo", "concept", "entity", "comparison")
TYPE_HEADINGS: dict[str, str] = {
    "overview": "Overview",
    "repo": "Repositories",
    "concept": "Concepts",
    "entity": "Entities",
    "comparison": "Comparisons",
}


def render_index(pages: list[Page]) -> str:
    groups: dict[str, list[Page]] = {}
    for page in pages:
        groups.setdefault(page.type or page_type(page.path), []).append(page)
    lines = ["# Index", ""]
    for kind in [*TYPE_ORDER, *sorted(set(groups) - set(TYPE_ORDER))]:
        members = sorted(groups.get(kind, []), key=lambda page: page.title.lower())
        if not members:
            continue
        lines += [f"## {TYPE_HEADINGS.get(kind, kind.title() or 'Other')}", ""]
        for page in members:
            count = len(page.sources)
            line = f"- [{page.title}]({page.path})"
            if page.summary:
                line += f" - {page.summary}"
            line += f" ({count} source{'' if count == 1 else 's'})"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class LogEntry:
    date: str
    operation: str
    subject: str
    details: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"## [{self.date}] {self.operation} | {self.subject}"]
        if self.details:
            lines += ["", *(f"- {detail}" for detail in self.details)]
        return "\n".join(lines) + "\n"


def append_log(wiki: Path, entries: list[LogEntry]) -> None:
    path = wiki / "log.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Log\n"
    blocks = [existing.rstrip("\n"), *(entry.render().rstrip("\n") for entry in entries)]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
