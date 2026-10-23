"""Drive one synthetic demo transcript through the REAL API and the local model exactly once, collecting what the report needs:
timings, model calls (from the usage ledger), the agents' tool calls (from the trace) and what went wrong. Saves a recording only when the run is clean
(no failed job, no refused tool call, no agent failure) and, for B, only when the Intake recheck really re-read the block and the corrected value passed.

    python scripts/overnight_runs.py a2 | a2c | a2m | b | c | d | dp   # one run, no retries (a2c: copy-error variant, a2m: planted misread)
Synthetic fixtures only. Output: data/overnight/<name>.json (gitignored) plus a summary on stdout."""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "fixtures" / "demo_v3"
OUT = ROOT / "data" / "overnight"
OUT.mkdir(parents=True, exist_ok=True)
c = httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=300)
H = {"X-Role": "admissions", "X-Actor": "Overnight Officer"}
T0 = time.time()
log: dict = {"steps": {}, "problems": []}


def now() -> float:
    return round(time.time() - T0, 1)


def wait(job_id: str, every: float = 2.0) -> dict:
    while True:
        j = c.get(f"/jobs/{job_id}").json()
        if j["status"] in ("DONE", "FAILED", "INTERRUPTED"):
            return j
        time.sleep(every)


def upload(file: str, name: str, sid: str, plant: bool = False, case_number: str = "") -> tuple[str, str]:
    p = V3 / file
    r = c.post("/applications", files={"file": (file, p.read_bytes(), "application/pdf")}, headers=H,
               data={"student_label": name, "student_id": sid, **({"case_number": case_number} if case_number else {}), **({"plant_misread": "1"} if plant else {})}).json()
    log["app_id"] = r["id"]
    return r["id"], r["job_id"]


def read_phase(app_id: str, job_id: str) -> dict:
    j = wait(job_id)
    log["steps"]["time_to_table_s"] = now()
    log["reading"] = {"status": j["status"], "stage_secs": j["timings"]["per_stage_secs"], "total_s": j["timings"]["total_secs"], "summary": {k: v for k, v in j["summary"].items() if k in ("recovery_used", "intake_changes")}}
    if j["status"] != "DONE":
        log["problems"].append(f"reading job {j['status']} {j['flags']}")
    app = c.get(f"/applications/{app_id}").json()
    log["extraction"] = {k: v for k, v in app["extraction"].items() if k != "blocks"}
    log["extraction"]["blocks"] = app["extraction"].get("blocks")
    log["rows"] = [{k: r.get(k) for k in ("title", "mark", "grade", "points", "credits", "flags", "questions")} | {"subject": (r.get("subject") or {}).get("code")} for r in app["rows"]]
    return app


def evaluate_phase(app_id: str) -> dict:
    t = now()
    cf = c.post(f"/applications/{app_id}/confirm", headers=H).json()
    first = None
    while True:
        j = c.get(f"/jobs/{cf['job_id']}").json()
        if first is None and any(u["stage"] == "evaluate" and u["status"] == "DONE" for u in j["units"]):
            first = now()
        if j["status"] in ("DONE", "FAILED", "INTERRUPTED"):
            break
        time.sleep(1.5)
    log["steps"].update({"confirm_at_s": t, "time_to_first_result_s": first, "evaluation_done_s": now(), "evaluation_job_s": j["timings"]["total_secs"]})
    if j["status"] != "DONE":
        log["problems"].append(f"evaluation job {j['status']} {j['flags']}")
    return j


def snapshot_states(app_id: str) -> dict:
    res = c.get(f"/applications/{app_id}/results").json()
    rows = {r["id"]: r["title"] for r in c.get(f"/applications/{app_id}").json()["rows"]}
    return {rows[k]: {"status": v["status"], "kind": v["kind"], "reason": v["reason"][:140], "finding": (v.get("finding") or {}).get("headline"), "requests": [q["what"] for q in v["requests"]]}
            for k, v in res["states"].items()}


