"""Caller tools: `Tool` declarations with JSON Schema inputs and async handlers that receive the model's arguments.

A handler that raises `ToolFailed` sends its message back to the model, which can correct the call and try again. The
same happens when the arguments do not match `input_schema`. Map sessions report through `write_repo_page`,
`propose_update`, and `flag_contradiction`; query sessions search the wiki with `search_wiki`.
"""

from pathlib import Path
import re

from amplifier_agent import Tool, ToolContext, ToolFailed

from llm_wiki.wiki.pages import SlugRegistry, load_pages
from llm_wiki.wiki.proposals import MapOutput, Proposal
from llm_wiki.wiki.search import Bm25

# Built-in tools, named by string in AgentOptions.tools.
READ_TOOLS = ("read_file", "glob", "grep")
WRITE_TOOLS = ("write_file",)

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
_CITATION = re.compile(r"^`?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{7,40}):([^\s`]+)`?$")


def object_schema(properties: dict[str, dict], required: list[str]) -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def check_citation(citation: str, repo: str, sha: str, checkout: Path) -> str:
    match = _CITATION.match(citation.strip())
    if match is None:
        raise ToolFailed(f"Cite as {repo}@{sha}:<path>, for example {repo}@{sha}:README.md. Got: {citation}")
    cited_repo, cited_sha, path = match.groups()
    if cited_repo.lower() != repo.lower() or not sha.startswith(cited_sha):
        raise ToolFailed(f"This session reads {repo}@{sha}; cite that repository and SHA. Got: {citation}")
    if not (checkout / path.split("#", 1)[0]).exists():
        raise ToolFailed(f"{path} does not exist in {repo}. Cite a file you read.")
    return f"{repo}@{sha}:{path}"


def map_tools(output: MapOutput, repo: str, sha: str, checkout: Path, existing_pages: list[str]) -> list[Tool]:
    """write_repo_page, propose_update, and flag_contradiction for one repository at one SHA."""
    slugs = SlugRegistry(existing_pages)

    def target(page: str) -> str:
        resolved = slugs.target(page)
        if resolved is None:
            raise ToolFailed(f"page must be concepts/<kebab-slug> or entities/<kebab-slug>. Got: {page}")
        return resolved.removesuffix(".md")

    async def write_repo_page(arguments: dict, context: ToolContext) -> str:
        markdown = str(arguments["markdown"])
        if f"{repo}@{sha}".lower() not in markdown.lower():
            raise ToolFailed(f"Cite claims as {repo}@{sha}:<path>. The page has no citation of this repository.")
        output.repo_page = markdown
        return "Repository page saved. It is written to the wiki when this session ends successfully."

    async def propose_update(arguments: dict, context: ToolContext) -> str:
        proposal = Proposal(
            target(str(arguments["page"])),
            str(arguments["kind"]),
            " ".join(str(arguments["claim"]).split()),
            check_citation(str(arguments["citation"]), repo, sha, checkout),
        )
        output.proposals.append(proposal)
        return f"Proposed {proposal.kind} for {proposal.page}."

    async def flag_contradiction(arguments: dict, context: ToolContext) -> str:
        proposal = Proposal(
            target(str(arguments["page"])),
            "contradiction",
            " ".join(str(arguments["claim"]).split()),
            check_citation(str(arguments["citation"]), repo, sha, checkout),
        )
        output.proposals.append(proposal)
        return f"Flagged a contradiction on {proposal.page}."

    page = {"type": "string", "description": "concepts/<slug> or entities/<slug>, kebab-case, no .md"}
    claim = {
        "type": "string",
        "description": "One self-contained sentence. Links use paths relative to the page. The citation goes in its "
        "own field, not here.",
    }
    citation = {"type": "string", "description": f"{repo}@{sha}:<path to the file that supports the claim>"}
    return [
        Tool(
            name="write_repo_page",
            description="Save the summary page for this repository. Call once, with the complete page.",
            input_schema=object_schema({"markdown": {"type": "string"}}, ["markdown"]),
            handler=write_repo_page,
        ),
        Tool(
            name="propose_update",
            description="Propose a fact or a cross-link for a concept or entity page. A later step merges proposals.",
            input_schema=object_schema(
                {
                    "page": page,
                    "kind": {"type": "string", "enum": ["fact", "link"]},
                    "claim": claim,
                    "citation": citation,
                },
                ["page", "kind", "claim", "citation"],
            ),
            handler=propose_update,
        ),
        Tool(
            name="flag_contradiction",
            description="Report a claim from this repository that disagrees with what a wiki page says now.",
            input_schema=object_schema(
                {"page": page, "claim": claim, "citation": citation}, ["page", "claim", "citation"]
            ),
            handler=flag_contradiction,
        ),
    ]


def search_tool(wiki: Path) -> Tool:
    async def search_wiki(arguments: dict, context: ToolContext) -> str:
        hits = Bm25(load_pages(wiki)).search(str(arguments["query"]), int(arguments.get("limit", 8)))
        if not hits:
            return "No matching pages. Read index.md for the full catalog."
        return "\n".join(f"{hit.path} ({hit.score}) {hit.title}\n  {hit.snippet}" for hit in hits)

    return Tool(
        name="search_wiki",
        description="Rank wiki pages by BM25 relevance to a query. Returns paths relative to the wiki root.",
        input_schema=object_schema(
            {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            ["query"],
        ),
        handler=search_wiki,
    )
