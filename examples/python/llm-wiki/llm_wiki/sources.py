"""Discover the Amplifier repositories, clone them, and remember which commit each one was ingested at."""

import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from llm_wiki.console import say

SEEDS: dict[str, str] = {
    "microsoft/amplifier": "app",
    "microsoft/amplifier-core": "core",
    "microsoft/amplifier-foundation": "library",
    "microsoft/amplifier-app-cli": "app",
    "microsoft/amplifier-agent": "app",
    "microsoft/amplifier-smart-tools": "smart-tool",
    "microsoft/amplifier-smart-tools-catalog": "smart-tool",
}

# Three small repositories that depend on each other: the kernel, a provider module it loads, and a Smart Tool that
# calls a provider.
FAST: tuple[str, ...] = (
    "microsoft/amplifier-core",
    "microsoft/amplifier-module-provider-openai",
    "microsoft/amplifier-smart-tool-tmux",
)

# Linked from the catalogs as dependencies or upstream projects, not as Amplifier components.
EXCLUDE: frozenset[str] = frozenset(
    {
        "github/copilot-cli",
        "openai/codex",
        "paperclipai/paperclip",
        "anomalyco/opencode",
    }
)

# The catalogs that list every other repository.
INDEX_REPOS = ("microsoft/amplifier", "microsoft/amplifier-smart-tools-catalog")
SHORT_SHA = 7

MODULES_PATH = "docs/MODULES.md"
CATALOG_TOOLS_PATH = "tools"

# Headings are matched by prefix so "Community Providers" style variants still resolve.
SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("core infrastructure", "core"),
    ("applications", "app"),
    ("documentation", "docs"),
    ("libraries", "library"),
    ("bundles", "bundle"),
    ("orchestrators", "orchestrator"),
    ("providers", "provider"),
    ("tools", "tool"),
    ("context managers", "context-manager"),
    ("hooks", "hook"),
    ("community", "community"),
    ("deprecated", "deprecated"),
)

_REPO_LINK = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class Source:
    owner: str
    repo: str
    kind: str
    description: str = ""

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def slug(self) -> str:
        return f"{self.owner}__{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"


def repo_name(owner: str, repo: str) -> str | None:
    """Normalize an owner and repository taken from a link, or None when it is not a repository."""
    repo = repo.removesuffix(".git")
    if not repo or repo.startswith("."):
        return None
    return f"{owner}/{repo}"


def section_kind(heading: str, parent: str | None) -> str | None:
    text = heading.strip().lower()
    if parent == "community":
        return "community"
    for prefix, kind in SECTION_KINDS:
        if text.startswith(prefix):
            return kind
    return None


def parse_modules(text: str) -> list[Source]:
    """Read every repository in the Repository column of the MODULES.md tables, with its section as its kind."""
    found: list[Source] = []
    top: str | None = None
    kind: str | None = None
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith("## "):
            top = section_kind(line[3:], None)
            kind = top
            continue
        if line.startswith("### "):
            kind = section_kind(line[4:], top) or top
            continue
        if not line.startswith("|") or kind is None:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= {"-", ":", " "}:
            continue
        description = cells[1].replace("**", "") if len(cells) > 2 else ""
        for owner, repo in _REPO_LINK.findall(cells[-1]):
            name = repo_name(owner, repo)
            if name is not None:
                found.append(Source(*name.split("/"), kind=kind, description=description))
    return found


def parse_catalog(tools_dir: Path) -> list[Source]:
    """Read the repository of every Smart Tool in a catalog's tools directory."""
    tools: dict[str, list[str]] = {}
    for source_file in sorted(tools_dir.glob("*/source.json")):
        data = json.loads(source_file.read_text(encoding="utf-8"))
        match = _REPO_LINK.match(str(data.get("repository", "")))
        if match is None:
            continue
        name = repo_name(*match.groups())
        if name is not None:
            tools.setdefault(name, []).append(source_file.parent.name)
    return [
        Source(*name.split("/"), kind="smart-tool", description=f"Smart Tools: {', '.join(names)}")
        for name, names in tools.items()
    ]


def merge(*groups: list[Source]) -> list[Source]:
    """Deduplicate by owner/repo, case-insensitively. Later groups override the kind; the first description stays."""
    excluded = {name.lower() for name in EXCLUDE}
    merged: dict[str, Source] = {}
    for group in groups:
        for source in group:
            key = source.name.lower()
            if key in excluded:
                continue
            previous = merged.get(key)
            if previous is None:
                merged[key] = source
            else:
                merged[key] = Source(
                    previous.owner,
                    previous.repo,
                    kind=source.kind,
                    description=previous.description or source.description,
                )
    return sorted(merged.values(), key=lambda source: source.name.lower())


def seed_sources() -> list[Source]:
    return [Source(*name.split("/"), kind=kind) for name, kind in SEEDS.items()]


def discover(raw: Path) -> list[Source]:
    """Every source: MODULES.md, the Smart Tools catalog, and the seeds, read from their clones under raw."""
    modules_file = raw / "microsoft__amplifier" / MODULES_PATH
    tools_dir = raw / "microsoft__amplifier-smart-tools-catalog" / CATALOG_TOOLS_PATH
    modules = parse_modules(modules_file.read_text(encoding="utf-8")) if modules_file.is_file() else []
    catalog = parse_catalog(tools_dir) if tools_dir.is_dir() else []
    found = {source.name.lower(): source for source in merge(modules, catalog)}
    for seed in seed_sources():
        listed = found.get(seed.name.lower())
        found[seed.name.lower()] = Source(seed.owner, seed.repo, seed.kind, listed.description if listed else "")
    return sorted(found.values(), key=lambda source: source.name.lower())


