"""Read-only access to the teammate's `curriculum` schema (Supabase). Never writes; never creates tables.
Degrades to 'unavailable' if DATABASE_URL is missing or unreachable."""
import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from sqlalchemy import text

from ..db.session import get_engine

_STOP = {"ap", "a", "level", "alevel", "as", "igcse", "gcse", "and", "of", "the", "in", "for", "international", "advanced"}


@dataclass
class Subject:
    id: str
    name: str
    code: str
    qualification: str
    qualification_type: str
    provider: str


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower().replace("&", " and ")) if t not in _STOP}


def _norm_code(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def score(title: str, code: str, s: Subject) -> float:
    if code and _norm_code(code) and (_norm_code(code) == _norm_code(s.code) or _norm_code(code) == _norm_code(s.code).replace("ap", "")):
        return 1.0
    a, b = _tokens(title), _tokens(s.name)
    if not a or not b:
        return 0.0
    if a == b:
        return 0.99  # identical words beat close ones; only an exact code scores higher
    contain = len(a & b) / min(len(a), len(b))
    ratio = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return round(min(0.97, max(ratio, 0.95 * contain if len(a & b) >= 1 else 0)), 3)  # a code match (1.0) always wins


def qualification_hint(text: str) -> set[str] | None:
    """Words in the transcript row that reveal the qualification (IGCSE, AP, A Level...). None if no hint."""
    t = f" {text.lower()} "
    if re.search(r"\bigcse\b", t):
        return {"Cambridge IGCSE"}
    if re.search(r"\bap\b", t):
        return {"Advanced Placement"}
    if re.search(r"\b(a|as)[ -]?level\b|\bgce\b|\bedexcel\b", t):
        return {"Cambridge International AS & A Level", "Pearson Edexcel Level 3 Advanced GCE"}
    return None


class SqlCurriculum:
    def __init__(self) -> None:
        self._subjects: list[Subject] | None = None
        self._at = 0.0
        self.error: str | None = None

    def subjects(self) -> list[Subject]:
        if self._subjects is not None and time.time() - self._at < 300:
            return self._subjects
        try:
            with get_engine().connect() as c:
                rows = c.execute(text(
                    "select s.id::text, s.name, s.subject_code, q.name, q.qualification_type, p.name "
                    "from curriculum.subjects s join curriculum.qualifications q on q.id=s.qualification_id "
                    "join curriculum.curriculum_providers p on p.id=q.provider_id order by q.name, s.name")).all()
            self._subjects = [Subject(*r) for r in rows]
            self._at, self.error = time.time(), None
        except Exception as e:  # noqa: BLE001
            self._subjects, self._at = [], time.time()
            self.error = f"{type(e).__name__}"
        return self._subjects

    def qualifications(self) -> list[str]:
        return sorted({s.qualification for s in self.subjects()})

    def candidates(self, title: str, code: str = "", qualification: str | None = None, k: int = 5) -> list[dict]:
        hint = qualification_hint(title) if not qualification else None
        pool = [s for s in self.subjects() if (not qualification or s.qualification == qualification)
                and (not hint or s.qualification in hint)]
        if hint and not pool:
            pool = self.subjects()
        ranked = sorted(((score(title, code, s), s) for s in pool), key=lambda t: -t[0])
        return [{**asdict(s), "score": sc} for sc, s in ranked[:k] if sc > 0.3]

    def search(self, q: str, qualification: str | None = None, limit: int = 20) -> list[dict]:
        ql = q.lower().strip()
        pool = [s for s in self.subjects() if not qualification or s.qualification == qualification]
        hits = [s for s in pool if not ql or ql in s.name.lower() or ql in (s.code or "").lower()]
        return [asdict(s) for s in hits[:limit]]

    def outcomes(self, subject_id: str) -> list[dict]:
        """Objectives then topic text of the subject's most recent version, with source pages. Original wording."""
        with get_engine().connect() as c:
            v = c.execute(text("select id from curriculum.curriculum_versions where subject_id=cast(:s as uuid) "
                               "order by valid_from desc nulls last limit 1"), {"s": subject_id}).scalar()
            if v is None:
                return []
            objs = c.execute(text("select objective_text, source_page from curriculum.learning_objectives "
                                  "where version_id=:v order by objective_order nulls last"), {"v": v}).all()
            tops = c.execute(text("select title, description, source_page from curriculum.curriculum_topics "
                                  "where version_id=:v order by topic_order nulls last"), {"v": v}).all()
        out = [{"text": o[0], "page": o[1] or 1, "kind": "objective"} for o in objs if o[0]]
        seen = {o["text"].lower() for o in out}
        for t, d, pg in tops:
            line = f"{t}: {d}" if d and d.strip().lower() != (t or "").strip().lower() else (t or "")
            if line and line.lower() not in seen:
                out.append({"text": line[:400], "page": pg or 1, "kind": "topic"})
        return out


curriculum = SqlCurriculum()
