"""Check Demo mode end to end against a prepared run. NO MODEL IS CALLED: the model's replies come from fixtures/prepared_runs. Needs the backend running
(start it with WARMUP_ON_START=false so it does not touch Ollama). Prints timings, the model-call split (live must be 0) and every row's status and program class.

    python scripts/demo_flow.py [fast|instant] [fixture file name] [case number]
    python scripts/demo_flow.py instant transcript_a2_batch.pdf CB-2026-0104

Run it twice with POST /api/v1/dev/reset between; the printed rows must be identical."""
import json
import sys
import time
from pathlib import Path

import httpx

V3 = Path(__file__).resolve().parents[1] / "fixtures" / "demo_v3"
speed = sys.argv[1] if len(sys.argv) > 1 else "fast"
fname = sys.argv[2] if len(sys.argv) > 2 else "transcript_a2_batch.pdf"
case = sys.argv[3] if len(sys.argv) > 3 else "CB-2026-0104"
c = httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=60)
H = {"X-Role": "admissions", "X-Actor": "Presenter"}


def wait(job_id: str) -> None:
    while c.get(f"/jobs/{job_id}").json()["status"] not in ("DONE", "FAILED", "INTERRUPTED"):
        time.sleep(0.2)


print("cases with a prepared run:", [(x["case_number"], x["filename"]) for x in c.get("/demo/cases").json()["cases"]])
t0 = time.time()
r = c.post("/applications", files={"file": (fname, (V3 / fname).read_bytes(), "application/pdf")}, headers=H, data={"demo": "1", "demo_speed": speed, "case_number": case})
if r.status_code != 200:
    sys.exit(f"{r.status_code}: {r.text}")
app = r.json()
wait(app["job_id"])
t_table = time.time() - t0
wait(c.post(f"/applications/{app['id']}/confirm", headers=H).json()["job_id"])
t_eval = time.time() - t0
rows = {x["title"]: x["id"] for x in c.get(f"/applications/{app['id']}").json()["rows"]}
states = c.get(f"/applications/{app['id']}/results").json()["states"]
summary = {k: (states[v]["status"], (states[v].get("classification") or {}).get("value")) for k, v in rows.items() if v in states}
if "AP Calculus AB" in rows:  # submitting the missing exam score resumes only this row (code, no model)
    wait(c.post(f"/applications/{app['id']}/rows/{rows['AP Calculus AB']}/evidence", data={"facts": '{"exam_score": 5}'}, headers=H).json()["job_id"])
print("confirmed in bulk:", c.post(f"/applications/{app['id']}/confirm-mapped", headers=H).json())
chk = c.post(f"/applications/{app['id']}/export-check", headers=H).json()
if chk.get("job_id"):
    wait(chk["job_id"])
sheet = c.get(f"/applications/{app['id']}/sheet").json()
use = c.get(f"/applications/{app['id']}/activity").json()["usage"]
print(f"{speed}: table ready {t_table:.1f}s, evaluated {t_eval:.1f}s | sheet ok={sheet['ok']} auditor clean={sheet['audit']['clean']} | live model calls={use['live_calls']} (must be 0), recorded={use['recorded_calls']}, tool calls={use['tool_calls']}")
print(json.dumps(summary, indent=1))
