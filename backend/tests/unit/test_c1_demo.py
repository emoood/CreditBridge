"""C1 step 4, Replay / Live AI: a prepared run carries the version of the data and of the page reading it was made with, and is refused ("Recording out of date") when either
has changed. Names are demo_ap_<student>_<variant>. No model is called: recordings here are hand-made, and the API is only asked to accept or refuse an upload."""
import json
from pathlib import Path

import pytest

from backend.app.services import prepared
from backend.app.services import vision_reader as vr
from backend.tests.conftest import ADMIN, FIX

ROOT = Path(__file__).resolve().parents[3]


def fresh_run(name="demo_ap_test_clean", **over):
    """A recording stamped with the versions of the code as it is now."""
    return {"version": 1, "name": name, "case_number": "CB-2026-0101", "recorded_at": "2026-10-23T00:00:00+00:00", "model": "test-model", "notes": "", "label": "Prepared run, test",
            "source": {"filename": "school_transcript.png", "sha256": "x"}, "planted": {"misread": False, "copy_error": False}, "links": {}, "model_calls": 0, "model_log": [],
            "data_version": prepared.data_version(), "reading_version": prepared.reading_version(), **over}


# ------------------------------------------------------------------------------------------------------------------------ the versions
def test_the_data_version_follows_the_content_of_policies_data_and_ignores_line_endings(tmp_path, monkeypatch):
    monkeypatch.setattr(prepared, "DATA_DIR", tmp_path)
    (tmp_path / "programs").mkdir()
    (tmp_path / "mapping_ap.csv").write_bytes(b"a,b\n1,2\n")
    (tmp_path / "programs" / "plan.yaml").write_bytes(b"x: 1\n")
    v = prepared.data_version()
    (tmp_path / "mapping_ap.csv").write_bytes(b"a,b\r\n1,2\r\n")                           # the same file checked out with Windows line endings
    assert prepared.data_version() == v
    (tmp_path / "mapping_ap.csv").write_bytes(b"a,b\n1,3\n")                                 # a changed mapping
    assert prepared.data_version() != v
    (tmp_path / "mapping_ap.csv").write_bytes(b"a,b\n1,2\n")
    assert prepared.data_version() == v
    (tmp_path / "programs" / "plan.yaml").write_bytes(b"x: 2\n")                              # a changed plan file, in a sub-folder
    assert prepared.data_version() != v
    (tmp_path / "programs" / "plan.yaml").write_bytes(b"x: 1\n")
    (tmp_path / "programs" / "new_plan.yaml").write_bytes(b"y: 1\n")                          # a plan file added
    assert prepared.data_version() != v


def test_the_shipped_data_has_a_stable_version_and_the_reading_version_names_the_prompt_and_the_layout():
    assert prepared.data_version() == prepared.data_version() and len(prepared.data_version()) == 16
    from backend.app.agents.intake_agent import PROMPT_VERSION
    assert prepared.reading_version() == f"{PROMPT_VERSION}+layout{vr.LAYOUT_VERSION}"


# ------------------------------------------------------------------------------------------------------------------ refusing an old recording
def test_a_recording_is_refused_when_the_data_or_the_reading_changed_or_it_was_never_stamped(monkeypatch):
    rec = fresh_run()
    assert prepared.stale_reason(rec) is None and prepared.refusal(rec) is None
    prepared.DemoProvider(rec)                                                              # a current recording plays
    monkeypatch.setattr(prepared, "data_version", lambda: "0123456789abcdef")
    assert "policies/data changed" in prepared.stale_reason(rec)
    with pytest.raises(prepared.RecordingOutOfDate, match="Recording out of date"):
        prepared.DemoProvider(rec)
    monkeypatch.undo()
    monkeypatch.setattr(vr, "LAYOUT_VERSION", vr.LAYOUT_VERSION + 1)
    assert "page reading" in prepared.stale_reason(rec)
    monkeypatch.undo()
    old = {k: v for k, v in fresh_run().items() if k not in ("data_version", "reading_version")}   # recorded before versions existed
    assert "before data versions were tracked" in prepared.stale_reason(old)
    with pytest.raises(prepared.NoPreparedRun):                                              # it is a kind of "no prepared run": existing handlers treat it the same
        prepared.DemoProvider(old)
    assert "Re-record it, or switch to Live AI" in prepared.refusal(old)


