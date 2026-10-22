"""TF-IDF candidate retrieval. Scores are retrieval relevance only, NOT equivalence probability."""
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

MAX_TOP_K = 3
# School subjects are introductory by nature: when ranking candidates for a SCHOOL subject, prefer courses whose title shares the
# subject's words, whose title says general/introductory, and that sit at 100-200 level. Retrieval relevance only, not equivalence.
INTRO_WORDS = {"general", "introduction", "introductory", "principles", "fundamentals", "foundations", "basic", "i"}
SCHOOL_WORDS = {"ap", "a", "as", "level", "igcse", "gcse", "and", "of", "the", "in", "for"}


def _tok(s: str) -> set[str]:
    import re
    return set(re.findall(r"[a-z0-9]+", s.lower()))


@dataclass
class Candidate:
    course_id: str
    score: float


class CatalogRetriever:
    mode = "lexical"

    def __init__(self, catalog: list[dict], exclude_departments: list[str] | None = None):
        self.excluded = {d.upper() for d in exclude_departments or []}  # subject areas that never take transfer credit
        self.ids = [c["code"] for c in catalog]
        self.titles = {c["code"]: _tok(c["title"]) for c in catalog}
        self.levels = {c["code"]: c.get("level") or 300 for c in catalog}
        docs = [" ".join([c["title"], c["description"], *[o["text"] for o in c["outcomes"]]]) for c in catalog]
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vec.fit_transform(docs)

    def search(self, query: str, top_k: int = MAX_TOP_K, subject_title: str | None = None) -> list[Candidate]:
        top_k = max(1, min(top_k, MAX_TOP_K))  # enforced server-side
        if not query.strip():
            return []
        sims = [float(x) for x in cosine_similarity(self.vec.transform([query]), self.matrix)[0]]
        if subject_title:  # ranking boost for school subjects (see INTRO_WORDS); the reported score is still relevance only
            words = _tok(subject_title) - SCHOOL_WORDS
            sims = [sv + (0.15 * len(words & self.titles[i]) / len(words) if words else 0)
                    + (0.10 if self.titles[i] & INTRO_WORDS else 0) + (0.05 if self.levels[i] <= 200 else 0)
                    for i, sv in zip(self.ids, sims, strict=True)]
        ranked = sorted(((i, sv) for i, sv in zip(self.ids, sims, strict=True) if i.split()[0].upper() not in self.excluded), key=lambda t: -t[1])
        return [Candidate(i, round(sv, 4)) for i, sv in ranked[:top_k] if sv > 0]
