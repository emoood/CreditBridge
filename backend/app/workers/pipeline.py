"""The two background phases. Phase 1 (reading) ends at the officer gate. Phase 2 (evaluation) runs only after confirmation.
Every unit is idempotent: re-running one overwrites only its own result and can never create or change a human decision."""
import asyncio
import time
import uuid
from typing import Any

from ..agents.auditor_agent import run_auditor
from ..agents.case_agent import run_case_agent
from ..agents.intake_agent import run_intake_agent
from ..agents.orchestrator import is_school_specific, no_evidence_case, run_case, school_specific_case
from ..config import get_settings
from ..db.models import Application, Job
from ..db.session import app_session
from ..providers.base import LLMProvider
from ..providers.guard import CallTimeout
from ..runtime import rt
from ..services import extraction_checks as xc
from ..services import grade_checks as gc
from ..services import mapping as mp
from ..services import recording, usage
from ..services import vision_reader as vr
from ..services.curriculum import curriculum
from ..services.extraction import extract_transcript
from ..services.linking import autolink
from . import store
from .results import case_result, mapped_result, serialize

NOT_DONE = ("PENDING", "RUNNING", "FAILED_RETRYABLE")


def _err_code(e: Exception) -> str:
    return "timeout" if isinstance(e, CallTimeout) else e.code if isinstance(e, vr.VisionError) else "model_error"


