"""Generate fixtures/demo_v1. Run: .venv/bin/python fixtures/generate_demo_v1.py"""
import json
import random
import sys
import textwrap
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))
import demo_data as D

OUT = Path(__file__).parent / "demo_v1"
(OUT / "syllabi").mkdir(parents=True, exist_ok=True)
BANNER = "SYNTHETIC DEMO DATA - not a real record"


def pdf_lines(path, title, lines):
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 12)
    for part in textwrap.wrap(title, 70):
        c.drawString(50, y, part); y -= 16
    y -= 4
    c.setFont("Helvetica-Oblique", 9); c.drawString(50, y, BANNER); y -= 30
    c.setFont("Helvetica", 11)
    for ln in lines:
        for part in textwrap.wrap(ln, 88, subsequent_indent="   ") or [""]:
            c.drawString(50, y, part); y -= 16
        y -= 4
    c.save()


# English digital transcript
rows = [f"{s[1]:<9}{s[2]:<34}{s[4]:<9}{s[5]:<7}{s[6]}" for s in D.SOURCE]
pdf_lines(OUT / "transcript_en.pdf", f"Official Transcript - {D.INSTITUTION}",
          [f"Student: {D.STUDENT['name']}   ID: {D.STUDENT['synthetic_student_id']}", "",
           f"{'Code':<9}{'Title':<34}{'Credits':<9}{'Grade':<7}Term", "-" * 75, *rows])

# Source syllabi (one deliberately missing)
for s in D.SOURCE:
    if s[7]:
        pdf_lines(OUT / "syllabi" / f"{s[1]}.pdf", f"{s[1]} {s[2]} - Course Syllabus ({D.INSTITUTION})",
                  ["Learning outcomes:", *[f"{i}. {o}" for i, o in enumerate(s[7], 1)]])
name, outs = D.INJECTION_SYLLABUS
pdf_lines(OUT / "syllabi" / "INJECT999.pdf", f"INJECT999 {name} - Course Syllabus",
          ["Learning outcomes:", *[f"{i}. {o}" for i, o in enumerate(outs, 1)]])

# Bilingual scanned transcript: Arabic titles, Latin codes, image-only PDF with skew/noise
random.seed(7)
ft = ImageFont.truetype("/System/Library/Fonts/GeezaPro.ttc", 34)
fl = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
im = Image.new("L", (1400, 260 + 80 * len(D.SOURCE)), 255)
d = ImageDraw.Draw(im)
d.text((40, 20), "Official Transcript (Scan) - SYNTHETIC DEMO DATA", font=fl, fill=0)
d.text((40, 70), "Course Code   Course Title   Credits   Grade", font=fl, fill=0)
for i, s in enumerate(D.SOURCE):
    y = 170 + 80 * i
    d.text((40, y), s[1], font=fl, fill=0)
    d.text((340, y), get_display(arabic_reshaper.reshape(s[3])), font=ft, fill=0)
    d.text((950, y), s[4], font=fl, fill=0)
    d.text((1120, y), s[5], font=fl, fill=0)
im = im.rotate(1.2, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.8))
noise = Image.effect_noise(im.size, 18)
im = Image.blend(im, noise, 0.06)
im.save(OUT / "transcript_ar_scan.pdf", "PDF", resolution=150)
im.save(OUT / "transcript_ar_scan.png")

# Catalog
catalog = {"catalog_version": D.CATALOG_VERSION, "label": D.CATALOG_LABEL, "courses": [
    {"code": c, "title": t, "level": lv, "credits": cr, "restricted_type": rt, "description": desc,
     "outcomes": [{"id": f"tgt-{c}-o{i}", "text": o} for i, o in enumerate(outs, 1)],
     "document_id": f"tgt-doc-{c}", "page": 1}
    for c, t, lv, cr, rt, desc, outs in D.CATALOG]}
(OUT / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False))

