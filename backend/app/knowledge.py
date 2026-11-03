"""Policy Knowledge Base: markdown passages + a small BM25 retriever (no external deps).
Swap for a vector store later without touching callers: only `search()` is public."""
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"
_STOP = set("a an the and or of to in on for is are be with your you we our it this that at by from as can if".split())


@dataclass
class Passage:
    id: str
    doc: str
    title: str
    text: str

    def as_source(self) -> dict:
        return {"id": self.id, "doc": self.doc, "title": self.title}


def _tokens(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]
    # light stemming so "returns"/"returned"/"returning" match
    return [re.sub(r"(ing|ed|es|s)$", "", t) if len(t) > 4 else t for t in toks]


class KnowledgeBase:
    def __init__(self, directory: Path = KB_DIR):
        self.directory = directory
        self.reload()

    def reload(self) -> int:
        self.passages: list[Passage] = []
        for f in sorted(self.directory.glob("*.md")):
            if f.name.lower() == "readme.md":
                continue
            raw = f.read_text(encoding="utf-8")
            doc_title = (re.search(r"^# (.+)$", raw, re.M) or [None, f.stem])[1]
            for i, sec in enumerate(re.split(r"^## ", raw, flags=re.M)[1:]):
                title, _, body = sec.partition("\n")
                self.passages.append(Passage(f"{f.stem}#{i}", doc_title.strip(), title.strip(), body.strip()))
        self._docs = [_tokens(p.title + " " + p.title + " " + p.text) for p in self.passages]
        self._avg = sum(map(len, self._docs)) / max(len(self._docs), 1)
        df = Counter(t for d in self._docs for t in set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        return len(self.passages)

    def search(self, query: str, k: int = 3, min_score: float = 0.5) -> list[tuple[Passage, float]]:
        q = _tokens(query)
        k1, b = 1.5, 0.75
        scores = []
        for p, d in zip(self.passages, self._docs):
            tf = Counter(d)
            s = 0.0
            for t in set(q):
                if t in tf:
                    f = tf[t]
                    s += self._idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / self._avg))
            if s >= min_score:
                scores.append((p, round(s, 3)))
        scores.sort(key=lambda x: -x[1])
        return scores[:k]


_kb: KnowledgeBase | None = None


def kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