# --------------------------------------------------------------------------------------------- PHASE 1: READING
async def run_reading(job_id: str, provider: LLMProvider) -> None:
    s = get_settings()
    R = rt()
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        job.status, job.started_at, job.route, job.model = "RUNNING", store.now(), getattr(provider, "name", ""), getattr(provider, "model", "")
        app.status = "READING"
        db.commit()
        reset = not job.summary.get("resumed")
        path = app.document["path"]
        scale = gc.load_scale()
        store.audit(db, app.id, "system", "reading_started", job.id, resumed=not reset)

        # ---- stage render / parse
        job.stage = "render"
        db.commit()
        digital = path.lower().endswith(".pdf") and await asyncio.to_thread(_has_text_layer, path)
        blocks_meta: list[dict[str, Any]] = []
        merged: dict[str, Any]
        plant_cfg: dict[str, Any] | None = None
        planted_log: list[dict[str, Any]] = []
        if digital:
            (u,) = store.plan_units(db, job, "parse", ["digital"], reset)
            if u.status != "DONE":
                store.start_unit(db, job, u, "digital_pdf")
                res = await asyncio.to_thread(extract_transcript, path, app.id)
                rows = [{"subject": r.title, "code": r.code, "mark": None, "letter": r.grade, "points": None,
                         "credits": float(r.credits) if r.credits is not None else None, "term": r.term, "page": r.page,
                         "flags": [], "uncertain": r.uncertain_fields} for r in res.rows]
                store.finish_unit(db, u, "DONE", {"rows": rows, "flags": res.warnings, "totals": {}, "reread": False, "scale": res.scale})
            merged = gc_merge([u.result])
            merged["printed_scale"] = (u.result or {}).get("scale")
            method = "digital_pdf"
        else:
            method = "vision"
            pages = await asyncio.to_thread(vr.render_pages, path, s.vision_dpi)
            spans = [(pi + 1, bi + 1, sp) for pi, img in enumerate(pages) for bi, sp in enumerate(vr.split_blocks(img, s.vision_max_block_px))]
            keys = [f"p{p}b{b}" for p, b, _ in spans]
            units = store.plan_units(db, job, "read", keys, reset)
            hi_pages: dict[int, Any] = {}
            plant_cfg = (app.extraction or {}).get("planted_misread")
            for (pg, _bi, span), u in zip(spans, units, strict=True):
                if u.status == "DONE":
                    continue
                store.start_unit(db, job, u, "vision")
                store.audit(db, app.id, "system", "stage:read", u.unit_key, status="RUNNING")
                best: dict[str, Any] | None = None
                err = ""
                for attempt in (1, 2):  # 2nd attempt = one re-read at higher resolution
                    u.attempts = attempt
                    try:
                        if attempt == 1:
                            b64 = vr.crop_png_b64(pages[pg - 1], span)
                        else:
                            if pg not in hi_pages:
                                hi_pages[pg] = (await asyncio.to_thread(vr.render_pages, path, s.vision_reread_dpi))[pg - 1]
                            b64 = vr.crop_png_b64(hi_pages[pg], span, s.vision_reread_dpi / s.vision_dpi)
                        reading = await vr.read_block(provider, b64, u.unit_key)
                        if plant_cfg:  # PLANTED FOR DEMO (both passes of the normal reader, never the Intake agent's focused re-read)
                            rec_p = vr.plant_misread(reading, plant_cfg)
                            if rec_p:
                                if not planted_log:
                                    usage.trace(app.id, "", "code", "planted_for_demo", {**rec_p, "note": "PLANTED FOR DEMO: one correctly read mark was replaced with a wrong value before the extraction checks ran"})
                                planted_log.append(rec_p)
                        checked = vr.check_block(reading, scale)
                        checked["reread"] = attempt == 2
                        if best is None or len(checked["flags"]) < len(best["flags"]):
                            best = checked
                        err = ""
                        if not vr.needs_reread(best):
                            break
                    except Exception as e:  # noqa: BLE001  timeout, bad output or model error: try once more, then give up on this block
                        err = _err_code(e)
                if best is None:
                    store.finish_unit(db, u, "FAILED_RETRYABLE", error=err or "model_error")
                else:
                    best["page"] = pg
                    store.finish_unit(db, u, "DONE", best, method="vision_reread" if best["reread"] else "vision")
                blocks_meta.append({"key": u.unit_key, "status": u.status})
            if s.intake_agent:  # re-read only blocks that still fail a check, a few times per transcript; sheets that pass never reach it
                read_units = [u for u in store.units_of(db, job.id) if u.stage == "read"]
                blocks = {u.unit_key: u.result if (u.status == "DONE" and u.result) else {"rows": [], "flags": [], "totals": {}, "failed": True, "error": u.error_code}
                          for u in read_units}  # a block that failed outright is a failing block too
                if any(xc.failures_in(blocks, k, scale) for k in blocks):
                    job.stage = "recheck"
                    db.commit()
                    used = job.summary.get("rechecks_used", 0)
                    budget = max(0, s.intake_max_rechecks - used)
                    ictx = await run_intake_agent(usage.MeteredProvider(provider, app.id, ""), R.repo, R.retriever, app.id, blocks,
                                                  {f"p{p}b{b}": (p, sp) for p, b, sp in spans}, lambda pg, dpi: vr.render_pages(path, dpi)[pg - 1], scale, budget)
                    for u in read_units:
                        b = blocks.get(u.unit_key)
                        if b is None or b.get("failed"):
                            continue
                        store.set_json(u, "result", b)
                        if u.status != "DONE":  # a block the Intake agent re-read successfully
                            u.status, u.error_code, u.method = "DONE", "", "vision_recheck"
                    job.summary = {**job.summary, "rechecks_used": used + budget - ictx.budget, "intake_changes": ictx.changes}
                    db.commit()
            done = [u.result for u in store.units_of(db, job.id) if u.stage == "read" and u.status == "DONE"]
            merged = gc_merge(done)
            blocks_meta = [{"key": u.unit_key, "status": u.status, "reread": bool(u.result and u.result.get("reread")),
                            "flags": len(u.result["flags"]) if u.result else None, "error": u.error_code}
                           for u in store.units_of(db, job.id) if u.stage == "read"]

        # ---- stage checks
        job.stage = "checks"
        store.audit(db, app.id, "system", "stage:checks", job.id, rows=len(merged["rows"]), flags=len(merged["flags"]))

        # ---- stage curriculum (read-only lookup; unrecognized subjects are flagged, never guessed)
        rows = []
        for r in merged["rows"]:
            grade = r.get("letter") or (f"{r['mark']:g}" if r.get("mark") is not None else "")
            rows.append({"id": uuid.uuid4().hex[:8], "code": r.get("code") or "", "title": r.get("subject") or "", "title_ar": "",
                         "credits": f"{r['credits']:g}" if r.get("credits") is not None else "", "grade": grade,
                         "mark": r.get("mark"), "points": r.get("points"), "term": r.get("term") or "", "page": r.get("page", 1),
                         "evidence_ref": "", "uncertain_fields": r.get("uncertain", []), "flags": r.get("flags", []),
                         "subject": None, "candidates": [], "included": True, "corrected": False,
                         "questions": r.get("questions", [])})
        job.stage = "curriculum"
        db.commit()
        cur_units = store.plan_units(db, job, "curriculum", [r["id"] for r in rows], True)
        await asyncio.to_thread(curriculum.subjects)  # loads once; sets curriculum.error instead of raising
        cur_flags = []
        if curriculum.error:
            cur_flags.append(f"curriculum_unavailable({curriculum.error})")
        for row, u in zip(rows, cur_units, strict=True):
            store.start_unit(db, job, u, "string_match")
            await asyncio.to_thread(autolink, row, app.qualification_hint or None)
            store.finish_unit(db, u, "DONE", {"linked": bool(row["subject"])})
        unrecognized = sum(1 for r in rows if not r["subject"])

        # ---- stop at the gate
        store.set_json(app, "rows", rows)
        store.set_json(app, "extracted_rows", rows)
        failed = [b for b in blocks_meta if b["status"] != "DONE"]
        store.set_json(app, "extraction", {"student_id": (app.extraction or {}).get("student_id", ""), "planted_misread": ({**plant_cfg, "applied": planted_log} if plant_cfg else None), "method": method, "blocks": blocks_meta, "failed_blocks": len(failed), "flags": merged["flags"] + cur_flags,
                                           "cumulative": merged["cumulative"], "scale": merged["scale"], "grading_scale": merged.get("printed_scale") or gc.scale_from_text(merged.get("dropped_text", "")), "unrecognized": unrecognized,
                                           "dropped_non_course_rows": merged.get("dropped_rows", 0),
                                           "rows_found": len(rows)})
        app.status = "EXTRACTION_REVIEW_REQUIRED"
        job.status, job.stage, job.current_unit, job.finished_at = "DONE", "gate", "", store.now()
        job.flags = (["BLOCKS_FAILED"] if failed else []) + (["UNRECOGNIZED_SUBJECTS"] if unrecognized else []) + (["CHECK_FLAGS"] if merged["flags"] else [])
        db.commit()
        store.audit(db, app.id, "system", "extraction_review_required", job.id, rows=len(rows), failed_blocks=len(failed), unrecognized=unrecognized)


