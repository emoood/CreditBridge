"""Officer actions on a row. Every action stores who, when and the ORIGINAL suggestion. Code validates; no model is involved.

CONFIRM  accept the suggested mapping (the row must be Mapped)
CHANGE   pick another AUD course and/or edit credits (reason required)
REJECT   refuse the suggestion; no credit (reason required)
NO_CREDIT  no credit for this course (reason required)
SEND_FOR_REVIEW  ask for a review package; not a resolution
CORRECT  an officer-applied auditor correction (shows before and after)
APPROVE  the final approval of the whole sheet (all relevant rows resolved, checks passing)"""
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Application, RowAction
from ..runtime import rt
from . import mapping as mp
from .mapping import ROOT

RESOLVING = ("CONFIRM", "CHANGE", "REJECT", "NO_CREDIT", "CORRECT")
NEEDS_REASON = ("CHANGE", "REJECT", "NO_CREDIT")


class ActionError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def transfer_credits() -> int:
    return int(yaml.safe_load((ROOT / "policies" / "institution.yaml").read_text()).get("transfer_credits", 3))


def latest(db: Session, app_id: str) -> dict[str, RowAction]:
    out: dict[str, RowAction] = {}
    for a in db.scalars(select(RowAction).where(RowAction.app_id == app_id, RowAction.row_id != "").order_by(RowAction.id)):
        if a.action != "SEND_FOR_REVIEW" or a.row_id not in out:  # a review request never hides a resolution
            out[a.row_id] = a
    return out


def action_dict(a: RowAction) -> dict[str, Any]:
    from ..db.models import iso
    return {"id": a.id, "row_id": a.row_id, "action": a.action, "actor": a.actor, "at": iso(a.at), "original": a.original, "chosen": a.chosen, "reason": a.reason, "batch": a.batch}


def suggestion(st) -> dict[str, Any]:
    """The system's suggestion for a row right now: what the officer is acting on."""
    m = st.mapping or {}
    return {"aud_code": m.get("aud_code"), "credits": m.get("aud_credits") or (transfer_credits() if m else None), "status": st.status, "kind": st.kind,
            "mapping_version": m.get("version"), "mapping_id": m.get("id")}


def current_choice(st, act: RowAction | None) -> dict[str, Any] | None:
    """What the row stands at: the officer's choice if they made one, else the suggestion. None = no credit."""
    if act is not None and act.action in RESOLVING:
        return act.chosen or None
    s = suggestion(st)
    return {"aud_code": s["aud_code"], "credits": s["credits"]} if s["aud_code"] else None


def _check_target(code: str, grade: str | None) -> None:
    repo = rt().repo
    if code not in repo.targets:
        raise ActionError(422, f"{code} is not an AUD course")
    fails = mp.policy_fails(grade, code, repo)
    if fails:
        raise ActionError(409, "Blocked by policy: " + "; ".join(fails))


def record(db: Session, app: Application, row: dict[str, Any], action: str, actor: str, role: str, aud_code: str | None = None,
           credits: int | None = None, reason: str = "", batch: bool = False) -> RowAction:
    st = mp.get_state(db, app.id, row["id"])
    if st is None or st.status == mp.NOT_EVAL:
        raise ActionError(409, "This course is not evaluated; include it first")
    if action in NEEDS_REASON and not reason.strip():
        raise ActionError(422, "A reason is required")
    sug = suggestion(st)
    chosen: dict[str, Any] = {}
    if action == "CONFIRM":
        if st.status != mp.MAPPED or not sug["aud_code"]:
            raise ActionError(409, "There is no mapped suggestion to confirm for this course")
        chosen = {"aud_code": sug["aud_code"], "credits": sug["credits"]}
    elif action in ("CHANGE", "CORRECT"):
        code = aud_code or sug["aud_code"]
        if not code:
            raise ActionError(422, "Pick an AUD course")
        if credits is not None and not 1 <= credits <= 12:
            raise ActionError(422, "Credits must be between 1 and 12")
        _check_target(code, row.get("grade"))
        chosen = {"aud_code": code, "credits": credits if credits is not None else (sug["credits"] if code == sug["aud_code"] else transfer_credits())}
        if action == "CHANGE" and code == sug["aud_code"] and chosen["credits"] == sug["credits"]:
            raise ActionError(422, "Nothing changed: use Confirm instead")
    elif action not in ("REJECT", "NO_CREDIT", "SEND_FOR_REVIEW"):
        raise ActionError(422, f"Unknown action {action}")
    a = RowAction(app_id=app.id, row_id=row["id"], action=action, actor=actor, actor_role=role, original=sug, chosen=chosen, reason=reason.strip(), batch=batch)
    db.add(a)
    db.commit()
    return a


def bulk_confirmable(st) -> bool:
    """A mapped row can be confirmed in bulk only if nothing about it needs a second look: no doubt on it, and its equivalent is not outside the program plan."""
    f = st.facts or {}
    return not f.get("_doubts") and (f.get("_class") or {}).get("value") != "Not applicable"


def confirm_all_mapped(db: Session, app: Application, actor: str, role: str) -> list[RowAction]:
    done = latest(db, app.id)
    out = []
    for r in app.rows:
        st = mp.get_state(db, app.id, r["id"])
        a = done.get(r["id"])
        if st and st.status == mp.MAPPED and not (a and a.action in RESOLVING) and bulk_confirmable(st):
            out.append(record(db, app, r, "CONFIRM", actor, role, batch=True))
    return out


def unresolved(db: Session, app: Application) -> list[dict[str, Any]]:
    """Relevant rows (anything evaluated) that still have no resolving action."""
    done = latest(db, app.id)
    res = []
    for r in app.rows:
        st = mp.get_state(db, app.id, r["id"])
        if st and st.status != mp.NOT_EVAL and not (done.get(r["id"]) and done[r["id"]].action in RESOLVING):
            res.append({"row_id": r["id"], "title": r.get("title"), "status": st.status})
    return res
