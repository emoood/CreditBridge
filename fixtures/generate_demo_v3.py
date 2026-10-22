"""Generate fixtures/demo_v3: three SYNTHETIC transcripts for invented students (no real record, no real person).

A  transcript_a_live_demo.pdf    text layer. A clean AP transcript: most rows map; the two exceptions are AP Calculus AB (conditional on an exam score)
                                 and AP Computer Science A (stale mapping). The live demo case: this file must never have been uploaded before the demo.
B  transcript_b_intake.pdf/png   IMAGE ONLY (no text layer). One year block in which one mark is hard to read (a smudge), so a check can fail and the
                                 Intake recheck has real work.
C  transcript_c_copy_error.pdf   text layer. An ordinary, clean transcript for the copy-error demo (the planted-for-demo switch changes one confirmed
                                 AUD equivalent in the output stage; the sheet checks and the Auditor must catch it).
Mapping codes used by the demo are PLACEHOLDER data (policies/dummy_mappings.yaml)."""
import json
import random
from pathlib import Path

import pymupdf
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent / "demo_v3"
OUT.mkdir(exist_ok=True)
BANDS = yaml.safe_load((Path(__file__).parents[1] / "policies" / "school_grade_scales.yaml").read_text())["scales"]["synthetic_us_4_0"]["bands"]
SCALE_LINE = "Grading scale: A 93-100 = 4.0 | A- 90-92 = 3.7 | B+ 87-89 = 3.3 | B 83-86 = 3.0 | B- 80-82 = 2.7 | C+ 77-79 = 2.3 | C 73-76 = 2.0"
STUDENTS = {"A": ("Maya Okonkwo", "SYN-1001"), "B": ("Daniel Ferreira", "SYN-1002"), "C": ("Sofia Lindqvist", "SYN-1003")}


def band(mark):
    for lo, letter, pts in BANDS:
        if mark >= lo:
            return letter, pts


def text_pdf(path: Path, who: str, terms: list[tuple[str, list[tuple[str, str, float]]]]) -> dict:
    """A text-layer transcript: Subject, Grade, Credits, Term. One line per row (monospace) so the digital parser reads it deterministically."""
    name, sid = STUDENTS[who]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 60

    def line(s, size=9.5, bold=False):
        nonlocal y
        page.insert_text((40, y), s, fontname="cobo" if bold else "cour", fontsize=size)
        y += size + 6

    line("EXAMPLE INTERNATIONAL SCHOOL", 14, True)
    line("Official Academic Transcript - SYNTHETIC DEMO DATA, not a real record", 9)
    line(f"Student: {name}   ID: {sid}   Programme: American High School Diploma", 9)
    y += 8
    truth = {"student": name, "student_id": sid, "rows": [], "scale_line": SCALE_LINE}
    for term, rows in terms:
        line(term, 11, True)
        line(f"{'Subject':<40}  {'Grade':<4}  {'Credits':<4}  Term", 9)
        for title, g, cr in rows:
            line(f"{title:<40}  {g:<4}  {cr:<4.1f}  {term}", 9.5)
            truth["rows"].append({"subject": title, "letter": g, "credits": cr, "term": term})
        y += 8
    y = 760
    line(SCALE_LINE, 7.5)
    line("This document is synthetic and was generated for software demonstration.", 7.5)
    doc.save(path)
    return truth


