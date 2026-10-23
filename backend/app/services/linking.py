"""Link a transcript row to a curriculum subject. Never guesses: an ambiguous or weak match stays unlinked and the row is
marked NEEDS_SYLLABUS so the officer decides."""
import re
from typing import Any

from .curriculum import curriculum

AUTO_LINK_MIN = 0.8


def subject_ref(s: dict[str, Any]) -> dict[str, Any]:
    return {"id": s["id"], "name": s["name"], "code": s["code"], "qualification": s["qualification"]}


def candidates(row: dict[str, Any], qualification: str | None) -> list[dict[str, Any]]:
    return curriculum.candidates(row.get("title") or "", row.get("code") or "", qualification)


def autolink(row: dict[str, Any], qualification: str | None) -> None:
    cands = candidates(row, qualification)
    row["candidates"] = cands
    if not row.get("subject") and cands:
        top = cands[0]
        unambiguous = len(cands) == 1 or top["score"] - cands[1]["score"] >= 0.05 or (top["score"] >= 0.99 > cands[1]["score"])
        # An AP course is always labelled AP on the transcript: a plain "Chemistry" or "Biology" must not link to its AP namesake (it is a regular school course).
        ap_unlabelled = top["qualification"] == "Advanced Placement" and not re.search(r"\bAP\b", row.get("title") or "", re.IGNORECASE)
        if top["score"] >= AUTO_LINK_MIN and unambiguous and not ap_unlabelled:
            row["subject"] = subject_ref(top)
            row["auto_linked"] = True
    row["curriculum_status"] = "LINKED" if row.get("subject") else "NEEDS_SYLLABUS"  # unrecognized: never guessed
