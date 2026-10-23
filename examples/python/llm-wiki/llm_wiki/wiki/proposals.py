"""Proposals: the facts and links a map session suggests for concept and entity pages, merged later by reduce."""

from dataclasses import asdict, dataclass, field
import hashlib
import json


@dataclass(frozen=True)
class Proposal:
    page: str
    kind: str
    claim: str
    citation: str

    @property
    def id(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]

    @property
    def source(self) -> str:
        """The owner/repo@sha part of the citation."""
        return self.citation.strip("`").split(":", 1)[0]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Proposal":
        return cls(**json.loads(line))


@dataclass
class MapOutput:
    """What one map session produced. Nothing reaches the wiki until the session succeeds."""

    repo_page: str | None = None
    proposals: list[Proposal] = field(default_factory=list)