def collect_activity(app_id: str) -> dict:
    a = c.get(f"/applications/{app_id}/activity").json()
    agent = [e for e in a["trace"] if e["actor"] not in ("code",)]
    refused = [e for e in agent if not e["ok"]]
    ledger = c.get(f"/applications/{app_id}/activity").json()["usage"]
    log["usage"] = ledger
    log["trace"] = a["trace"]
    log["tool_calls"] = [(e["actor"], e["name"], e["ok"]) for e in agent]
    log["refused_tool_calls"] = [(e["actor"], e["name"], e["detail"][:160]) for e in refused]
    log["calls_per_stage"] = {k: {**v, "sec_per_call": round(v["seconds"] / v["calls"], 2) if v["calls"] else None} for k, v in ledger["by_stage"].items()}
    log["total_model_calls"] = ledger["calls"]
    log["mean_sec_per_call"] = round(ledger["seconds"] / ledger["calls"], 2) if ledger["calls"] else None
    if refused:
        log["problems"].append(f"{len(refused)} refused tool call(s): {[(e['actor'], e['name']) for e in refused]}")
    return a


def finish(name: str, rec_name: str | None, ok: bool, case_number: str = "") -> None:
    log["clean"] = ok and not log["problems"]
    log["total_wall_s"] = now()
    if log["clean"] and rec_name:
        sys.path.insert(0, str(ROOT))
        from backend.app.services import (
            prepared,  # the model replies captured during this real run become a prepared run (fixtures/prepared_runs)
        )
        log["recording"] = prepared.save(log["app_id"], rec_name, case_number, notes=f"real run, timings {log['steps']}")
    else:
        log["recording"] = None
    (OUT / f"{name}.json").write_text(json.dumps(log, indent=1, default=str))
    keep = {k: log.get(k) for k in ("app_id", "steps", "reading", "total_model_calls", "mean_sec_per_call", "calls_per_stage", "tool_calls", "refused_tool_calls", "problems", "clean", "recording", "total_wall_s")}
    print(json.dumps(keep, indent=1, default=str))


def _audit_and_wait(app_id: str, tag: str) -> dict:
    t = now()
    chk = c.post(f"/applications/{app_id}/export-check", headers=H).json()
    if chk.get("job_id"):
        wj = wait(chk["job_id"])
        if wj["flags"]:
            log["problems"].append(f"export audit flags {wj['flags']}")
    log["steps"][f"export_audit_{tag}_s"] = round(now() - t, 1)
    sh = c.get(f"/applications/{app_id}/sheet").json()
    log[f"export_audit_{tag}"] = {k: (sh["audit"] or {}).get(k) for k in ("clean", "summary", "warnings", "differences", "proposals", "explanations", "flags", "agent_comment")}
    return sh


