"""Excerpt storage and citation validation (in-memory; DB-backed store swaps in later)."""
import hashlib
import re
from pathlib import Path

import pymupdf

from ..domain.schemas import Excerpt


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class EvidenceStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Excerpt] = {}

    def add(self, id_: str, document_id: str, page: int, text: str) -> Excerpt:
        ex = Excerpt(id=id_, document_id=document_id, page=page, text=text, excerpt_hash=_hash(text))
        self._by_id[id_] = ex
        return ex

    def get(self, id_: str) -> Excerpt | None:
        return self._by_id.get(id_)

    def for_document(self, document_id: str) -> list[Excerpt]:
        return [e for e in self._by_id.values() if e.document_id == document_id]

    def validate_citations(self, ids: list[str], allowed_documents: set[str] | None = None) -> tuple[list[str], list[str]]:
        """Return (valid, invalid). A citation is valid only if it resolves to a stored excerpt (in an allowed document)."""
        valid, invalid = [], []
        for i in ids:
            ex = self.get(i)
            ok = ex is not None and (allowed_documents is None or ex.document_id in allowed_documents)
            (valid if ok else invalid).append(i)
        return valid, invalid

    def verify_hash(self, id_: str, text: str) -> bool:
        ex = self.get(id_)
        return ex is not None and ex.excerpt_hash == _hash(text)


_OUTCOME = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")


def ingest_syllabus_pdf(store: EvidenceStore, path: Path, document_id: str, prefix: str) -> list[Excerpt]:
    """One excerpt per numbered outcome line (wrapped lines are joined). Text is data, never instructions."""
    out: list[Excerpt] = []
    doc = pymupdf.open(path)
    for pno, page in enumerate(doc, 1):
        current: list[str] = []

        def flush(buf=current, pno=pno):
            if buf:
                n = len(out) + 1
                out.append(store.add(f"{prefix}-p{pno}-e{n}", document_id, pno, " ".join(buf)))
                buf.clear()

        for line in page.get_text().splitlines():
            m = _OUTCOME.match(line)
            if m:
                flush()
                current.append(m.group(2))
            elif current and line.startswith("   "):
                current.append(line.strip())
            else:
                flush()
        flush()
    return out