def image_pdf(stem: str, who: str, degrade: bool = False) -> dict:
    """Transcript B: image only. One year block; the Mark cell of AP Chemistry is smeared so it is hard to read (the printed value is 85)."""
    random.seed(23)
    name, sid = STUDENTS[who]
    W, H = 1654, 2339
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    F = lambda s, bold=False: ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", s, index=1 if bold else 0)
    d.text((100, 80), "EXAMPLE INTERNATIONAL SCHOOL", font=F(44, True), fill=0)
    d.text((100, 140), "Official Academic Transcript  -  SYNTHETIC DEMO DATA, not a real record", font=F(26), fill=0)
    d.text((100, 200), f"Student: {name}     ID: {sid}     Programme: American High School Diploma", font=F(28), fill=0)
    rows = [("AP Calculus BC", 92, 1.0), ("AP Physics 2", 88, 1.0), ("AP Chemistry", 85, 1.0), ("AP Macroeconomics", 91, 1.0),
            ("AP Spanish Language", 94, 1.0), ("AP Biology", 89, 1.0), ("AP Statistics", 96, 1.0), ("AP Human Geography", 90, 0.5)]
    cols = [100, 760, 920, 1060, 1230]
    y = 300
    d.text((100, y), "2025-2026 - Grade 12", font=F(34, True), fill=0)
    y += 60
    for x, h in zip(cols, ["Subject", "Mark", "Grade", "Points", "Credits"], strict=True):
        d.text((x, y), h, font=F(26, True), fill=0)
    y += 44
    d.line([(100, y), (1550, y)], fill=0, width=2)
    y += 14
    truth_rows, cr_t, pts_t, smudge_box = [], 0.0, 0.0, None
    for name_, mark, cr in rows:
        letter, pts = band(mark)
        for x, v in zip(cols, [name_, str(mark), letter, f"{pts:.1f}", f"{cr:.1f}"], strict=True):
            d.text((x, y), v, font=F(28), fill=0)
        if name_ == "AP Chemistry":
            smudge_box = (cols[1] - 14, y - 12, cols[2] - 18, y + 46)
        truth_rows.append({"subject": name_, "mark": mark, "letter": letter, "points": pts, "credits": cr})
        cr_t += cr
        pts_t += pts * cr
        y += 52
    d.line([(100, y), (1550, y)], fill=0, width=1)
    y += 12
    d.text((100, y), f"Term credits: {cr_t:.1f}    Term quality points: {pts_t:.2f}    Term GPA: {pts_t / cr_t:.2f}", font=F(26), fill=0)
    d.text((100, y + 70), f"CUMULATIVE   Credits: {cr_t:.1f}    Quality points: {pts_t:.2f}    GPA: {pts_t / cr_t:.2f}", font=F(32, True), fill=0)
    d.text((100, 2200), SCALE_LINE, font=F(22), fill=0)
    d.text((100, 2250), "This document is synthetic and was generated for software demonstration.", font=F(22), fill=0)
    box = smudge_box
    region = im.crop(box).filter(ImageFilter.GaussianBlur(2.1))  # the hard-to-read mark: smeared and streaked, but still partly legible
    im.paste(region, box)
    sd = ImageDraw.Draw(im)
    for _ in range(5):
        x0 = random.randint(box[0], box[2] - 20)
        sd.line([(x0, box[1] + random.randint(0, 20)), (x0 + random.randint(10, 60), box[3] - random.randint(0, 20))], fill=random.randint(150, 205), width=random.randint(2, 4))
    if degrade:  # a worse mark: low resolution (a coarse re-sample) and low contrast, still readable by a person
        reg = im.crop(box)
        reg = reg.resize((max(1, reg.width // 4), max(1, reg.height // 4)), Image.BILINEAR).resize(reg.size, Image.BICUBIC)
        reg = reg.point(lambda v: int(255 - (255 - v) * 0.5))
        im.paste(reg, box)
    im = im.rotate(0.5, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    im = Image.blend(im, Image.effect_noise(im.size, 12), 0.04)
    im.save(OUT / f"{stem}.png")
    im.convert("RGB").save(OUT / f"{stem}.pdf", "PDF", resolution=200)
    return {"student": STUDENTS[who][0], "student_id": STUDENTS[who][1], "term": "2025-2026 - Grade 12", "rows": truth_rows,
            "term_totals": {"credits": cr_t, "quality_points": round(pts_t, 2), "gpa": round(pts_t / cr_t, 2)},
            "hard_to_read": {"row": "AP Chemistry", "cell": "Mark", "printed_value": 85, "expected_failing_checks": ["grading_scale (mark -> letter B)", "term credit/quality-point totals if the smear also hides the credits"]}}


TERM_A1, TERM_A2 = "Fall 2025 (Yr 11)", "Spring 2026 (Yr 11)"
a = text_pdf(OUT / "transcript_a_live_demo.pdf", "A", [
    (TERM_A1, [("AP Calculus AB", "A", 1.0), ("AP Psychology", "A", 1.0), ("AP Biology", "A-", 1.0), ("AP Microeconomics", "A-", 0.5), ("Moral Education", "A", 0.5)]),
    (TERM_A2, [("AP Computer Science A", "A-", 1.0), ("AP Statistics", "B+", 1.0), ("AP Chemistry", "B+", 1.0), ("AP Physics 1", "B", 1.0),
               ("AP Art History", "B", 0.5), ("AP Human Geography", "A", 0.5)])])
b = image_pdf("transcript_b_intake", "B")
c = text_pdf(OUT / "transcript_c_copy_error.pdf", "C", [
    ("Fall 2025 (Yr 12)", [("AP Statistics", "B+", 1.0), ("AP Psychology", "A-", 1.0), ("AP Biology", "B+", 1.0), ("AP Chemistry", "A-", 1.0), ("AP Physics 1", "B", 1.0)]),
    ("Spring 2026 (Yr 12)", [("AP Physics 2", "B+", 1.0), ("AP Microeconomics", "A", 0.5), ("AP Macroeconomics", "A-", 0.5), ("AP Human Geography", "B+", 0.5)])])
(OUT / "expected.json").write_text(json.dumps({
    "label": "SYNTHETIC DEMO DATA: invented students, hand-derived from the generator, not from any model output. Mapping codes are PLACEHOLDER data.",
    "A_live_demo": {**a, "file": "transcript_a_live_demo.pdf", "text_layer": True,
                    "expected_statuses": {"AP Calculus AB": "Conditional (needs an AP exam score of at least 4)", "AP Computer Science A": "Conditional (stale mapping)",
                                          "Moral Education": "Not evaluated (school-specific)", "everything else": "Mapped"}},
    "B_intake": {**b, "file": "transcript_b_intake.pdf / .png", "text_layer": False},
    "C_copy_error": {**c, "file": "transcript_c_copy_error.pdf", "text_layer": True, "demo": "enable the 'planted for demo' switch after confirming; the sheet checks must fail on one row"},
}, indent=1))
(OUT / "students.json").write_text(json.dumps({k: {"name": v[0], "student_id": v[1]} for k, v in STUDENTS.items()}, indent=1))
(OUT / "provenance.json").write_text(json.dumps({"synthetic": True, "generator": "fixtures/generate_demo_v3.py", "never_uploaded": ["transcript_a_live_demo.pdf"]}, indent=1))
print("wrote", OUT)