def select(sources: list[Source], mode: str, repos: list[str]) -> list[Source]:
    """The sources one run works on: named repositories, the fast set, or everything."""
    by_name = {source.name.lower(): source for source in sources}
    if repos:
        names = repos
    elif mode == "fast":
        names = list(FAST)
    else:
        return sources
    picked = []
    for name in names:
        owner, _, repo = name.partition("/")
        picked.append(by_name.get(name.lower(), Source(owner, repo, kind="unknown")))
    return picked


class GitError(Exception):
    pass


async def git(*args: str, cwd: Path | None = None, stdin: str | None = None) -> str:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env=environment,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(stdin.encode() if stdin is not None else None)
    if process.returncode != 0:
        raise GitError(stderr.decode(errors="replace").strip() or f"git {args[0]} exited {process.returncode}")
    return stdout.decode().strip()


# Blobs above this size are images, videos, lock files, and generated data. A partial clone never downloads them,
# and the sparse checkout leaves them out of the working tree, so checkout does not fetch them one by one either.
BLOB_LIMIT = "100k"


def sparse_patterns(paths: list[str]) -> str:
    """Non-cone sparse-checkout patterns that check out everything except the given paths."""
    lines = ["/*"]
    for path in paths:
        escaped = "".join(f"\\{character}" if character in "\\*?[!#" else character for character in path)
        lines.append(f"!/{escaped}")
    return "\n".join(lines) + "\n"


async def missing_paths(commit: str, cwd: Path) -> list[str]:
    """Paths in the commit whose blobs the partial clone filtered out."""
    listed = await git("rev-list", "--objects", "--missing=print", commit, cwd=cwd)
    missing = {line[1:] for line in listed.splitlines() if line.startswith("?")}
    if not missing:
        return []
    tree = await git("ls-tree", "-r", "-z", "--format=%(objectname) %(path)", commit, cwd=cwd)
    entries = (entry.partition(" ") for entry in tree.split("\0") if entry)
    return sorted(path for objectname, _, path in entries if objectname in missing)


async def fetch(source: Source, raw: Path) -> str:
    """Shallow, blob-filtered clone, or update an existing clone to the remote HEAD. Returns the HEAD SHA."""
    destination = raw / source.slug
    filtered = f"--filter=blob:limit={BLOB_LIMIT}"
    if (destination / ".git").is_dir():
        await git("fetch", "--depth", "1", filtered, "--quiet", "origin", "HEAD", cwd=destination)
        commit = "FETCH_HEAD"
    else:
        raw.mkdir(parents=True, exist_ok=True)
        await git("clone", "--depth", "1", filtered, "--no-checkout", "--quiet", source.url, str(destination))
        commit = "HEAD"
    patterns = sparse_patterns(await missing_paths(commit, destination))
    await git("sparse-checkout", "set", "--no-cone", "--stdin", cwd=destination, stdin=patterns)
    await git("reset", "--hard", "--quiet", commit, cwd=destination)
    return await git("rev-parse", "HEAD", cwd=destination)


def is_archived(source: Source) -> bool:
    """Ask the GitHub API whether a repository is archived. Unknown answers count as not archived."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{source.owner}/{source.repo}",
        headers={"Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.load(response).get("archived", False))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


@dataclass
class RepoState:
    head: str
    ingested: str | None = None


class SourceState:
    """The last fetched and last ingested SHA of every repository, in .state/sources.json."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.repos: dict[str, RepoState] = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.repos = {name: RepoState(value["head"], value.get("ingested")) for name, value in data.items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: asdict(state) for name, state in sorted(self.repos.items())}
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def changed(self, name: str) -> bool:
        state = self.repos.get(name)
        return state is None or state.ingested != state.head

    def heads(self) -> dict[str, str]:
        return {name: state.head for name, state in self.repos.items()}


def short(sha: str) -> str:
    return sha[:SHORT_SHA]


async def fetch_reported(source: Source, raw: Path) -> str:
    """Fetch one repository, printing a line when it starts and when it lands, since a clone can take a while."""
    action = "updating" if (raw / source.slug / ".git").is_dir() else "cloning"
    say("sync", f"{action} {source.name}")
    head = await fetch(source, raw)
    say("sync", f"{source.name}@{short(head)}")
    return head


async def sync(raw: Path, mode: str, repos: list[str], jobs: int) -> tuple[list[tuple[Source, str]], list[str]]:
    """Fetch the catalogs, discover sources, and fetch the selected ones. Returns (source, head) pairs and skips."""
    skipped: list[str] = []

    async def index(source: Source) -> None:
        try:
            await fetch_reported(source, raw)
        except GitError as error:
            skipped.append(f"{source.name}: {error}")

    await asyncio.gather(*(index(source) for source in seed_sources() if source.name in INDEX_REPOS))
    selected = select(discover(raw), mode, repos)
    limit = asyncio.Semaphore(jobs * 2)

    async def one(source: Source) -> tuple[Source, str] | None:
        async with limit:
            if await asyncio.to_thread(is_archived, source):
                skipped.append(f"{source.name}: archived")
                return None
            try:
                return source, await fetch_reported(source, raw)
            except GitError as error:
                skipped.append(f"{source.name}: unreachable ({error})")
                return None

    fetched = [item for item in await asyncio.gather(*(one(source) for source in selected)) if item is not None]
    return fetched, skipped
