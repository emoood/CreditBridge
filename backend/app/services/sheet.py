"""The spreadsheet (confirmed rows, unconfirmed marked) and the deterministic checks run before it is copied or downloaded.
No model is involved. Columns and their order come from policies/institution.yaml. Failures name the exact row."""
import hashlib
import json
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..db.models import Application, CourseMapping, RowAction, iso
from ..runtime import rt
from . import actions as ac
from . import mapping as mp

COLUMN_NAMES = {"source_course": "Source course", "grade": "Grade", "aud_equivalent": "AUD equivalent", "aud_credits": "AUD credits", "status": "Status"}
CREDITED = ("CONFIRM", "CHANGE", "CORRECT")


def columns() -> list[str]:
    cols = yaml.safe_load((mp.ROOT / "policies" / "institution.yaml").read_text()).get("export", {}).get("columns") or list(COLUMN_NAMES)
    return [c for c in cols if c in COLUMN_NAMES]


def _line(row: dict[str, Any], st, act: RowAction | None, override: dict[str, Any] | None) -> dict[str, Any]:
    choice = {**(ac.current_choice(st, act) or {}), **(override or {})} if (override or act is None or act.action in CREDITED) else None
    code = (choice or {}).get("aud_code")
    t = rt().repo.targets.get(code or "")
    resolved = act is not None and act.action in ac.RESOLVING
    if override:
        label, action = "Confirmed (corrected)", "CORRECT"
    elif act is not None and act.action in CREDITED:
        label, action = "Confirmed", act.action
    elif act is not None and act.action in ("REJECT", "NO_CREDIT"):
        label, action = "No credit", act.action
    else:
        label, action = f"Unconfirmed: {st.status}", act.action if act else ""
    credit_line = bool(code) and (override is not None or (act is not None and act.action in CREDITED) or (not resolved and st.status == mp.MAPPED))
    return {"row_id": row["id"], "source_course": row.get("title") or "", "grade": row.get("grade") or "",
            "aud_code": code if credit_line else None, "aud_equivalent": (f"{code} · {t['title']}" if t else code) if credit_line else ("No credit" if label == "No credit" else ""),
            "aud_credits": (choice or {}).get("credits") if credit_line else None, "status": label, "action": action, "state_status": st.status}


def build(db: Session, app: Application, overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    done = ac.latest(db, app.id)
    lines, checks = [], []
    plant = (app.extraction or {}).get("planted_copy_error")  # PLANTED FOR DEMO: changes one confirmed AUD equivalent in the output stage only
    for r in app.rows:
        st = mp.get_state(db, app.id, r["id"])
        if st is None or st.status == mp.NOT_EVAL:
            continue
        ln = _line(r, st, done.get(r["id"]), (overrides or {}).get(r["id"]))
        base = _line(r, st, done.get(r["id"]), None)  # what the officer's decision (and the mapping record) say, before any planted change or proposed fix
        act = done.get(r["id"])
        ln["decision"] = {"aud_code": base["aud_code"], "aud_credits": base["aud_credits"], "action": act.action if act else "", "by": act.actor if act else "",
                          "at": iso(act.at) if act else None, "status": base["status"]}
        if plant and plant["row_id"] == r["id"] and not (overrides or {}).get(r["id"]) and not (done.get(r["id"]) and done[r["id"]].action == "CORRECT") and ln["aud_code"]:
            t = rt().repo.targets.get(plant["to"])
            ln.update({"aud_code": plant["to"], "aud_equivalent": f"{plant['to']} · {t['title']}" if t else plant["to"], "planted": True})
        lines.append(ln)
        tag = " [planted for demo]" if ln.get("planted") else ""
        fail = lambda check, msg, ln=ln, tag=tag: checks.append({"row_id": ln["row_id"], "source_course": ln["source_course"], "check": check, "message": msg + tag})
        live = db.get(CourseMapping, st.mapping["id"]) if st.mapping and st.mapping.get("id") else None
        confirmed = ln["status"].startswith("Confirmed")
        if ln["action"] == "CONFIRM":  # a plain confirmation must still equal its mapping record
            if live is None or live.status != "ACTIVE":
                fail("matches_mapping", "The mapping record is missing or no longer active")
            else:
                if live.aud_code != ln["aud_code"]:
                    fail("matches_mapping", f"Sheet says {ln['aud_code']} but the mapping record says {live.aud_code}")
                if live.aud_credits != ln["aud_credits"]:
                    fail("credits_match", f"Sheet says {ln['aud_credits']} credits but the mapping record says {live.aud_credits}")
            if st.status != mp.MAPPED:
                fail("no_unresolved_confirmed", f"Marked confirmed but the row is now '{st.status}'")
        if ln["action"] in ("CHANGE", "CORRECT") and not (isinstance(ln["aud_credits"], int) and 1 <= ln["aud_credits"] <= 12):
            fail("credits_match", f"Credits {ln['aud_credits']} are not a valid number (1 to 12)")
        if confirmed and not ln["aud_code"]:
            fail("no_unresolved_confirmed", "Marked confirmed but has no AUD course")
        if ln["aud_code"]:
            for msg in mp.policy_fails(r.get("grade"), ln["aud_code"], rt().repo):
                fail("policy_respected", msg)
    seen: dict[str, dict[str, Any]] = {}
    for ln in lines:
        if ln["aud_code"] and ln["status"].startswith(("Confirmed", "Unconfirmed")):
            if ln["aud_code"] in seen:
                checks.append({"row_id": ln["row_id"], "source_course": ln["source_course"], "check": "no_duplicates",
                               "message": f"{ln['aud_code']} is already credited for {seen[ln['aud_code']]['source_course']}"})
            else:
                seen[ln["aud_code"]] = ln
    diff = [{"row_id": ln["row_id"], "source_course": ln["source_course"], "field": f, "sheet": ln[f], "decision": ln["decision"][f], "action": ln["decision"]["action"],
             "by": ln["decision"]["by"], "at": ln["decision"]["at"]}
            for ln in lines for f in ("aud_code", "aud_credits") if ln[f] != ln["decision"][f]]
    warnings = []
    for ln in lines:
        st = mp.get_state(db, app.id, ln["row_id"])
        cls = (st.facts or {}).get("_class") if st else None
        if ln["aud_code"] and ln["status"].startswith("Confirmed") and cls and cls["value"] == "Not applicable":
            warnings.append({"row_id": ln["row_id"], "source_course": ln["source_course"], "check": "program_plan", "message": f"{ln['aud_code']} is not on the plan for {app.program}"})
    cols = columns()
    digest = hashlib.sha256(json.dumps([[ln["row_id"], ln["aud_code"], ln["aud_credits"], ln["status"]] for ln in lines], sort_keys=True).encode()).hexdigest()[:16]
    return {"sheet_hash": digest, "diff": diff, "warnings": warnings, "columns": cols, "headers": [COLUMN_NAMES[c] for c in cols], "rows": lines, "checks": checks, "ok": not checks,
            "unconfirmed": sum(1 for ln in lines if ln["status"].startswith("Unconfirmed")),
            "planted": plant if plant and any(ln.get("planted") for ln in lines) else None,
            "note": "AUD codes come from placeholder mapping data, not from AUD."}