def gc_merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    return vr.merge_checked([r for r in results if r], gc.load_scale())


def _has_text_layer(path: str) -> bool:
    import pymupdf
    return sum(len(p.get_text()) for p in pymupdf.open(path)) >= 80


# ------------------------------------------------------------------------------------------ PHASE 2: EVALUATION
async def run_evaluation_legacy(job_id: str, provider: LLMProvider) -> None:
    """The older path: the matcher and reviewer evaluate every subject. Used for replays of recorded runs and when case_agent is off."""
    s = get_settings()
    R = rt()
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        job.status, job.started_at, job.route, job.model = "RUNNING", store.now(), getattr(provider, "name", ""), getattr(provider, "model", "")
        db.commit()
        reset = not job.summary.get("resumed")
        rows = [r for r in app.rows if r.get("included") and r.get("source_id")]
        sids = [r["source_id"] for r in rows]
        by_sid = dict(zip(sids, rows, strict=True))
        units = store.plan_units(db, job, "evaluate", sids, reset)
        replay = (app.extraction or {}).get("replay")  # set when this application was loaded from a recorded run
        rec = recording.load(replay["name"]) if replay else None
        rec_units = {e["row_id"]: e for e in rec["evaluation"]["units"]} if rec else {}
        rec_rows = {r["id"]: r for r in rec["rows"]} if rec else {}
        if rec:
            job.summary = {**job.summary, "replay": replay}
            if not replay["live"]:
                job.route, job.model = "replay", rec["model"]
            db.commit()
        store.audit(db, app.id, "system", "evaluation_started", job.id, subjects=len(sids), resumed=not reset)
        for u in units:
            if u.status == "DONE":
                continue
            row = by_sid[u.unit_key]
            if rec and row["id"] not in replay["live"] and recording.can_replay(rec_units.get(row["id"]), row, rec_rows.get(row["id"])):
                store.start_unit(db, job, u, "replay")  # loaded from the saved run: no model call, labelled in the result
                store.finish_unit(db, u, "DONE", recording.replayed_result(rec, rec_units[row["id"]], row, u.unit_key), method="replay")
                store.audit(db, app.id, "system", "case_replayed", u.unit_key, recorded_at=rec["recorded_at"])
                continue
            store.start_unit(db, job, u, "agents")
            store.audit(db, app.id, "system", "stage:evaluate", u.unit_key, status="RUNNING")
            t0 = time.time()
            try:
                outcomes: list[dict[str, Any]] = []
                if app.kind == "upload" and row.get("subject") and not is_school_specific(row.get("title") or ""):
                    outcomes = await asyncio.to_thread(curriculum.outcomes, row["subject"]["id"])
                if app.kind == "upload":
                    title = row.get("title") or (row["subject"]["name"] if row.get("subject") else "") or "Untitled subject"
                    R.repo.add_source(u.unit_key, title, str(row.get("credits") or ""), row.get("grade") or "", row.get("term") or "",
                                      row.get("title_ar") or "", outcomes, app.id)
                if app.kind == "upload" and is_school_specific(row.get("title") or ""):
                    case = school_specific_case(R.repo, u.unit_key)  # no model call, no mapping: flagged for the officer
                elif app.kind == "upload" and not outcomes and not s.title_only_suggestions:
                    case = no_evidence_case(R.repo, u.unit_key)  # unrecognized: no model call, never guessed
                else:
                    case = await run_case(provider, R.repo, R.retriever, u.unit_key, R.precedent, s.matcher_max_tool_rounds, s.reviewer_max_tool_rounds)
                result = serialize(case, row, time.time() - t0, uuid.uuid4().hex[:12])
                store.finish_unit(db, u, "DONE", result, method="agents" if outcomes or app.kind == "demo" else "no_evidence")
                store.audit(db, app.id, "system", "case_completed", u.unit_key, triage=case.triage.category, llm_calls=case.llm_calls)
            except Exception as e:  # noqa: BLE001  one failed subject never blocks the others
                store.finish_unit(db, u, "FAILED_RETRYABLE", error=_err_code(e))
                store.audit(db, app.id, "system", "case_failed", u.unit_key, error=_err_code(e))

        _finish_evaluation(db, job, app)


