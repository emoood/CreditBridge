"""Drive one synthetic demo transcript through the REAL API and the local model exactly once, collecting what the report needs:
timings, model calls (from the usage ledger), the agents' tool calls (from the trace) and what went wrong. Saves a recording only when the run is clean
(no failed job, no refused tool call, no agent failure) and, for B, only when the Intake recheck really re-read the block and the corrected value passed.

    python scripts/overnight_runs.py a2 | b | c          # one run, no retries
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


def upload(file: str, name: str, sid: str, plant: bool = False) -> tuple[str, str]:
    p = V3 / file
    r = c.post("/applications", files={"file": (file, p.read_bytes(), "application/pdf")}, headers=H,
               data={"student_label": name, "student_id": sid, **({"plant_misread": "1"} if plant else {})}).json()
    log["app_id"] = r["id"]
    return r["id"], r["job_id"]


def read_phase(app_id: str, job_id: str) -> dict:
    j = wait(job_id)
    log["steps"]["time_to_table_s"] = now()
    log["reading"] = {"status": j["status"], "stage_secs": j["timings"]["per_stage_secs"], "total_s": j["timings"]["total_secs"], "summary": {k: v for k, v in j["summary"].items() if k in ("rechecks_used", "intake_changes")}}
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


def finish(name: str, rec_name: str | None, ok: bool) -> None:
    log["clean"] = ok and not log["problems"]
    log["total_wall_s"] = now()
    if log["clean"] and rec_name:
        r = c.post("/recordings", json={"app_id": log["app_id"], "name": rec_name, "timings": log["steps"] | {"total_wall_s": log["total_wall_s"]}}, headers=H)
        log["recording"] = {"name": rec_name, "http": r.status_code}
    else:
        log["recording"] = None
    (OUT / f"{name}.json").write_text(json.dumps(log, indent=1, default=str))
    keep = {k: log.get(k) for k in ("app_id", "steps", "reading", "total_model_calls", "mean_sec_per_call", "calls_per_stage", "tool_calls", "refused_tool_calls", "problems", "clean", "recording", "total_wall_s")}
    print(json.dumps(keep, indent=1, default=str))


def run_a2() -> None:
    app_id, job = upload("transcript_a2_batch.pdf", "Rania Haddad", "SYN-1004")
    read_phase(app_id, job)
    evaluate_phase(app_id)
    log["states_after_evaluation"] = snapshot_states(app_id)
    rows = {r["title"]: r["id"] for r in c.get(f"/applications/{app_id}").json()["rows"]}
    calc = rows["AP Calculus AB"]
    log["calc_before"] = log["states_after_evaluation"]["AP Calculus AB"]
    t = now()
    j = c.post(f"/applications/{app_id}/rows/{calc}/evidence", data={"facts": '{"exam_score": 5}'}, headers=H).json()
    wait(j["job_id"])
    log["steps"]["calc_resume_s"] = round(now() - t, 1)
    log["calc_after"] = snapshot_states(app_id)["AP Calculus AB"]
    log["confirm_mapped"] = c.post(f"/applications/{app_id}/confirm-mapped", headers=H).json()
    log["states_final"] = snapshot_states(app_id)
    collect_activity(app_id)
    finish("a2", "demo_a2", log["calc_after"]["status"] == "Mapped")
    if log["calc_after"]["status"] != "Mapped":
        log["problems"].append("AP Calculus AB did not become Mapped after the exam score")


def run_b() -> None:
    app_id, job = upload("transcript_b_intake.pdf", "Daniel Ferreira", "SYN-1002", plant=True)
    app = read_phase(app_id, job)
    collect_activity(app_id)
    chem = next((r for r in app["rows"] if "Chemistry" in (r["title"] or "")), None)
    log["chemistry_final"] = chem and {"mark": chem.get("mark"), "grade": chem["grade"], "questions": chem.get("questions")}
    changes = log["reading"]["summary"].get("intake_changes") or []
    fixed = any("Chemistry" in str(c_.get("row")) and c_.get("field") == "mark" and c_.get("to") == 85 for c_ in changes)
    reread = any(n == "reread_block" and ok for _, n, ok in log["tool_calls"])
    log["recheck_really_fixed"] = bool(fixed and reread and chem and chem.get("mark") == 85 and not (chem.get("flags")))
    if not log["recheck_really_fixed"]:
        log["problems"].append("the Intake recheck did not re-read the block and produce a corrected value that passes the check: not recorded")
    if log["recheck_really_fixed"]:
        evaluate_phase(app_id)
        log["states_after_evaluation"] = snapshot_states(app_id)
        collect_activity(app_id)
    finish("b", "demo_b_intake", log["recheck_really_fixed"])


def run_c() -> None:
    app_id, job = upload("transcript_c_copy_error.pdf", "Sofia Lindqvist", "SYN-1003")
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
    finish("c", "demo_c_copy_error", ok_detect and log["auditor_proposal_fixes_it"])


if __name__ == "__main__":
    {"a2": run_a2, "b": run_b, "c": run_c}[sys.argv[1]]()
