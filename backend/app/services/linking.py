"""Link a transcript row to a curriculum subject. Never guesses: an ambiguous or weak match stays unlinked and the row is
marked NEEDS_SYLLABUS so the officer decides."""
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
        if top["score"] >= AUTO_LINK_MIN and unambiguous:
            row["subject"] = subject_ref(top)
            row["auto_linked"] = True
    row["curriculum_status"] = "LINKED" if row.get("subject") else "NEEDS_SYLLABUS"  # unrecognized: never guessed
