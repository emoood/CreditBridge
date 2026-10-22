"""One live end-to-end pass through the API, exactly as the UI does it: upload -> poll reading -> verify -> confirm -> poll
evaluation -> results -> decision. Prints REAL timings. Uses a synthetic transcript only."""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000/api/v1"
H = {"X-Role": "admissions", "X-Actor": "E2E Officer"}
FIX = ROOT / "fixtures" / "demo_v2"
name = sys.argv[1] if len(sys.argv) > 1 else "school_transcript.pdf"
truth = json.loads((FIX / "expected.json").read_text())
exp_rows = [r for t in truth["school_transcript"]["terms"] for r in t["rows"]]
c = httpx.Client(base_url=BASE, timeout=60)
T0 = time.time()
stamp: dict[str, float] = {}


def poll(job_id: str, label: str):
    last = ""
    while True:
        j = c.get(f"/jobs/{job_id}").json()
        line = f"{j['status']:9} stage={j['stage']:11} unit={j['current_unit']:12} " + " ".join(f"{u['key']}:{u['status'][:4]}" for u in j["units"] if u["stage"] in ("read", "evaluate"))
        if line != last:
            print(f"  [{time.time() - T0:6.1f}s] {label}: {line}", flush=True)
            last = line
        if j["status"] in ("DONE", "FAILED", "INTERRUPTED"):
            return j
        time.sleep(2)


print(f"== upload {name}")
t = time.time()
up = c.post("/applications", files={"file": (name, (FIX / name).read_bytes(), "application/pdf")}, headers=H).json()
stamp["upload_response_s"] = round(time.time() - t, 2)
print(f"  job_id returned in {stamp['upload_response_s']}s: {up['job_id']} app {up['id']} status {up['status']}")
rj = poll(up["job_id"], "reading")
app = c.get(f"/applications/{up['id']}").json()
print(f"== reading done: status={app['status']} rows={len(app['rows'])} method={app['extraction']['method']} failed_blocks={app['extraction'].get('failed_blocks')}")
print(f"   timings: total={rj['timings']['total_secs']}s per stage={rj['timings']['per_stage_secs']}")
print("   block units:", [(u["key"], u["status"], u["method"], f"{u['duration_s']}s", f"x{u['attempts']}") for u in rj["units"] if u["stage"] == "read"])
print("   cross-check flags:", app["extraction"]["flags"] or "none", "| cumulative read:", app["extraction"].get("cumulative"))
hit = bad = 0
for e in exp_rows:
    r = next((x for x in app["rows"] if (x["title"] or "").lower() == e["subject"].lower()), None)
    if r and r["mark"] == e["mark"] and r["grade"] == e["letter"] and r["points"] == e["points"] and float(r["credits"] or -1) == e["credits"]:
        hit += 1
    else:
        bad += 1
        print("   MISMATCH:", e, "got", r and {k: r[k] for k in ("title", "mark", "grade", "points", "credits")})
print(f"   extraction vs answer key: {hit}/{len(exp_rows)} rows exactly right (subject, mark, letter, points, credits)")
for r in app["rows"]:
    print(f"   row {r['title'][:38]:38} -> {(r['subject'] or {}).get('name', 'UNRECOGNIZED'):34} {r['curriculum_status']}")

print("== evaluation before confirmation must be refused:", c.post(f"/applications/{up['id']}/evaluate", headers=H).status_code)
t = time.time()
cf = c.post(f"/applications/{up['id']}/confirm", headers=H).json()
print(f"== confirm -> {cf} ({round(time.time() - t, 2)}s); replay ->", c.post(f"/applications/{up['id']}/confirm", headers=H).json())
ej = poll(cf["job_id"], "evaluation")
res = c.get(f"/applications/{up['id']}/results").json()
print(f"== evaluation done: app status={res['status']} counts={res['counts']} pending={res['pending_units']}")
print(f"   timings: total={ej['timings']['total_secs']}s per stage={ej['timings']['per_stage_secs']}")
for r in res["results"]:
    m = r["match"]
    print(f"   {r['course']['title'][:34]:34} {r['triage']['category']:16} {m['status']:27} -> {(m['target'] or {}).get('code', '-'):9} review={(r['review'] or {}).get('status', '-'):26} {r['llm_calls']:>2} calls {r['duration_s']:>6}s")
print("   evaluate unit seconds:", {u["key"]: u["duration_s"] for u in ej["units"] if u["stage"] == "evaluate"})
ev = c.get("/dev/events?limit=300").json()
llm = [e for e in ev if e["kind"] == "llm_call"]
leak = [e for e in ev if e["kind"] == "llm_call" and e.get("stage") == "read" and "request_messages" in e and e["request_messages"] != "[redacted: document content]"]
print(f"== model calls logged: {len(llm)} (reads {sum(1 for e in llm if e.get('stage') == 'read')}); read events with document content in the log: {len(leak)}")
print(f"== TOTAL wall time {round(time.time() - T0, 1)}s")
print(json.dumps({"app_id": up["id"], "reading_job": up["job_id"], "evaluation_job": cf["job_id"], **stamp}))