@pytest.mark.asyncio
async def test_an_upload_in_replay_is_refused_with_the_reason_when_the_recording_is_out_of_date(env, monkeypatch, tmp_path):
    monkeypatch.setattr(prepared, "DIR", tmp_path)
    (tmp_path / "demo_ap_test_clean.json").write_text(json.dumps(fresh_run(data_version="stale-data")))
    data = (FIX / "school_transcript.png").read_bytes()
    r = await env.client.post("/api/v1/applications", files={"file": ("school_transcript.png", data, "image/png")}, headers=ADMIN, data={"demo": "1", "demo_run": "demo_ap_test_clean"})
    assert r.status_code == 409 and r.json()["detail"].startswith("Recording out of date: the mapping, condition or plan files in policies/data changed")
    (tmp_path / "demo_ap_test_clean.json").write_text(json.dumps(fresh_run()))              # the same recording, stamped with the current versions
    ok = await env.client.post("/api/v1/applications", files={"file": ("school_transcript.png", data, "image/png")}, headers=ADMIN, data={"demo": "1", "demo_run": "demo_ap_test_clean"})
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_live_ai_is_never_affected_by_an_out_of_date_recording(env, monkeypatch, tmp_path):
    monkeypatch.setattr(prepared, "DIR", tmp_path)
    (tmp_path / "demo_ap_test_clean.json").write_text(json.dumps(fresh_run(data_version="stale-data")))
    data = (FIX / "school_transcript.png").read_bytes()
    r = await env.client.post("/api/v1/applications", files={"file": ("school_transcript.png", data, "image/png")}, headers=ADMIN)   # no demo field: Live AI
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_the_status_list_says_which_recordings_can_be_replayed_and_which_names_follow_the_rule(env, monkeypatch, tmp_path):
    monkeypatch.setattr(prepared, "DIR", tmp_path)
    (tmp_path / "demo_ap_test_clean.json").write_text(json.dumps(fresh_run()))
    (tmp_path / "demo_ap_test_misread.json").write_text(json.dumps(fresh_run("demo_ap_test_misread", planted={"misread": True, "copy_error": False}, reading_version="intake_v3+layout1")))
    (tmp_path / "t_run.json").write_text(json.dumps(fresh_run("t_run")))
    got = {x["name"]: x for x in (await env.client.get("/api/v1/demo/recordings")).json()}
    assert got["demo_ap_test_clean"] | {"reason": None} == {"name": "demo_ap_test_clean", "variant": "clean", "case_number": "CB-2026-0101", "current": True, "reason": None, "name_ok": True}
    assert got["demo_ap_test_misread"]["current"] is False and "page reading" in got["demo_ap_test_misread"]["reason"] and got["demo_ap_test_misread"]["variant"] == "misread"
    assert got["t_run"]["name_ok"] is False


# ------------------------------------------------------------------------------------------------------------------------------ names
def test_run_names_are_demo_ap_student_variant():
    assert prepared.run_name("Rania Haddad", "clean") == "demo_ap_rania_clean"
    assert prepared.run_name("Rania Haddad", "copy_error") == "demo_ap_rania_copy_error"
    assert prepared.run_name("Daniel Ferreira", "misread") == "demo_ap_daniel_misread"
    for bad in (("", "clean"), ("Rania", "a2c"), ("Rania", "copy error")):
        with pytest.raises(ValueError, match="demo_ap_<student>"):
            prepared.run_name(*bad)
    assert all(prepared.NAME_RE.match(prepared.run_name("Rania", v)) for v in prepared.VARIANTS)
    assert not prepared.NAME_RE.match("demo_a2") and not prepared.NAME_RE.match("demo_ap_rania") and not prepared.NAME_RE.match("demo_ap_rania_clean2")


def test_every_shipped_prepared_run_follows_the_naming_rule_and_its_file_name_is_its_name():
    files = sorted((ROOT / "fixtures" / "prepared_runs").glob("*.json"))
    assert files
    for f in files:
        rec = json.loads(f.read_text())
        assert rec["name"] == f.stem and prepared.NAME_RE.match(rec["name"]), f.name
        assert rec["name"].rsplit("_", 1)[-1] == prepared.variant_of(rec) or rec["name"].endswith("copy_error") and prepared.variant_of(rec) == "copy_error"


def test_the_overnight_script_records_under_the_rule(monkeypatch):
    text = (ROOT / "scripts" / "overnight_runs.py").read_text()
    import re
    names = [n for n in re.findall(r'"(demo_[a-z0-9_]+)"', text) if n != "demo_v3"]   # demo_v3 is the fixtures folder, not a recording
    assert names and all(prepared.NAME_RE.match(n) for n in names), names