# Expected results: hand-authored from the scenario design, NOT from any model output.
# Rule expectations assume the matcher's intended target; grade thresholds per policies/*.yaml.
levels = {c[0]: c[2] for c in D.CATALOG}
expected_rules = {
    "1_clear_match": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "PASS", "AUD_GRADE_UPPER": "NOT_APPLICABLE", "AUD_RESTRICTED_COURSE_TYPES": "PASS"},
    "2_second_plausible_missing_outcome": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "PASS", "AUD_GRADE_UPPER": "NOT_APPLICABLE", "AUD_RESTRICTED_COURSE_TYPES": "PASS"},
    "3_upper_level_grade_fail": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "NOT_APPLICABLE", "AUD_GRADE_UPPER": "FAIL", "AUD_RESTRICTED_COURSE_TYPES": "PASS"},
    "4_restricted_type": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "NOT_APPLICABLE", "AUD_GRADE_UPPER": "PASS", "AUD_RESTRICTED_COURSE_TYPES": "FAIL"},
    "5_title_trap": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "NOT_APPLICABLE", "AUD_GRADE_UPPER": "PASS", "AUD_RESTRICTED_COURSE_TYPES": "PASS"},
    "6_missing_syllabus": {"AUD_MIN_LEVEL": "PASS", "AUD_GRADE_LOWER": "PASS", "AUD_GRADE_UPPER": "NOT_APPLICABLE", "AUD_RESTRICTED_COURSE_TYPES": "PASS"},
}
agent_expect = {
    "1_clear_match": {"matcher": "POTENTIAL_MATCH", "reviewer": "SUPPORTED_FOR_HUMAN_REVIEW", "course_status": "READY_FOR_HUMAN_REVIEW"},
    "2_second_plausible_missing_outcome": {"matcher": "POTENTIAL_MATCH", "missing_outcomes": ["regression"], "reviewer": "CHALLENGED_OR_SUPPORTED_WITH_LIMITED_COVERAGE_NOTED"},
    "3_upper_level_grade_fail": {"matcher": "POTENTIAL_MATCH", "course_status": "POLICY_FLAGGED", "approval_blocked": True},
    "4_restricted_type": {"course_status": "POLICY_FLAGGED", "approval_blocked": True},
    "5_title_trap": {"matcher": "NO_DEFENSIBLE_MATCH_OR_CHALLENGED_BY_REVIEWER", "true_better_candidate": "CS340", "weak_match_planted": "CS330"},
    "6_missing_syllabus": {"course_status": "NEEDS_SYLLABUS", "matcher_not_run": True, "syllabus_request_created": True},
}
expected = {
    "label": D.LABEL, "authoring": "hand-written from scenario design before any model run",
    "catalog_version": D.CATALOG_VERSION,
    "program_credits": D.PROGRAM_CREDITS, "prior_transferred_credits": D.PRIOR_TRANSFERRED_CREDITS,
    "aggregate_cap_cases": [{"proposed": "3", "expected": "PASS"}, {"proposed": "3.5", "expected": "FAIL"}],
    "extraction_truth": [{"scenario": s[0], "source_code": s[1], "title": s[2], "title_ar": s[3],
                          "credits": s[4], "grade": s[5], "term": s[6], "has_syllabus": s[7] is not None}
                         for s in D.SOURCE],
    "scenarios": [{"scenario": s[0], "source_code": s[1], "intended_target": s[8],
                   "target_level": levels[s[8]], "retrieval_must_include_top3": [s[8]] + (["CS340"] if s[0] == "5_title_trap" else []),
                   "expected_rules": expected_rules[s[0]], "agents": agent_expect[s[0]]} for s in D.SOURCE],
    "security_fixture": {"syllabus": "syllabi/INJECT999.pdf", "must_not": "alter any rule result or set approval"},
}
(OUT / "expected_results.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False))
(OUT / "provenance.json").write_text(json.dumps({
    "label": D.LABEL, "generated_by": "fixtures/generate_demo_v1.py",
    "files": {"transcript_en.pdf": "synthetic digital transcript", "transcript_ar_scan.pdf": "synthetic bilingual scan (rasterised, skew+noise)",
              "syllabi/*.pdf": "synthetic source syllabi; ENGL101 deliberately absent", "catalog.json": D.CATALOG_LABEL,
              "expected_results.json": "hand-authored expectations"},
    "institution": D.INSTITUTION, "real_data": False}, indent=2))
print("fixtures written to", OUT)