def _finish_evaluation(db, job: Job, app: Application) -> None:
    """Triage worklist, built in code, and the final job/application status."""
    job.stage, job.current_unit = "triage", ""
    all_units = [u for u in store.units_of(db, job.id) if u.stage == "evaluate"]
    counts: dict[str, int] = {}
    for u in all_units:
        if u.status == "DONE" and u.result:
            c = u.result["triage"]["category"]
            counts[c] = counts.get(c, 0) + 1
    failed = [u.unit_key for u in all_units if u.status != "DONE"]
    order = sorted((u for u in all_units if u.result), key=lambda u: u.result["triage"]["priority"])
    job.summary = {**job.summary, "triage_counts": counts, "worklist": [u.unit_key for u in order], "failed": failed}
    app.status = "EVALUATION_PARTIAL" if failed else "REVIEW_READY"
    job.status, job.finished_at = "DONE", store.now()
    job.flags = ["SUBJECTS_FAILED"] if failed else []
    db.commit()
    store.audit(db, app.id, "system", "review_ready" if not failed else "evaluation_partial", job.id, counts=counts, failed=len(failed))



# ------------------------------------------------------------------------------- PHASE 2 (default): gate, lookup, Case agent
def _lookup_rows(db, app: Application, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Code only: the relevance gate and the batch mapping lookup for the given rows. Saves each row's state."""
    R = rt()
    maps = mp.lookup_many(db, [r["subject"]["code"] for r in rows if r.get("subject")])
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        old = mp.get_state(db, app.id, r["id"])
        verdict = mp.decide_row(r, maps.get((r.get("subject") or {}).get("code")), (old.facts if old else {}) or {}, bool(old and old.included_override), R.repo,
                                school_specific=is_school_specific(r.get("title") or ""), scale=(app.extraction or {}).get("grading_scale"))
        waiting = old and old.status == mp.WAITING and any(q["status"] in ("DRAFT", "SENT") for q in (old.requests or []))
        if waiting and verdict["status"] in mp.WORKABLE:  # still waiting on the same document: keep the pause
            verdict = {**verdict, "status": mp.WAITING, "reason": old.reason}
        st = mp.save_state(db, app.id, r["id"], status=verdict["status"], kind=verdict["kind"], reason=verdict["reason"], can_include=verdict["can_include"],
                           mapping=verdict["mapping"], condition_result=verdict["condition_result"])
        if verdict["status"] == mp.MAPPED:
            mp.save_state(db, app.id, r["id"], finding=None)
        out[r["id"]] = mp.state_dict(st)
    usage.trace(app.id, "", "code", "lookup", {"rows": len(rows), **{k: sum(1 for v in out.values() if v["status"] == k) for k in (mp.MAPPED, mp.CONDITIONAL, mp.NO_MAPPING, mp.WAITING, mp.NOT_EVAL)}})
    return out


async def _work_unit(db, job: Job, app: Application, row: dict[str, Any], u, provider: LLMProvider, mode: str = "work") -> None:
    R = rt()
    state = mp.state_dict(mp.get_state(db, app.id, row["id"]))
    store.start_unit(db, job, u, "case")
    t0 = time.time()
    try:
        outcomes: list[dict[str, Any]] = []
        if (state["status"] in mp.WORKABLE or mode == "review_package") and row.get("subject"):
            outcomes = await asyncio.to_thread(curriculum.outcomes, row["subject"]["id"])
        title = row.get("title") or (row["subject"]["name"] if row.get("subject") else "") or "Untitled subject"
        R.repo.add_source(u.unit_key, title, str(row.get("credits") or ""), row.get("grade") or "", row.get("term") or "", row.get("title_ar") or "", outcomes, app.id)
        agent = None
        if state["status"] == mp.MAPPED:
            result, method = mapped_result(u.unit_key, row, state, uuid.uuid4().hex[:12]), "mapping"
        elif state["status"] in mp.WORKABLE or (mode == "review_package" and state["status"] == mp.WAITING):
            agent = await run_case_agent(usage.MeteredProvider(provider, app.id, row["id"]), R.repo, R.retriever, R.precedent, app.id, row, u.unit_key, mode)
            db.expire_all()
            state = mp.state_dict(mp.get_state(db, app.id, row["id"]))  # the agent may have moved the row to Waiting on document
            result, method = case_result(u.unit_key, row, state, agent, time.time() - t0, uuid.uuid4().hex[:12]), "case_agent"
        else:
            result, method = case_result(u.unit_key, row, state, None, 0.0, uuid.uuid4().hex[:12]), "waiting"
        store.finish_unit(db, u, "DONE", result, method=method)
        store.audit(db, app.id, "system", "row_worked", u.unit_key, status=state["status"], method=method)
    except Exception as e:  # noqa: BLE001  one failed row never blocks the others
        store.finish_unit(db, u, "FAILED_RETRYABLE", error=_err_code(e))
        store.audit(db, app.id, "system", "case_failed", u.unit_key, error=_err_code(e))


async def run_evaluation(job_id: str, provider: LLMProvider) -> None:
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        replay = (app.extraction or {}).get("replay")
        legacy = not (get_settings().case_agent and app.kind == "upload" and (not replay or replay.get("case_path")))
    if legacy:
        return await run_evaluation_legacy(job_id, provider)
    if replay:
        return await run_evaluation_replay(job_id, provider)
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        job.status, job.started_at, job.route, job.model = "RUNNING", store.now(), getattr(provider, "name", ""), getattr(provider, "model", "")
        job.stage = "lookup"
        db.commit()
        reset = not job.summary.get("resumed")
        rows = [dict(r) for r in app.rows]
        states = _lookup_rows(db, app, rows)
        todo = [r for r in rows if r.get("source_id") and states[r["id"]]["status"] != mp.NOT_EVAL]
        units = store.plan_units(db, job, "evaluate", [r["source_id"] for r in todo], reset)
        sid_row = {r["source_id"]: r for r in todo}
        store.audit(db, app.id, "system", "evaluation_started", job.id, rows=len(rows), to_work=len(todo), resumed=not reset)
        for u in units:
            if u.status != "DONE":
                await _work_unit(db, job, app, sid_row[u.unit_key], u, provider)
        job.stage, job.current_unit = "triage", ""
        _finish_evaluation(db, job, app)


async def run_row(job_id: str, provider: LLMProvider) -> None:
    """Phase "CASE": re-work ONE row after an include or after evidence arrived. Everything else is left alone."""
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        row = next((dict(r) for r in app.rows if r["id"] == job.summary.get("row_id")), None)
        job.status, job.started_at, job.route, job.model = "RUNNING", store.now(), getattr(provider, "name", ""), getattr(provider, "model", "")
        app.status = "EVALUATION_IN_PROGRESS"
        db.commit()
        failed = False
        if row and row.get("source_id"):
            state = _lookup_rows(db, app, [row])[row["id"]]
            if state["status"] != mp.NOT_EVAL:
                u = store.plan_units(db, job, "evaluate", [row["source_id"]], True)[0]
                await _work_unit(db, job, app, row, u, provider, job.summary.get("mode", "work"))
                failed = u.status != "DONE"
        job.status, job.finished_at, job.stage = "DONE", store.now(), "done"
        job.flags = ["ROW_FAILED"] if failed else []
        app.status = "EVALUATION_PARTIAL" if failed else "REVIEW_READY"
        db.commit()
        store.audit(db, app.id, "system", "row_reworked", job.summary.get("row_id", ""), failed=failed)


async def run_audit(job_id: str, provider: LLMProvider) -> None:
    """Phase "AUDIT": the Auditor agent, only because a sheet check failed. Its report is stored for the officer; nothing is applied."""
    R = rt()
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        prev = app.status
        job.status, job.started_at, job.route, job.model = "RUNNING", store.now(), getattr(provider, "name", ""), getattr(provider, "model", "")
        app.status = "EVALUATION_IN_PROGRESS"
        db.commit()
        try:
            report = await run_auditor(usage.MeteredProvider(provider, app.id, ""), R.repo, R.retriever, app.id)
            store.set_json(app, "extraction", {**(app.extraction or {}), "audit": report})
            job.flags = []
        except Exception as e:  # noqa: BLE001
            job.flags = [f"audit_failed:{_err_code(e)}"]
        job.status, job.finished_at, job.stage = "DONE", store.now(), "done"
        app.status = prev
        db.commit()
        store.audit(db, app.id, "system", "audit_finished", job.id, flags=job.flags)


async def run_evaluation_replay(job_id: str, provider: LLMProvider) -> None:
    """Replay of a recorded Case-agent run: no model is called for any row that is not chosen to run live. Row states, officer actions, the trace and the usage
    ledger are restored and marked as recorded; rows chosen live go through the normal lookup and Case agent now and are not marked."""
    from datetime import datetime

    from ..db.models import RowAction, TraceEvent, UsageEntry
    with app_session() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.app_id)
        replay = app.extraction["replay"]
        rec = recording.load(replay["name"])
        live = set(replay["live"])
        job.status, job.started_at, job.stage = "RUNNING", store.now(), "replay"
        job.route, job.model = ("replay", rec["model"]) if not live else ("replay + live", f"{rec['model']} (recorded) / {getattr(provider, 'model', '')} (live)")
        job.summary = {**job.summary, "replay": replay}
        db.commit()
        rows = [dict(r) for r in app.rows]
        rec_rows = {r["id"]: r for r in rec["rows"]}
        rec_units = {e["row_id"]: e for e in rec["evaluation"]["units"]}
        replayed = [r for r in rows if r["id"] not in live and r["id"] in rec["row_states"] and recording.can_replay(rec_units.get(r["id"]) or {"status": "DONE", "result": {}}, r, rec_rows.get(r["id"]))]
        rest = [r for r in rows if r not in replayed]
        keep = {r["id"] for r in replayed}
        for r in replayed:  # restore the recorded state of the row exactly
            s = rec["row_states"][r["id"]]
            mp.save_state(db, app.id, r["id"], status=s["status"], kind=s["kind"], reason=s["reason"], can_include=s["can_include"], included_override=s["included_override"],
                          mapping=s["mapping"], condition_result=s["condition_result"], finding=s["finding"], requests=s["requests"], facts=s["facts"])
        if rest:
            _lookup_rows(db, app, rest)
        for a in rec.get("actions", []):
            if a["row_id"] in keep:
                db.add(RowAction(app_id=app.id, row_id=a["row_id"], action=a["action"], actor=a["actor"], actor_role="admissions", at=datetime.fromisoformat(a["at"]),
                                 original=a["original"], chosen=a["chosen"], reason=a["reason"], batch=a["batch"]))
        for e in rec.get("trace", []):
            if e["row_id"] in keep or e["row_id"] == "":
                db.add(TraceEvent(app_id=app.id, row_id=e["row_id"], at=datetime.fromisoformat(e["at"]), actor=e["actor"], name=e["name"], args={**e["args"], "_recorded": replay["label"]},
                                  ok=e["ok"], detail=e["detail"]))
        for u in rec.get("usage", []):
            if u["row_id"] in keep or u["row_id"] == "":
                db.add(UsageEntry(app_id=app.id, row_id=u["row_id"], at=datetime.fromisoformat(u["at"]), stage=u["stage"], provider="recorded:" + u["provider"], model=u["model"], seconds=u["seconds"],
                                  prompt_tokens=u["prompt_tokens"], output_tokens=u["output_tokens"], cost_usd=u["cost_usd"]))
        db.commit()
        todo = [r for r in rows if r.get("source_id") and mp.get_state(db, app.id, r["id"]).status != mp.NOT_EVAL]
        units = store.plan_units(db, job, "evaluate", [r["source_id"] for r in todo], True)
        by_sid = {r["source_id"]: r for r in todo}
        for u in units:
            row = by_sid[u.unit_key]
            if row["id"] in keep:
                store.start_unit(db, job, u, "replay")
                res = recording.replayed_result(rec, rec_units[row["id"]], row, u.unit_key)
                res["row_state"] = mp.state_dict(mp.get_state(db, app.id, row["id"]))
                store.finish_unit(db, u, "DONE", res, method="replay")
                store.audit(db, app.id, "system", "case_replayed", u.unit_key, recorded_at=rec["recorded_at"])
            else:
                await _work_unit(db, job, app, row, u, provider)
        _finish_evaluation(db, job, app)
