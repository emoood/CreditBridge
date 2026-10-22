"""Every endpoint the UI calls (frontend/src/api.ts) exists and returns the fields the UI's types read."""
import pytest

from backend.tests.conftest import ADMIN, read_to_gate

pytestmark = pytest.mark.asyncio

APP_KEYS = {"id", "kind", "student", "program", "institution", "courses", "status", "decided", "catalog", "created_at"}
ROW_KEYS = {"id", "code", "title", "title_ar", "credits", "grade", "term", "page", "uncertain_fields", "subject", "candidates", "included", "corrected"}
JOB_KEYS = {"id", "status", "courses", "errors", "route", "model", "llm_calls", "activity", "phase", "stage", "current_unit", "units", "timings", "flags"}
RESULT_KEYS = {"course", "triage", "match", "review", "rules", "staff_checklist", "case_file", "revised", "contested", "llm_calls", "provider",
               "model", "duration_s", "is_replay", "syllabus_requests", "original_claim", "decision", "version"}
COURSE_KEYS = {"code", "row_code", "title", "title_ar", "credits", "grade", "term", "has_syllabus", "curriculum_subject", "curriculum_qualification"}
MATCH_KEYS = {"status", "target", "evidence_strength", "supported_outcomes", "missing_or_weaker_outcomes", "concerns", "response_to_objections", "flags"}


def has(d, keys, where):
    assert keys <= set(d), f"{where}: missing {keys - set(d)}"


async def test_every_ui_call_exists_with_the_shape_the_ui_reads(env):
    c = env.client
    # --- applications list + demo detail
    r = await c.get("/api/v1/applications")
    assert r.status_code == 200 and r.json()
    has(r.json()[0], APP_KEYS, "GET /applications")
    d = (await c.get("/api/v1/applications/DEMO-001")).json()
    has(d, APP_KEYS | {"rows", "extraction", "curriculum"}, "GET /applications/{id}")
    has(d["rows"][0], ROW_KEYS, "demo row")
    # --- upload -> reading job (UI polls /jobs/{id})
    up = await read_to_gate(env)
    has(up, APP_KEYS | {"job_id"}, "POST /applications")
    job = (await c.get(f"/api/v1/jobs/{up['job_id']}")).json()
    has(job, JOB_KEYS, "GET /jobs/{id}")
    assert job["status"] == "DONE" and {u["stage"] for u in job["units"]} >= {"read", "curriculum"}
    assert set(job["units"][0]) >= {"stage", "key", "status", "attempts", "method", "error_code", "duration_s"}
    assert job["timings"]["per_stage_secs"] and job["timings"]["total_secs"] is not None
    detail = (await c.get(f"/api/v1/applications/{up['id']}")).json()
    has(detail, APP_KEYS | {"rows", "extraction", "document", "curriculum", "corrections"}, "uploaded detail")
    assert detail["status"] == "EXTRACTION_REVIEW_REQUIRED" and "path" not in detail["document"]
    has(detail["extraction"], {"method", "blocks", "flags", "unrecognized"}, "extraction")
    has(detail["rows"][0], ROW_KEYS | {"mark", "points", "flags", "curriculum_status"}, "row")
    # --- verification table calls
    rid = detail["rows"][0]["id"]
    assert (await c.patch(f"/api/v1/applications/{up['id']}/rows/{rid}", json={"term": "Fall 2025"}, headers=ADMIN)).status_code == 200
    new = (await c.post(f"/api/v1/applications/{up['id']}/rows", headers=ADMIN)).json()
    has(new, ROW_KEYS, "added row")
    assert (await c.delete(f"/api/v1/applications/{up['id']}/rows/{new['id']}", headers=ADMIN)).json() == {"deleted": True}
    lk = await c.post(f"/api/v1/applications/{up['id']}/link", json={"qualification": None, "relink": True}, headers=ADMIN)
    has(lk.json(), {"rows", "id"}, "POST /link")
    pv = await c.get(f"/api/v1/applications/{up['id']}/preview/1")
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/png"
    # --- curriculum
    q = (await c.get("/api/v1/curriculum/qualifications")).json()
    has(q, {"qualifications", "available", "error"}, "qualifications")
    subs = (await c.get("/api/v1/curriculum/subjects", params={"q": "calc"})).json()
    has(subs[0], {"id", "name", "code", "qualification"}, "subjects")
    # --- confirm (gate) -> evaluation job -> results
    cf = (await c.post(f"/api/v1/applications/{up['id']}/confirm", headers=ADMIN)).json()
    has(cf, {"job_id", "already_confirmed"}, "POST /confirm")
    await env.worker.idle()
    res = (await c.get(f"/api/v1/applications/{up['id']}/results")).json()
    has(res, {"results", "counts", "catalog", "job", "pending_units", "status"}, "GET /results")
    has(res["results"][0], RESULT_KEYS, "result")
    has(res["results"][0]["course"], COURSE_KEYS, "result.course")
    has(res["results"][0]["match"], MATCH_KEYS, "result.match")
    has(res["results"][0]["triage"], {"category", "reasons", "priority"}, "triage")
    ejob = (await c.get(f"/api/v1/jobs/{cf['job_id']}")).json()
    assert ejob["phase"] == "EVALUATION" and ejob["summary"]["triage_counts"] and ejob["summary"]["worklist"]
    # --- decisions (UI sends X-Role / X-Actor, expected_version, idempotency_key)
    code = res["results"][0]["course"]["code"]
    body = {"decision": "DECLINED", "reason": "test", "expected_version": 1, "idempotency_key": "contract-1"}
    dec = await c.post(f"/api/v1/applications/{up['id']}/results/{code}/decision", json=body, headers=ADMIN)
    assert dec.status_code == 200
    has(dec.json(), {"decision", "reason", "actor", "actor_role", "at", "idempotency_key"}, "decision")
    assert (await c.post(f"/api/v1/applications/{up['id']}/results/{code}/decision", json=body, headers=ADMIN)).json() == dec.json()  # replay
    assert (await c.post(f"/api/v1/applications/{up['id']}/results/{code}/decision", json={**body, "idempotency_key": "x"},
                         headers={"X-Role": "registrar"})).status_code == 403
    # --- runtime / health / dev pages
    has((await c.get("/api/v1/config/runtime")).json(), {"route", "model", "last_latency_s"}, "runtime")
    assert (await c.get("/api/v1/dev/events")).status_code == 200
    assert (await c.get(f"/api/v1/applications/{up['id']}/audit")).status_code == 200
    assert (await c.get(f"/api/v1/applications/{up['id']}/jobs")).status_code == 200
