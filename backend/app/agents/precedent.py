"""Institutional memory: past admissions-officer decisions, searchable by similarity.
In-memory for now; the decision service appends here and the DB-backed version replaces it later."""
from dataclasses import asdict, dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class Precedent:
    subject_title: str
    qualification: str
    target_course_id: str | None
    decision: str  # ACCEPTED | DECLINED
    reason: str = ""
    actor: str = ""
    decided_at: str = ""
    source: str = "officer_decision"  # officer_decision | imported_table


class PrecedentStore:
    def __init__(self, items: list[Precedent] | None = None):
        self.items: list[Precedent] = list(items or [])

    def add(self, p: Precedent) -> None:
        self.items.append(p)

    def search(self, query: str, k: int = 3) -> list[dict]:
        if not self.items or not query.strip():
            return []
        docs = [f"{p.subject_title} {p.qualification} {p.target_course_id or ''} {p.reason}" for p in self.items]
        vec = TfidfVectorizer(stop_words="english").fit(docs + [query])
        sims = cosine_similarity(vec.transform([query]), vec.transform(docs))[0]
        ranked = sorted(zip(self.items, sims), key=lambda t: -t[1])
        return [{**asdict(p), "similarity": round(float(s), 3)} for p, s in ranked[:k] if s > 0.05]
