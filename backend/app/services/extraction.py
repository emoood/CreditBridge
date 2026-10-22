"""Transcript -> course rows. Deterministic parsing for digital PDFs; two-pass Tesseract OCR for scans/images
(English pass for code, credits and grade; Arabic+English pass for titles). Everything uncertain is flagged for human
verification. No LLM is involved. Layout is unknown in advance, so the parser is tolerant and reports what it skipped."""
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf
from PIL import Image

from ..domain.schemas import CourseExtraction

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
BIDI = dict.fromkeys([0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069])
GRADE = re.compile(r"^(A\*|A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|E|F|U|P|[1-9])$")
CODE = re.compile(r"^(?:[A-Z]{1,5}[- ]?\d{2,4}[A-Z]?|\d{4}[A-Z]?)$")
CREDITS = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
TERM = re.compile(r"(?i)\b(fall|spring|summer|winter|autumn|semester|term)\b|\b(19|20)\d{2}\b")
HEADER = re.compile(r"(?i)\b(course|credit|grade|title|subject|code|transcript|student)\b")
ARABIC = re.compile(r"[؀-ۿ][؀-ۿ\s]*[؀-ۿ]|[؀-ۿ]")


@dataclass
class ExtractionResult:
    rows: list[CourseExtraction]
    method: str  # digital_pdf | ocr
    page_count: int
    warnings: list[str] = field(default_factory=list)
    unparsed_lines: list[dict] = field(default_factory=list)  # {page, text}
    lines: list[dict] = field(default_factory=list)  # every raw line kept as evidence: {page, n, text}
    scale: dict | None = None  # the grading scale printed on the transcript, when one was found


def normalize(text: str) -> str:
    return text.translate(ARABIC_DIGITS).translate(BIDI)


