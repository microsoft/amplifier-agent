"""BM25 search over wiki pages, behind the search_wiki caller tool."""

from collections import Counter
from dataclasses import dataclass
import math

from llm_wiki.wiki.pages import TOKEN, Page


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


@dataclass
class SearchHit:
    path: str
    title: str
    score: float
    snippet: str


class Bm25:
    """Okapi BM25 over page titles, tags, and bodies. Titles and tags count twice."""

    def __init__(self, pages: list[Page], k1: float = 1.5, b: float = 0.75) -> None:
        self.pages = pages
        self.k1 = k1
        self.b = b
        self.docs: list[Counter[str]] = []
        self.lengths: list[int] = []
        for page in pages:
            tags = " ".join(str(tag) for tag in page.meta.get("tags") or [])
            tokens = tokenize(f"{page.title} {tags}") * 2 + tokenize(page.body)
            self.docs.append(Counter(tokens))
            self.lengths.append(len(tokens))
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        frequency: Counter[str] = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in frequency.items()}

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        terms = tokenize(query)
        scored = []
        for index, doc in enumerate(self.docs):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / (self.average or 1))
            for term in terms:
                count = doc.get(term, 0)
                if count:
                    score += self.idf[term] * count * (self.k1 + 1) / (count + norm)
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], self.pages[item[1]].path))
        return [
            SearchHit(self.pages[i].path, self.pages[i].title, round(s, 3), snippet(self.pages[i].body, terms))
            for s, i in scored[:limit]
        ]


def snippet(body: str, terms: list[str], width: int = 200) -> str:
    flat = " ".join(body.split())
    lower = flat.lower()
    positions = [lower.find(term) for term in terms if term in lower]
    start = max(0, min(positions) - 40) if positions else 0
    return flat[start : start + width]
