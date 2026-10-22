"""Generate fixtures/demo_v2: a SYNTHETIC school transcript rendered as an image-only PDF/PNG (no text layer), so it is
read by the vision model. Prints marks, letters, grade points, credits, per-term and overall totals and GPA."""
import json
import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent / "demo_v2"
OUT.mkdir(exist_ok=True)
SCALE = yaml.safe_load((Path(__file__).parents[1] / "policies" / "school_grade_scales.yaml").read_text())["scales"]["synthetic_us_4_0"]["bands"]


def grade(mark):
    for lo, letter, pts in SCALE:
        if mark >= lo:
            return letter, pts


TERMS = [("Fall 2025 - Grade 11", [("AP Calculus AB", 94, 1.0), ("AP English Language and Composition", 88, 1.0),
                                    ("AP Psychology", 95, 0.5), ("Moral Education", 90, 0.5)]),
         ("Spring 2026 - Grade 11", [("AP Computer Science A", 91, 1.0), ("AP Statistics", 85, 1.0), ("AP Art History", 82, 0.5)])]


def render(path_stem: str, planted_error: bool = False):
    random.seed(11)
    W, H = 1654, 2339
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    F = lambda s, bold=False: ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", s, index=1 if bold else 0)
    d.text((100, 80), "EXAMPLE INTERNATIONAL SCHOOL", font=F(44, True), fill=0)
    d.text((100, 140), "Official Academic Transcript  -  SYNTHETIC DEMO DATA, not a real record", font=F(26), fill=0)
    d.text((100, 200), "Student: SYNTHETIC STUDENT     ID: SYN-0002     Programme: American High School Diploma", font=F(28), fill=0)
    cols = [100, 760, 920, 1060, 1230, 1400]
    y = 300
    all_pts = all_cr = 0.0
    truth = {"terms": [], "planted_error": None}
    for term, rows in TERMS:
        d.text((100, y), term, font=F(34, True), fill=0); y += 60
        for x, h in zip(cols, ["Subject", "Mark", "Grade", "Points", "Credits", ""]):
            d.text((x, y), h, font=F(26, True), fill=0)
        y += 44
        d.line([(100, y), (1550, y)], fill=0, width=2); y += 14
        t_rows, t_cr, t_pts = [], 0.0, 0.0
        for name, mark, cr in rows:
            letter, pts = grade(mark)
            shown = letter
            if planted_error and name == "AP Statistics":
                shown = "B+"  # printed letter does not match the mark 85 -> expected "B"
                truth["planted_error"] = {"subject": name, "mark": mark, "printed_letter": shown, "expected_letter": letter}
            for x, v in zip(cols, [name, str(mark), shown, f"{pts:.1f}", f"{cr:.1f}"]):
                d.text((x, y), v, font=F(28), fill=0)
            t_rows.append({"subject": name, "mark": mark, "letter": letter, "points": pts, "credits": cr})
            t_cr += cr; t_pts += pts * cr; y += 52
        d.line([(100, y), (1550, y)], fill=0, width=1); y += 12
        d.text((100, y), f"Term credits: {t_cr:.1f}    Term quality points: {t_pts:.2f}    Term GPA: {t_pts / t_cr:.2f}", font=F(26), fill=0)
        y += 90
        all_cr += t_cr; all_pts += t_pts
        truth["terms"].append({"term": term, "rows": t_rows, "credits": t_cr, "quality_points": round(t_pts, 2), "gpa": round(t_pts / t_cr, 2)})
    d.text((100, y), f"CUMULATIVE   Credits: {all_cr:.1f}    Quality points: {all_pts:.2f}    GPA: {all_pts / all_cr:.2f}", font=F(32, True), fill=0)
    truth["cumulative"] = {"credits": all_cr, "quality_points": round(all_pts, 2), "gpa": round(all_pts / all_cr, 2)}
    d.text((100, 2200), "Grading scale: A 93-100 = 4.0 | A- 90-92 = 3.7 | B+ 87-89 = 3.3 | B 83-86 = 3.0 | B- 80-82 = 2.7 | C+ 77-79 = 2.3 | C 73-76 = 2.0", font=F(22), fill=0)
    d.text((100, 2250), "This document is synthetic and was generated for software demonstration.", font=F(22), fill=0)
    im = im.rotate(0.6, expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    im = Image.blend(im, Image.effect_noise(im.size, 12), 0.04)
    im.save(OUT / f"{path_stem}.png")
    im.convert("RGB").save(OUT / f"{path_stem}.pdf", "PDF", resolution=200)
    return truth


clean = render("school_transcript")
bad = render("school_transcript_planted_error", planted_error=True)
(OUT / "expected.json").write_text(json.dumps({
    "label": "SYNTHETIC DEMO DATA - hand-derived from the generator's data, not from any model output",
    "grade_scale": "policies/school_grade_scales.yaml synthetic_us_4_0",
    "school_transcript": clean, "school_transcript_planted_error": bad,
    "curriculum_expectations": {"AP Calculus AB": "AP-CALC-AB", "AP English Language and Composition": "AP-ENG-LANG", "AP Psychology": "AP-PSYC",
                                "AP Computer Science A": "AP-CSA", "AP Statistics": "AP-STAT", "AP Art History": "AP-ART-HIST",
                                "Moral Education": "NOT IN CURRICULUM DB -> unrecognized / NEEDS_SYLLABUS"}}, indent=1))
(OUT / "provenance.json").write_text(json.dumps({"synthetic": True, "generator": "fixtures/generate_demo_v2.py",
                                                  "files": ["school_transcript.pdf/png", "school_transcript_planted_error.pdf/png"]}, indent=1))
print("wrote", OUT, clean["cumulative"])
