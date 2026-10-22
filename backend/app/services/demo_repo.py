"""In-memory reference data loaded from fixtures/demo_v1. Stands in for the DB (same read interface)."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.schemas import Excerpt
from .evidence import EvidenceStore, ingest_syllabus_pdf

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "demo_v1"


@dataclass
class SourceCourse:
    id: str
    code: str
    title: str
    credits: str
    grade: str
    syllabus_doc_id: str | None
    excerpt_ids: list[str] = field(default_factory=list)
    title_ar: str = ""
    term: str = ""


class DemoRepo:
    def __init__(self, fixtures: Path = FIXTURES):
        self.store = EvidenceStore()
        cat = json.loads((fixtures / "catalog.json").read_text())
        self.catalog_version, self.catalog_label = cat["catalog_version"], cat["label"]
        self.targets = {c["code"]: c for c in cat["courses"]}
        for c in cat["courses"]:
            self.store.add(f"tgt-{c['code']}-desc", c["document_id"], 1, c["description"])
            for o in c["outcomes"]:
                self.store.add(o["id"], c["document_id"], 1, o["text"])
        truth = json.loads((fixtures / "expected_results.json").read_text())["extraction_truth"]
        self.sources: dict[str, SourceCourse] = {}
        for t in truth:
            code = t["source_code"]
            sc = SourceCourse(code, code, t["title"], t["credits"], t["grade"], None, title_ar=t.get("title_ar", ""), term=t.get("term", ""))
            pdf = fixtures / "syllabi" / f"{code}.pdf"
            if pdf.exists():
                sc.syllabus_doc_id = f"src-doc-{code}"
                sc.excerpt_ids = [e.id for e in ingest_syllabus_pdf(self.store, pdf, sc.syllabus_doc_id, f"src-{code}")]
            self.sources[code] = sc
        self.syllabus_requests: list[dict] = []

    def target_excerpts(self, code: str) -> list[Excerpt]:
        return self.store.for_document(self.targets[code]["document_id"])

    def add_source(self, source_id: str, title: str, credits: str, grade: str, term: str, title_ar: str,
                   outcomes: list[dict], document_id: str) -> SourceCourse:
        """Register an uploaded-transcript subject whose evidence is the curriculum database (read-only)."""
        sc = SourceCourse(source_id, source_id, title, credits, grade, None, title_ar=title_ar, term=term)
        if outcomes:
            sc.syllabus_doc_id = f"src-doc-{source_id}"
            for n, o in enumerate(outcomes, 1):
                ex = self.store.add(f"src-{source_id}-p{o['page']}-e{n}", sc.syllabus_doc_id, o["page"], o["text"])
                sc.excerpt_ids.append(ex.id)
        self.sources[source_id] = sc
        return sc
