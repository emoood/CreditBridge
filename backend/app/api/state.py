"""In-memory application state (prototype). Uploaded files live under DOCUMENT_ROOT; everything else is lost on restart."""
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..agents.precedent import PrecedentStore
from ..services.catalog_retriever import CatalogRetriever
from ..services.demo_repo import DemoRepo

DEMO_ID = "DEMO-001"


@dataclass
class AppState:
    id: str
    kind: str  # demo | upload
    student: str
    program: str
    institution: str
    document: dict[str, Any] | None = None
    extraction: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    latest_job: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def short(self) -> str:
        return self.id.replace("-", "")[-6:].upper()


class State:
    def __init__(self) -> None:
        self.repo = DemoRepo()
        self.retriever = CatalogRetriever(list(self.repo.targets.values()))
        self.precedent = PrecedentStore()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.active_job: str | None = None
        self.apps: dict[str, AppState] = {}
        demo = AppState(DEMO_ID, "demo", "Sample Student (synthetic)", "BSc Computer Science (demo)",
                        "Gulf Example University (synthetic)")
        for c in self.repo.sources.values():
            demo.rows.append({"id": c.code, "code": c.code, "title": c.title, "title_ar": c.title_ar, "credits": c.credits,
                              "grade": c.grade, "term": c.term, "page": 1, "evidence_ref": "", "uncertain_fields": [],
                              "subject": None, "candidates": [], "included": True, "corrected": False})
        self.apps[DEMO_ID] = demo

    def new_upload(self, student: str, program: str) -> AppState:
        app = AppState(f"APP-{uuid.uuid4().hex[:8].upper()}", "upload", student or "Uploaded transcript", program, "")
        self.apps[app.id] = app
        return app

    def log(self, actor: str, event: str, obj: str = "", **details: Any) -> None:
        self.audit.append({"at": datetime.now(UTC).isoformat(), "actor": actor, "event": event, "object": obj, "details": details})


S = State()


def reset_state() -> None:
    global S
    S = State()