def run_a2(variant: str = "clean") -> None:
    """Rania (BBA, A2). clean: Statistics gets a score of 5, Human Geography waits on a syllabus. copy: also the planted copy-error and the Auditor catch.
    misread: the planted misread (AP Chemistry's letter) must be repaired by the Intake agent."""
    plant = variant == "misread"
    app_id, job = upload("transcript_a2_batch.pdf", "Rania Haddad", "SYN-1004", plant=plant, case_number="CB-2026-0104")
    app = read_phase(app_id, job)
    intake = app["extraction"].get("intake") or {}
    planted = (app["extraction"].get("planted_misread") or {}).get("applied") or []
    if plant:
        changes = intake.get("changes") or []
        log["planted_applied"] = planted
        fixed = bool(planted) and any(str(c_.get("row")) == str(planted[0]["subject"]) and c_.get("field") == planted[0]["field"] and c_.get("to") == planted[0]["from"] for c_ in changes)
        log["repair_genuine"] = bool(fixed and intake.get("recovery_used", 0) >= 1)
        if not log["repair_genuine"]:
            log["problems"].append("no genuine repair of the planted value: not recorded")
    evaluate_phase(app_id)
    log["states_after_evaluation"] = snapshot_states(app_id)
    st0 = log["states_after_evaluation"]
    rows = {r["title"]: r["id"] for r in c.get(f"/applications/{app_id}").json()["rows"]}
    hg = st0.get("AP Human Geography", {})
    log["human_geography"] = hg
    if hg.get("status") != "Waiting on document" or not hg.get("requests"):
        log["problems"].append(f"AP Human Geography is {hg.get('status')} with requests {hg.get('requests')}; expected Waiting on document with a drafted request")
    stat0 = st0.get("AP Statistics", {})
    if stat0.get("kind") != "condition_missing":
        log["problems"].append(f"AP Statistics is {stat0.get('status')}/{stat0.get('kind')}; expected Conditional, exam score missing")
    t = now()
    j = c.post(f"/applications/{app_id}/rows/{rows['AP Statistics']}/evidence", data={"facts": '{"exam_score": 5}'}, headers=H).json()
    wait(j["job_id"])
    log["steps"]["score_resume_s"] = round(now() - t, 1)
    st1 = snapshot_states(app_id)
    log["stat_after"] = st1["AP Statistics"]
    changed = sorted(k for k in st1 if st1[k] != st0.get(k))
    log["rows_changed_by_the_score"] = changed
    if st1["AP Statistics"]["status"] != "Mapped":
        log["problems"].append("AP Statistics did not become Mapped after the exam score")
    if changed != ["AP Statistics"]:
        log["problems"].append(f"submitting the score changed {changed}; only AP Statistics should resume")
    log["confirm_mapped"] = c.post(f"/applications/{app_id}/confirm-mapped", headers=H).json()
    log["states_final"] = snapshot_states(app_id)
    sh = _audit_and_wait(app_id, "clean")
    log["sheet_ok"] = sh["ok"]
    ok = not log["problems"]
    if variant == "copy":
        log["planted"] = c.post(f"/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers=H).json()
        pl = (log["planted"] or {}).get("planted") or {}
        sh = _audit_and_wait(app_id, "planted")
        diffs = [d for d in (sh["audit"] or {}).get("differences", [])]
        expl = json.dumps((sh["audit"] or {}).get("explanations") or diffs)
        log["detected_by_checks_or_diff"] = bool(sh["checks"] or diffs)
        match = bool(pl) and any(d.get("row_id") == pl.get("row_id") for d in diffs) and pl["from"] in expl and pl["to"] in expl and len(diffs) == 1
        log["auditor_explanation_matches_verified_difference"] = match
        if not match:
            log["problems"].append("the Auditor's explanation does not match the verified difference: the copy-error variant is not recorded")
        ok = ok and match
        log["audit_planted"] = sh["audit"]
    collect_activity(app_id)
    name = {"clean": "demo_ap_rania_clean", "copy": "demo_ap_rania_copy_error", "misread": "demo_ap_rania_misread"}[variant]
    if variant == "misread":
        ok = ok and log.get("repair_genuine", False)
    finish("a2" if variant == "clean" else f"a2_{variant}", name if ok else None, ok, "CB-2026-0104")


def run_b() -> None:
    app_id, job = upload("transcript_b_intake.pdf", "Daniel Ferreira", "SYN-1002", plant=True, case_number="CB-2026-0102")
    app = read_phase(app_id, job)
    collect_activity(app_id)
    chem = next((r for r in app["rows"] if "Chemistry" in (r["title"] or "")), None)
    log["chemistry_final"] = chem and {"mark": chem.get("mark"), "grade": chem["grade"], "flags": chem.get("flags"), "questions": chem.get("questions")}
    intake = app["extraction"].get("intake") or {}
    planted = (app["extraction"].get("planted_misread") or {}).get("applied") or []
    changes = intake.get("changes") or []
    log["planted_applied"] = planted
    fixed = bool(planted) and any(str(c_.get("row")) == str(planted[0]["subject"]) and c_.get("field") == "mark" and c_.get("to") == planted[0]["from"] for c_ in changes)
    log["recheck_really_fixed"] = bool(fixed and intake.get("recovery_used", 0) >= 1 and chem and chem.get("mark") == planted[0]["from"] and not chem.get("flags"))
    if not log["recheck_really_fixed"]:
        log["problems"].append("no genuine repair: the planted wrong mark was not replaced by a re-read value that passes its check, so B is not recorded")
    if log["recheck_really_fixed"]:
        evaluate_phase(app_id)
        log["states_after_evaluation"] = snapshot_states(app_id)
        collect_activity(app_id)
    finish("b", "demo_ap_daniel_clean", log["recheck_really_fixed"], "CB-2026-0102")


def run_d(plant: bool = False) -> None:
    app_id, job = upload("transcript_d_demo2.pdf", "Rami Test-Student", "DVIS-SYN-0457", plant=plant, case_number="CB-2026-0105")
    app = read_phase(app_id, job)
    collect_activity(app_id)
    log["synopsis"] = app["extraction"].get("synopsis")
    intake = app["extraction"].get("intake") or {}
    planted = (app["extraction"].get("planted_misread") or {}).get("applied") or []
    log["planted_applied"] = planted
    if plant:
        changes = intake.get("changes") or []
        fixed = bool(planted) and any(str(c_.get("row")) == str(planted[0]["subject"]) and c_.get("field") == planted[0]["field"] and c_.get("to") == planted[0]["from"] for c_ in changes)
        log["repair_genuine"] = bool(fixed and intake.get("recovery_used", 0) >= 1)
        if not log["repair_genuine"]:
            log["problems"].append("no genuine repair of the planted value: not recorded")
    evaluate_phase(app_id)
    log["states_after_evaluation"] = snapshot_states(app_id)
    rows = {r["title"]: r["id"] for r in c.get(f"/applications/{app_id}").json()["rows"]}
    if "AP Calculus BC" in rows:
        j = c.post(f"/applications/{app_id}/rows/{rows['AP Calculus BC']}/evidence", data={"facts": '{"exam_score": 5}'}, headers=H).json()
        wait(j["job_id"])
        log["calc_after"] = snapshot_states(app_id)["AP Calculus BC"]
    log["confirm_mapped"] = c.post(f"/applications/{app_id}/confirm-mapped", headers=H).json()
    chk = c.post(f"/applications/{app_id}/export-check", headers=H).json()
    if chk.get("job_id"):
        wait(chk["job_id"])
    sh = c.get(f"/applications/{app_id}/sheet").json()
    log["export_audit"] = {k: (sh["audit"] or {}).get(k) for k in ("clean", "summary", "warnings", "differences", "proposals", "flags")}
    log["states_final"] = snapshot_states(app_id)
    log["rows_final"] = [{k: r.get(k) for k in ("title", "grade", "mark", "credits", "term")} for r in c.get(f"/applications/{app_id}").json()["rows"]]
    collect_activity(app_id)
    ok = (not plant) or log.get("repair_genuine", False)
    finish("d_planted" if plant else "d", ("demo_ap_rami_misread" if plant else "demo_ap_rami_clean") if ok else None, ok, "CB-2026-0105")


def run_c() -> None:
    app_id, job = upload("transcript_c_copy_error.pdf", "Sofia Lindqvist", "SYN-1003", case_number="CB-2026-0103")
    read_phase(app_id, job)
    evaluate_phase(app_id)
    log["states_after_evaluation"] = snapshot_states(app_id)
    log["confirm_mapped"] = c.post(f"/applications/{app_id}/confirm-mapped", headers=H).json()
    log["sheet_before"] = c.get(f"/applications/{app_id}/sheet").json()["ok"]
    log["planted"] = c.post(f"/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers=H).json()
    sh = c.get(f"/applications/{app_id}/sheet").json()
    log["sheet_checks_after_plant"] = sh["checks"]
    t = now()
    j = c.post(f"/applications/{app_id}/audit", headers=H).json()
    wj = wait(j["job_id"])
    log["steps"]["auditor_s"] = round(now() - t, 1)
    audit = c.get(f"/applications/{app_id}/sheet").json()
    log["audit"] = audit["audit"]
    if wj["flags"]:
        log["problems"].append(f"audit job flags {wj['flags']}")
    collect_activity(app_id)
    ok_detect = bool(sh["checks"]) and len(sh["checks"]) == 1 and sh["checks"][0]["row_id"] == log["planted"]["planted"]["row_id"]
    log["detected_through_normal_checks"] = ok_detect
    proposals = (audit["audit"] or {}).get("proposals", [])
    log["auditor_proposal_fixes_it"] = bool((audit["audit"] or {}).get("passes_after")) and any(p["row_id"] == log["planted"]["planted"]["row_id"] and p["to"]["aud_code"] == log["planted"]["planted"]["from"] for p in proposals)
    if not log["auditor_proposal_fixes_it"]:
        log["problems"].append("the Auditor's proposal does not restore the original equivalent (or the corrected sheet does not pass)")
    finish("c", "demo_ap_sofia_copy_error", ok_detect and log["auditor_proposal_fixes_it"], "CB-2026-0103")


if __name__ == "__main__":
    {"a2": run_a2, "a2c": lambda: run_a2("copy"), "a2m": lambda: run_a2("misread"), "b": run_b, "c": run_c, "d": run_d, "dp": lambda: run_d(True)}[sys.argv[1]]()
