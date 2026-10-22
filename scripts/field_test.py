"""Field test through the real API, as an officer would use it (upload -> wait -> verify -> correct -> confirm -> evaluate -> review).

    python scripts/field_test.py read data/demo_inputs/demo1.pdf     # upload, wait for the gate, save the table locally
    python scripts/field_test.py evaluate <app_id> [corrections.json]  # apply officer corrections, confirm, evaluate, save results

Row-level data (a real student's transcript) goes ONLY to data/field_test/<name>/ (gitignored). Stdout carries counts,
timings, flags and statuses, never student identity. Timings are printed per phase."""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000/api/v1"
H = {"X-Role": "admissions", "X-Actor": "Field Test Officer"}
c = httpx.Client(base_url=BASE, timeout=120)
T0 = time.time()


def poll(job_id: str, label: str, every: float = 3.0) -> dict:
    last = ""
    while True:
        j = c.get(f"/jobs/{job_id}").json()
        line = f"{j['status']:9} stage={j['stage']:11} unit={j['current_unit']:12} " + " ".join(
            f"{u['key']}:{u['status'][:4]}" for u in j["units"] if u["stage"] in ("read", "evaluate"))
        if line != last:
            print(f"  [{time.time() - T0:7.1f}s] {label}: {line}", flush=True)
            last = line
        if j["status"] in ("DONE", "FAILED", "INTERRUPTED"):
            return j
        time.sleep(every)


def out_dir(app_id: str) -> Path:
    d = ROOT / "data" / "field_test" / app_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read(path: str) -> None:
    p = Path(path)
    t = time.time()
    up = c.post("/applications", files={"file": (p.name, p.read_bytes(), "application/pdf")}, headers=H).json()
    print(f"== upload {p.name}: response in {time.time() - t:.2f}s, app {up['id']}, job {up['job_id']}, status {up['status']}", flush=True)
    j = poll(up["job_id"], "reading")
    app = c.get(f"/applications/{up['id']}").json()
    d = out_dir(up["id"])
    (d / "rows.json").write_text(json.dumps(app["rows"], indent=1, ensure_ascii=False))
    (d / "extraction.json").write_text(json.dumps(app["extraction"], indent=1, ensure_ascii=False))
    (d / "reading_job.json").write_text(json.dumps(j, indent=1, ensure_ascii=False))
    ex = app["extraction"]
    print(f"== reading finished: status={app['status']} job={j['status']} rows={len(app['rows'])} method={ex.get('method')} "
          f"failed_blocks={ex.get('failed_blocks')} unrecognized={ex.get('unrecognized')} dropped_non_course={ex.get('dropped_non_course_rows')}")
    print(f"   reading time: total={j['timings']['total_secs']}s per stage={j['timings']['per_stage_secs']}")
    print("   blocks:", [(b["key"], b["status"], "reread" if b.get("reread") else "", b.get("flags"), b.get("error")) for b in ex.get("blocks", [])])
    print("   cross-check flags:", ex.get("flags") or "none")
    print("   cumulative read from page:", ex.get("cumulative"))
    by_term: dict[str, list] = {}
    for r in app["rows"]:
        by_term.setdefault(r.get("term") or "?", []).append(r)
    for term, rs in by_term.items():
        cr = sum(float(r["credits"] or 0) for r in rs)
        print(f"   {term:16} rows={len(rs):2} credits={cr:g} uncertain={sum(1 for r in rs if r.get('uncertain_fields'))} row_flags={sum(len(r.get('flags') or []) for r in rs)}")
    print(f"   TOTAL rows={len(app['rows'])} credits={sum(float(r['credits'] or 0) for r in app['rows']):g}")
    print(f"   rows saved to {d}/rows.json (local only)")
    print(json.dumps({"app_id": up["id"], "reading_job": up["job_id"], "reading_wall_s": round(time.time() - T0, 1)}))


def evaluate(app_id: str, corrections: str | None) -> None:
    d = out_dir(app_id)
    app = c.get(f"/applications/{app_id}").json()
    n = 0
    if corrections:  # [{"row_title": "...", "field": "grade", "value": "A"}, ...] applied as officer corrections (logged by the app)
        for cor in json.loads(Path(corrections).read_text()):
            row = next((r for r in app["rows"] if (r.get("title") or "").lower() == cor["row_title"].lower()), None)
            if not row:
                print("   correction target not found:", cor["row_title"])
                continue
            r = c.patch(f"/applications/{app_id}/rows/{row['id']}", json={cor["field"]: cor["value"]}, headers=H)
            print(f"   correction {cor['field']} -> {r.status_code}")
            n += 1
    print(f"== evaluate before confirm must be refused: HTTP {c.post(f'/applications/{app_id}/evaluate', headers=H).status_code}")
    t = time.time()
    cf = c.post(f"/applications/{app_id}/confirm", headers=H).json()
    print(f"== confirm ({n} corrections) -> {cf} in {time.time() - t:.2f}s; replay -> {c.post(f'/applications/{app_id}/confirm', headers=H).json()}")
    t1 = time.time()
    ej = poll(cf["job_id"], "evaluation", 5.0)
    (d / "evaluation_job.json").write_text(json.dumps(ej, indent=1, ensure_ascii=False))
    res = c.get(f"/applications/{app_id}/results").json()
    (d / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"== evaluation finished in {time.time() - t1:.1f}s (job total {ej['timings']['total_secs']}s): app status={res['status']} counts={res['counts']} pending={res['pending_units']}")
    for r in res["results"]:
        m = r["match"]
        rv = r["review"] or {}
        print(f"   {r['course']['title'][:34]:34} {r['triage']['category']:15} {m['status']:27} -> {(m['target'] or {}).get('code', '-'):9} "
              f"review={rv.get('status', '-'):26} objections={len(rv.get('objections', []))} titleonly={len(m.get('title_only_suggestions', []))} "
              f"{r['llm_calls']:>2} calls {r['duration_s']:>6}s flags={m['flags']}")
    print(json.dumps({"app_id": app_id, "evaluation_job": cf["job_id"], "evaluation_wall_s": round(time.time() - t1, 1)}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "read":
        read(sys.argv[2])
    elif cmd == "evaluate":
        evaluate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
