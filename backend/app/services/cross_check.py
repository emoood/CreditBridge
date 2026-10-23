"""The text layer of a digital PDF as a CROSS-CHECK on what vision read. It never overrides vision and never fills a value: a disagreement is a
finding for the Intake agent (re-read that block) and, if it stays, a question for the officer. Image-only files have no text layer, so no cross-check."""
import difflib
import re
from typing import Any

import pymupdf

from .extraction import extract_transcript


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def has_text_layer(path: str) -> bool:
    return path.lower().endswith(".pdf") and sum(len(p.get_text()) for p in pymupdf.open(path)) >= 80


def text_rows(path: str, doc_id: str) -> dict[int, list[dict[str, Any]]] | None:
    """Course rows the text layer shows, per page (title, grade, credits, term). None when the file has no text layer."""
    if not has_text_layer(path):
        return None
    out: dict[int, list[dict[str, Any]]] = {}
    for r in extract_transcript(path, doc_id).rows:
        out.setdefault(r.page, []).append({"title": r.title, "grade": r.grade, "credits": float(r.credits) if r.credits is not None else None, "term": r.term})
    return out


def block_of(path: str, page_no: int, title: str, spans: dict[str, tuple[int, tuple[int, int]]], dpi: int) -> str | None:
    """The block (at the reading dpi) whose vertical span contains this title in the text layer."""
    hits = pymupdf.open(path)[page_no - 1].search_for(title[:40])
    if not hits:
        return None
    y = hits[0].y0 * dpi / 72
    return next((k for k, (pg, (a, b)) in spans.items() if pg == page_no and a - 6 <= y <= b + 6), None)


def _same_title(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


def _same_grade(vision: dict[str, Any], text_grade: str) -> bool:
    g = (text_grade or "").strip().upper()
    if vision.get("letter"):
        return vision["letter"].strip().upper() == g
    if vision.get("mark") is not None:
        try:
            return float(g) == float(vision["mark"])
        except ValueError:
            return False
    return False


def compare(blocks: dict[str, dict[str, Any]], spans: dict[str, tuple[int, tuple[int, int]]], text: dict[int, list[dict[str, Any]]], path: str, dpi: int) -> list[dict[str, Any]]:
    """Findings where the text layer and vision disagree, on pages where vision has read at least one block. Each names a block (and a row when it can)."""
    out: list[dict[str, Any]] = []
    for pg, trows in text.items():
        keys = [k for k, (p, _) in spans.items() if p == pg and k in blocks and not blocks[k].get("failed")]
        if not keys:
            continue  # nothing read on this page yet, or only failed blocks (those are already failures of their own)
        vrows = [(k, i, r) for k in keys for i, r in enumerate(blocks[k].get("rows", []))]
        read_all = all(k in blocks for k, (p, _) in spans.items() if p == pg)
        for t in trows:
            m = next(((k, i, r) for k, i, r in vrows if _same_title(r.get("subject") or "", t["title"])), None)
            if m is None:
                if read_all:  # only meaningful once every block of the page was read
                    out.append({"check": "text_layer_crosscheck", "kind": "missing_in_vision", "block": block_of(path, pg, t["title"], spans, dpi) or keys[0], "row": None,
                                "subject": t["title"], "field": "block", "detail": f"the text layer shows '{t['title']}' on page {pg} but vision did not read that row"})
                continue
            k, i, r = m
            if not _same_grade(r, t["grade"]):
                out.append({"check": "text_layer_crosscheck", "kind": "grade_differs", "block": k, "row": i, "subject": r.get("subject"), "field": "grade",
                            "detail": f"vision read grade '{r.get('letter') or r.get('mark')}' but the text layer shows '{t['grade']}'"})
    return out


def name_in_text(path: str, name: str) -> bool | None:
    """Whether the student's name from the record appears in the text layer of page 1 (None when there is no text layer)."""
    if not has_text_layer(path):
        return None
    page = _norm(pymupdf.open(path)[0].get_text())
    return all(tok in page for tok in _norm(name).split())


def prints_totals(path: str) -> bool:
    """Whether the text layer prints any total, GPA or quality-points line. A transcript that prints none cannot have a total to check."""
    return bool(re.search(r"\b(total|gpa|quality points)\b", " ".join(p.get_text() for p in pymupdf.open(path)), re.IGNORECASE))
