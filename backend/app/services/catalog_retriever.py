"""TF-IDF candidate retrieval. Scores are retrieval relevance only, NOT equivalence probability."""
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

MAX_TOP_K = 3


@dataclass
class Candidate:
    course_id: str
    score: float


class CatalogRetriever:
    mode = "lexical"

    def __init__(self, catalog: list[dict]):
        self.ids = [c["code"] for c in catalog]
        docs = [" ".join([c["title"], c["description"], *[o["text"] for o in c["outcomes"]]]) for c in catalog]
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vec.fit_transform(docs)

    def search(self, query: str, top_k: int = MAX_TOP_K) -> list[Candidate]:
        top_k = max(1, min(top_k, MAX_TOP_K))  # enforced server-side
        if not query.strip():
            return []
        sims = cosine_similarity(self.vec.transform([query]), self.matrix)[0]
        ranked = sorted(zip(self.ids, sims), key=lambda t: -t[1])
        return [Candidate(i, round(float(s), 4)) for i, s in ranked[:top_k] if s > 0]
