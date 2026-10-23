"""Cross-check the hand-written expected results against the deterministic rules engine."""
import json
from pathlib import Path

import pymupdf

from backend.app.services import policy_rules as pr

FX = Path(__file__).resolve().parents[3] / "fixtures" / "demo_v1"
EXP = json.loads((FX / "expected_results.json").read_text(encoding="utf-8"))
CAT = {c["code"]: c for c in json.loads((FX / "catalog.json").read_text(encoding="utf-8"))["courses"]}
P = pr.Policy.load()


def test_catalog_labelled_and_sized():
    cat = json.loads((FX / "catalog.json").read_text(encoding="utf-8"))
    assert "not official" in cat["label"]
    assert 8 <= len(cat["courses"]) <= 12
    assert all(c["outcomes"] for c in cat["courses"])


def test_six_scenarios_and_one_missing_syllabus():
    assert len(EXP["scenarios"]) == 6
    missing = [e for e in EXP["extraction_truth"] if not e["has_syllabus"]]
    assert [m["source_code"] for m in missing] == ["ENGL101"]
    assert not (FX / "syllabi" / "ENGL101.pdf").exists()


def test_english_transcript_contains_every_course_row():
    text = pymupdf.open(FX / "transcript_en.pdf")[0].get_text()
    for e in EXP["extraction_truth"]:
        assert e["source_code"] in text and e["title"] in text


def test_expected_rule_outcomes_match_rules_engine():
    truth = {e["source_code"]: e for e in EXP["extraction_truth"]}
    for sc in EXP["scenarios"]:
        t = CAT[sc["intended_target"]]
        facts = pr.CourseFacts(truth[sc["source_code"]]["grade"], t["level"], t["restricted_type"], True)
        got = {r.rule_id: r.status for r in pr.evaluate_course(P, facts)}
        for rule, status in sc["expected_rules"].items():
            assert got[rule] == status, (sc["scenario"], rule)
