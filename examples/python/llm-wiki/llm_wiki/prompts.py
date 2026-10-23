"""Every prompt the pipeline sends. The schema in WIKI.md and the wiki-maintainer skill carry the page conventions."""

from llm_wiki.sources import BLOB_LIMIT, Source
from llm_wiki.wiki.lint import LintReport
from llm_wiki.wiki.pages import Page
from llm_wiki.wiki.proposals import Proposal

MAP_READ_BUDGET = 12

IN_WIKI = (
    "Your working directory is the wiki folder. Use paths relative to it, such as index.md and concepts/bundle.md, "
    "never wiki/index.md."
)


def map_prompt(source: Source, sha: str, index: str, old_page: str) -> str:
    lines = [
        f"Ingest the repository {source.name} at {sha}. Your working directory is its checkout.",
        f"Catalog kind: {source.kind}.",
    ]
    if source.description:
        lines.append(f"Catalog description: {source.description}")
    lines += [
        "",
        (
            f"Read at most about {MAP_READ_BUDGET} files, several in each step. Start with README.md, AGENTS.md, and "
            "the manifest that exists (pyproject.toml, package.json, Cargo.toml, bundle.md, SMART_TOOL.md). Then read "
            "only the docs/ pages and entry-point source files they point to. Skip tests, examples, lock files, "
            f"generated files, and assets. Files over {BLOB_LIMIT} bytes are not checked out. Stop reading as soon as "
            "you can state what the repository is, what it provides, and how it connects to the rest of the ecosystem."
        ),
        "",
        f"1. Call write_repo_page once with the page for repos/{source.slug}.md.",
        (
            "2. Call propose_update for each fact or cross-link that belongs on a concept or entity page. Use the "
            "seed concept slugs. Propose for the few pages this repository is most about, not one proposal per page. "
            "Aim for 5 to 8 proposals."
        ),
        "3. Call flag_contradiction when this repository disagrees with a page that exists now.",
        f"Every citation is {source.name}@{sha}:<path>.",
        "",
        "When you are done, reply with one line naming the pages you proposed updates for.",
        "",
        "## Current index.md",
        "",
        index.strip() or "(the wiki is empty)",
    ]
    if old_page:
        lines += ["", f"## Current repos/{source.slug}.md", "", old_page.strip()]
    return "\n".join(lines) + "\n"


RELATED = (
    "Fill ## Related with links to the concept and entity pages this page relates to: the link proposals, related "
    "pages in the index or written in this run, and the concepts the pages that link here also cover. Leave "
    '## Related out when nothing is related; never write a placeholder such as "None yet".'
)


def reduce_prompt(
    target: str,
    current: str,
    proposals: list[Proposal],
    issues: list[str],
    index: str,
    linked_from: list[str],
    written: list[str],
) -> str:
    checkouts = sorted({proposal.source.split("@", 1)[0].replace("/", "__") for proposal in proposals})
    lines = [
        f"Update the wiki page {target}. Load the wiki-maintainer skill first.",
        IN_WIKI,
        f"Write the complete page to {target} with write_file. Do not write any other file.",
        RELATED,
    ]
    if checkouts:
        lines.append(f"The cited repositories are checked out at: {', '.join(f'../raw/{name}/' for name in checkouts)}")
    lines.append("")
    if proposals:
        lines += ["## Proposals", ""]
        lines += [f"- [{proposal.kind}] {proposal.claim} (`{proposal.citation}`)" for proposal in proposals]
        lines.append("")
    if issues:
        lines += ["## Lint findings to fix", ""]
        lines += [f"- {issue}" for issue in issues]
        lines.append("")
    if linked_from:
        lines += ["## Pages that link here", "", *(f"- {path}" for path in linked_from), ""]
    others = [path for path in written if path != target]
    if others:
        lines += ["## Other pages written in this run", "", *(f"- {path}" for path in others), ""]
    lines += [f"## Current {target}", "", current.strip() or "(the page does not exist yet)", ""]
    lines += ["## Current index.md", "", index.strip() or "(the wiki is empty)"]
    return "\n".join(lines) + "\n"


OVERVIEW_PROMPT = f"""\
Refresh overview.md, the synthesis of the whole ecosystem. {IN_WIKI} Read index.md, then the repository and concept
pages you need. Explain the layers, how the main pieces fit together, and where to start reading. Link to pages; keep
citations from the pages you draw on. Write the complete page to overview.md with write_file. Do not write any other
file.
"""


def query_prompt(question: str) -> str:
    return (
        f"Answer from the wiki. {IN_WIKI} Read index.md first, use search_wiki to find more pages, then read the pages "
        "you need. Link every page you rely on with a relative link from the wiki root, such as concepts/bundle.md. "
        "End each claim with the source citation the page gives for it, copied in backticks, such as "
        "`microsoft/amplifier-core@1a2b3c4:README.md`. Say what the wiki does not cover.\n\n"
        f"Question: {question}\n"
    )


def lint_prompt(report: LintReport, sample: list[Page], target: str) -> str:
    paths = "\n".join(f"- {page.path}" for page in sample) or "- (no pages)"
    return (
        f"Review the wiki. Load the wiki-maintainer skill first. {IN_WIKI}\n\n"
        "Code checks found the findings below. Read the sample pages, look for contradictions between pages, claims "
        "without citations, and concepts that are mentioned often but have no page. Then write a lint report to "
        f"{target} with write_file: one section per finding type, each finding with the page, the problem, and a "
        "suggested fix. Do not write any other file.\n\n"
        f"## Code checks\n\n{report.render()}\n## Sample pages\n\n{paths}\n"
    )