def normalize_arabic(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("ى", "ي").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")).strip()


def parse_line(line: str) -> dict | None:
    """Return {code,title,credits,grade,term,uncertain} for a plausible course row, else None."""
    line = normalize(line).strip()
    if re.search(r"(?i)grading scale", line):  # a printed scale is read separately; its letters are not course grades
        return None
    if len(line) < 4 or HEADER.search(line) and not re.search(r"\b[A-F][+-]?\s*$", line):
        return None
    cols = [c.strip() for c in re.split(r"\s{2,}|\t|\|", line) if c.strip()]
    if len(cols) < 3:  # single-spaced text: split on spaces and regroup
        cols = line.split()
    grade_i = next((i for i in range(len(cols) - 1, -1, -1) if GRADE.match(cols[i])), None)
    if grade_i is None:
        return None
    grade = cols[grade_i]
    rest = [c for i, c in enumerate(cols) if i != grade_i]
    term = next((c for c in rest if TERM.search(c)), "")
    rest = [c for c in rest if c != term]
    credits_s = next((c for c in rest if CREDITS.match(c)), "")
    if credits_s:
        rest.remove(credits_s)
    code = ""
    if rest and CODE.match(rest[0]):
        code = rest.pop(0)
    elif len(rest) >= 2 and CODE.match(f"{rest[0]} {rest[1]}"):
        code = f"{rest.pop(0)} {rest.pop(0)}"
    title = " ".join(rest).strip(" -:|")
    if not re.search(r"[A-Za-z؀-ۿ]{3}", title) and not code:
        return None
    return {"code": code, "title": title, "credits": credits_s, "grade": grade, "term": term}


def _to_row(p: dict, page: int, ref: str) -> CourseExtraction:
    unc = []
    credits = None
    if p["credits"]:
        try:
            credits = Decimal(p["credits"])
        except InvalidOperation:
            unc.append("credits")
    else:
        unc.append("credits")
    if not p["title"]:
        unc.append("title")
    return CourseExtraction(code=p["code"], title=p["title"], credits=credits, grade=p["grade"], term=p["term"],
                            page=page, evidence_ref=ref, uncertain_fields=unc)


def _parse_text_pages(pages: list[list[str]], doc_id: str, res: ExtractionResult) -> None:
    for pno, lines in enumerate(pages, 1):
        for n, line in enumerate(lines, 1):
            line = normalize(line)
            if not line.strip():
                continue
            ref = f"tr-{doc_id[:8]}-p{pno}-l{n}"
            res.lines.append({"id": ref, "page": pno, "n": n, "text": line.strip()})
            p = parse_line(line)
            if p:
                res.rows.append(_to_row(p, pno, ref))
            elif re.search(r"[A-Za-z]{3}", line) and re.search(r"\d", line) and not HEADER.search(line):
                res.unparsed_lines.append({"page": pno, "text": line.strip()[:160]})


def _ocr_images(images: list[Image.Image], doc_id: str, res: ExtractionResult) -> None:
    import pytesseract
    try:
        have_ara = "ara" in pytesseract.get_languages()
    except Exception:  # noqa: BLE001
        have_ara = False
    if not have_ara:
        res.warnings.append("Arabic OCR data is not installed; Arabic titles were not read.")
    eng_pages, ara_pages = [], []
    for im in images:
        g = im.convert("L")
        eng_pages.append(normalize(pytesseract.image_to_string(g, lang="eng", config="--psm 6")).splitlines())
        ara_pages.append(normalize(pytesseract.image_to_string(g, lang="ara+eng", config="--psm 6")).splitlines() if have_ara else [])
    _parse_text_pages(eng_pages, doc_id, res)
    # attach Arabic titles from the second pass by code, else by order when line counts agree
    by_page: dict[int, list[CourseExtraction]] = {}
    for r in res.rows:
        by_page.setdefault(r.page, []).append(r)
    for pno, ara_lines in enumerate(ara_pages, 1):
        parsed = [(parse_line(ln), ln) for ln in ara_lines]
        parsed = [(p, ln) for p, ln in parsed if p]
        rows = by_page.get(pno, [])
        for i, row in enumerate(rows):
            match = next((ln for p, ln in parsed if row.code and p["code"] == row.code), None)
            if match is None and len(parsed) == len(rows):
                match = parsed[i][1]
            ar = ARABIC.findall(match or "")
            if ar:
                row.title_ar = normalize_arabic(max(ar, key=len))
                if row.title and not re.search(r"[A-Za-z]{4}", row.title.replace(row.code, "")):
                    row.title = ""
                row.title = row.title if re.match(r"^[A-Za-z0-9 ,&'().:/-]{4,}$", row.title) else ""
                if not row.title:
                    row.uncertain_fields.append("title")
    for r in res.rows:  # OCR is never fully trusted: flag anything unusual, and always the grade for a human glance
        if r.grade and not GRADE.match(r.grade):
            r.uncertain_fields.append("grade")
        if r.credits is not None and not (0 < r.credits <= 12):
            r.uncertain_fields.append("credits")
        r.uncertain_fields = sorted(set(r.uncertain_fields) | {"grade"} if res.method == "ocr" else set(r.uncertain_fields))


def _finalize(res: ExtractionResult) -> ExtractionResult:
    from .grade_checks import scale_from_text
    res.scale = scale_from_text("\n".join(ln["text"] for ln in res.lines))
    if res.rows and all(r.credits is None for r in res.rows):  # e.g. school records have no credits column
        for r in res.rows:
            r.uncertain_fields = [u for u in r.uncertain_fields if u != "credits"]
    return res


def extract_transcript(path: str, doc_id: str) -> ExtractionResult:
    return _finalize(_extract(path, doc_id))


def _extract(path: str, doc_id: str) -> ExtractionResult:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        doc = pymupdf.open(p)
        pages = [pg.get_text().splitlines() for pg in doc]
        if sum(len(x) for pg in pages for x in pg) >= 80:
            res = ExtractionResult([], "digital_pdf", len(doc))
            _parse_text_pages(pages, doc_id, res)
            if not res.rows:
                res.warnings.append("No course rows were recognised. Check the unparsed lines or add rows manually.")
            return res
        images = [Image.open(io.BytesIO(pg.get_pixmap(dpi=200).tobytes("png"))) for pg in doc]
        res = ExtractionResult([], "ocr", len(doc))
    else:
        images = [Image.open(p)]
        res = ExtractionResult([], "ocr", 1)
    res.warnings.append("This file has no text layer, so it was read with OCR. Please check every row.")
    _ocr_images(images, doc_id, res)
    if not res.rows:
        res.warnings.append("No course rows were recognised. Check the image quality or add rows manually.")
    return res
