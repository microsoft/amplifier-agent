"""Wiki pages on disk: frontmatter, slugs, links, citations, and the sources each page lists."""

from dataclasses import dataclass
import datetime
from pathlib import Path, PurePosixPath
import posixpath
import re

import yaml

PAGE_DIRS: dict[str, str] = {
    "repos": "repo",
    "concepts": "concept",
    "entities": "entity",
    "comparisons": "comparison",
}

_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")
_CITATION = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{7,40})(?::([^\s`)\]]+))?")
SOURCE = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{7,40})$")
TOKEN = re.compile(r"[a-z0-9]+")
_RELATED = re.compile(r"^##\s+Related\s*$", re.IGNORECASE)
_SECTION_END = re.compile(r"^#{1,2}\s")


@dataclass
class Page:
    path: str
    meta: dict
    body: str

    @property
    def type(self) -> str:
        return str(self.meta.get("type", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or PurePosixPath(self.path).stem)

    @property
    def sources(self) -> list[str]:
        value = self.meta.get("sources") or []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]

    @property
    def summary(self) -> str:
        if self.meta.get("summary"):
            return " ".join(str(self.meta["summary"]).split())
        for block in self.body.split("\n\n"):
            text = " ".join(block.split())
            if text and not text.startswith(("#", "-", "|", ">", "```")):
                return text if len(text) <= 160 else text[:157].rstrip() + "..."
        return ""


def slugify(text: str) -> str:
    return "-".join(TOKEN.findall(text.lower()))


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}, text
    body = text[end + 4 :].lstrip("\n")
    return (meta if isinstance(meta, dict) else {}), body


def render_page(meta: dict, body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{front}\n---\n\n{body.strip()}\n"


def read_page(wiki: Path, relative: str) -> Page | None:
    path = wiki / relative
    if not path.is_file():
        return None
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return Page(relative, meta, body)


def load_pages(wiki: Path) -> list[Page]:
    """Every content page: overview.md and the pages in the page directories, sorted by path."""
    pages = []
    candidates = [wiki / "overview.md", *(path for name in PAGE_DIRS for path in (wiki / name).glob("*.md"))]
    for path in sorted(candidates):
        if path.is_file():
            page = read_page(wiki, path.relative_to(wiki).as_posix())
            if page is not None:
                pages.append(page)
    return pages


def page_type(relative: str) -> str:
    if relative == "overview.md":
        return "overview"
    return PAGE_DIRS.get(relative.split("/", 1)[0], "")


def merge_sources(*groups: list[str]) -> list[str]:
    """Union of owner/repo@sha entries, keeping the last SHA seen for each repository."""
    latest: dict[str, str] = {}
    for group in groups:
        for entry in group:
            match = SOURCE.match(entry.strip())
            if match is not None:
                latest[match.group(1)] = match.group(2)
    return [f"{repo}@{sha}" for repo, sha in sorted(latest.items())]


def normalize_page(page: Page, sources: list[str], updated: str) -> str:
    """The page with code-owned frontmatter fields set: title, type, sources, updated, tags. Drops an empty Related."""
    meta = dict(page.meta)
    meta["title"] = page.title
    meta["type"] = page_type(page.path) or page.type
    meta["sources"] = merge_sources(page.sources, sources)
    meta["updated"] = datetime.date.fromisoformat(updated)
    tags = meta.get("tags") or []
    meta["tags"] = [str(tag) for tag in tags] if isinstance(tags, list) else [str(tags)]
    ordered = {key: meta.pop(key) for key in ("title", "type", "summary", "sources", "updated", "tags") if key in meta}
    return render_page({**ordered, **meta}, strip_empty_related(page.body))


def strip_empty_related(body: str) -> str:
    """Remove a `## Related` section that links nothing, such as one that only says "None yet"."""
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if _RELATED.match(line)), None)
    if start is None:
        return body
    end = next((i for i in range(start + 1, len(lines)) if _SECTION_END.match(lines[i])), len(lines))
    if _LINK.search("\n".join(lines[start + 1 : end])):
        return body
    kept = [*lines[:start], *lines[end:]]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n"


def links(markdown: str) -> list[str]:
    """Relative link targets, without anchors. External and anchor-only links are left out."""
    targets = []
    for _, href in _LINK.findall(markdown):
        if re.match(r"^[a-z][a-z0-9+.-]*:", href) or href.startswith("#"):
            continue
        target = href.split("#", 1)[0]
        if target:
            targets.append(target)
    return targets


def resolve(page_path: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(page_path), href))


def rebase_links(markdown: str, from_dir: str, to_dir: str) -> str:
    """Rewrite relative links written from one wiki directory so they resolve from another."""

    def rewrite(match: re.Match[str]) -> str:
        text, href = match.groups()
        if re.match(r"^[a-z][a-z0-9+.-]*:", href) or href.startswith("#"):
            return match.group(0)
        target, _, anchor = href.partition("#")
        absolute = posixpath.normpath(posixpath.join(from_dir or ".", target))
        moved = posixpath.relpath(absolute, to_dir or ".")
        return f"[{text}]({moved}{'#' + anchor if anchor else ''})"

    return _LINK.sub(rewrite, markdown)


def linked_from(pages: list[Page], target: str) -> list[str]:
    """Paths of the other pages that link to target."""
    return [
        page.path
        for page in pages
        if page.path != target and any(resolve(page.path, href) == target for href in links(page.body))
    ]


def citations(markdown: str) -> list[tuple[str, str, str | None]]:
    return [(repo, sha, path or None) for repo, sha, path in _CITATION.findall(markdown)]


def citation_sources(markdown: str) -> list[str]:
    """The owner/repo@sha of every citation in the text."""
    return merge_sources([f"{repo}@{sha}" for repo, sha, _ in citations(markdown)])


def canonical_key(slug: str) -> str:
    key = slug.replace("-", "")
    return key[:-1] if key.endswith("s") and len(key) > 3 else key


class SlugRegistry:
    """Map proposed page names onto existing slugs that differ only in case, punctuation, or a plural s."""

    def __init__(self, existing: list[str]) -> None:
        self.keys: dict[tuple[str, str], str] = {}
        for page in existing:
            directory, _, name = page.removesuffix(".md").partition("/")
            if name:
                self.keys.setdefault((directory, canonical_key(name)), name)

    def target(self, page: str) -> str | None:
        """The wiki path for a proposed page name, or None when it is not a concept or entity page."""
        directory, _, name = page.strip().strip("/").removesuffix(".md").partition("/")
        if directory not in ("concepts", "entities"):
            return None
        slug = slugify(name)
        if not slug:
            return None
        canonical = self.keys.setdefault((directory, canonical_key(slug)), slug)
        return f"{directory}/{canonical}.md"
