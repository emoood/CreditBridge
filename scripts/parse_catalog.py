"""Parse the AUD Undergraduate Catalog course glossary into structured, traceable records. No LLM involved.

Format handled (deterministic regex, same as the original team script):
    CODE | TITLE (lecture-lab-credits)
    Prerequisite(s): <codes or none> | <frequency legend>
    <description lines...>

Additions over the original: description capture (across line and page breaks), running header/footer removal,
derived fields (level, department, restricted type, description sentences as citable excerpts), JSON output in the
shape the backend loads, a manifest, and validation counts. Anything that looked like a course header but did not
fully match goes to review_needed.csv instead of being dropped.

Usage:
    python scripts/parse_catalog.py data/aud_catalog/raw/Undergraduate_Catalog_2026-2027.pdf \
        --year 2026-2027 --url <source url> --out catalog/aud_undergrad_2026_2027
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

HEADER = re.compile(
    r"([A-Z]{2,4}\s?\d{3}(?:-\d{3})?(?:/[A-Z]{2,4}\s?\d{3})?)\s*\|\s*"
    r"([A-Za-z0-9 ,'’‘\"“”&/.\-:()]+?)\s*(?:\|\s*)?"
    r"\(\s*(?:[IVX]+\s+)?(\d+)(?:\s*-\s*(\d+)\s*-\s*(\d+))?\s*\)")  # tolerates a catalog typo like "(II 0-12-6)"
HSTART = re.compile(r"^[A-Z]{2,4}\s?\d{3}(?:-\d{3})?(?:/[A-Z]{2,4}\s?\d{3})?\s*\|")  # a line that starts like a course header
CANDIDATE = re.compile(r"[A-Z]{2,4}\s?\d{3}.*\|.*\(\d")
REQ = re.compile(r"(Pre|Co)requisites?\s*:\s*(.*)")
RESTRICTED = [("capstone", r"\bcapstone\b"), ("internship", r"\binternship\b"), ("senior_project", r"\bsenior (project|thesis)\b"),
              ("portfolio", r"\bportfolio\b"), ("study_tour", r"\bstudy (tour|abroad)\b")]


def read_lines(pdf_path: str) -> list[tuple[int, str]]:
    """All text lines with page numbers; running headers/footers (lines repeated on many pages) and page numbers removed."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            pages.append([(n, ln.strip()) for ln in (page.extract_text() or "").split("\n") if ln.strip()])
    freq = Counter(ln for pg in pages for _, ln in {x for x in pg})
    noisy = {ln for ln, c in freq.items() if c >= max(8, len(pages) // 4) and not HEADER.search(ln) and not REQ.match(ln)}
    out = []
    for pg in pages:
        for n, ln in pg:
            if ln in noisy or re.fullmatch(r"(page\s*)?\d{1,3}", ln, re.IGNORECASE):
                continue
            out.append((n, ln))
    print(f"read {len(pages)} pages, {len(out)} content lines, removed running lines: {sorted(noisy)[:4]}", file=sys.stderr)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if len(s.strip()) > 3]


def parse(lines: list[tuple[int, str]]):
    entries, review = [], []
    i = 0
    while i < len(lines):
        page, line = lines[i]
        m = HEADER.search(line)
        if not m and HSTART.match(line):  # title wrapped onto the next line(s): join and retry
            for extra in (1, 2):
                if i + extra < len(lines):
                    joined = " ".join(x[1] for x in lines[i:i + extra + 1])
                    m = HEADER.search(joined)
                    if m:
                        line, i = joined, i + extra
                        break
        if not m:
            if HSTART.match(line) or (CANDIDATE.search(line) and not REQ.match(line)):
                review.append({"source_page": page, "raw_line": line, "problem": "header-like line did not match"})
            i += 1
            continue
        code, title, a, b, c = m.groups()
        roman = re.search(r"\(\s*([IVX]+)\s+\d", line)  # catalog typo "(II 0-12-6)": the numeral belongs to the title
        if roman:
            title = f"{title.strip()} {roman.group(1)}"
        entry = {"code": re.sub(r"\s+", " ", code.strip()), "title": title.strip(), "credits": c if c else a,
                 "lecture_hours": a if c else "", "lab_hours": b if c else "", "prerequisites": "", "frequency": "",
                 "description": "", "source_page": page, "raw_line": line}
        i += 1
        # requisite lines: "Prerequisite(s): ... | S, SI" and/or "Corequisite(s): ..." (may wrap before the '|' legend).
        # Courses with no requisite line at all simply have no requirements.
        entry["corequisites"] = ""
        while i < len(lines) and REQ.match(lines[i][1]):
            kind, txt = REQ.match(lines[i][1]).groups()
            i += 1
            for _ in range(3):
                if "|" in txt or i >= len(lines) or HEADER.search(lines[i][1]) or HSTART.match(lines[i][1]) or REQ.match(lines[i][1]):
                    break
                txt += " " + lines[i][1]
                i += 1
            if "|" in txt:
                txt, freq = txt.rsplit("|", 1)
                entry["frequency"] = freq.strip()
            entry["prerequisites" if kind == "Pre" else "corequisites"] = txt.strip()
        desc = []
        while i < len(lines) and not HEADER.search(lines[i][1]) and not HSTART.match(lines[i][1]):
            desc.append(lines[i][1])
            i += 1
        entry["description"] = re.sub(r"\s+", " ", " ".join(desc)).strip()
        # a wrapped frequency legend ("F, S,\nSI") can leak into the description: move it back to the frequency field
        leak = re.match(r"^((?:Upon demand|(?:F|S|SII|SI)\b)(?:,?\s*(?:and\s+)?(?:Upon demand|(?:F|S|SII|SI)\b))*)\s+(?=[A-Z])", entry["description"])
        if leak:
            tag = leak.group(1)
            entry["frequency"] = (entry["frequency"].rstrip(", ") + (", " if entry["frequency"] and not entry["frequency"].endswith("and") else " ") + tag).strip() if entry["frequency"] else tag
            entry["description"] = entry["description"][leak.end():]
        if not entry["description"]:
            review.append({"source_page": page, "raw_line": line, "problem": "empty description"})
        entries.append(entry)
    return entries, review


def derive(e: dict) -> dict:
    primary = e["code"].split("/")[0]
    num = int(re.search(r"(\d{3})", primary).group(1))
    text = f"{e['title']} {e['description']}".lower()
    title = e["title"].lower()
    basis = [name for name, pat in RESTRICTED if re.search(pat, title)]
    if "portfolio" in basis and re.search(r"\b(investment|management|risk|financial|asset)\b", title):
        basis.remove("portfolio")  # finance "portfolio" courses are ordinary courses
    mention = [name for name, pat in RESTRICTED if re.search(pat, text) and name not in basis]
    e["department"] = primary.split()[0]
    e["level"] = (num // 100) * 100
    e["restricted_type"] = basis[0] if basis else ""
    e["restricted_type_basis"] = "title keyword (UNVERIFIED)" if basis else ("mentioned in description only (not flagged)" if mention else "")
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--year", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = read_lines(a.pdf)
    entries, review = parse(lines)
    entries = [derive(e) for e in entries]
    seen = Counter(e["code"] for e in entries)
    dups = {k: v for k, v in seen.items() if v > 1}

    cols = ["code", "title", "credits", "lecture_hours", "lab_hours", "prerequisites", "corequisites", "frequency", "description", "department",
            "level", "restricted_type", "restricted_type_basis", "source_page", "raw_line"]
    with open(out / "courses.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows([{k: e.get(k, "") for k in cols} for e in entries])
    with open(out / "review_needed.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source_page", "raw_line", "problem"])
        w.writeheader()
        w.writerows(review)

    courses = []
    for e in entries:
        sents = sentences(e["description"])
        courses.append({"code": e["code"], "title": e["title"], "level": e["level"], "credits": int(e["credits"]),
                        "restricted_type": e["restricted_type"] or None, "restricted_type_verified": False,
                        "prerequisites": e["prerequisites"], "corequisites": e["corequisites"], "frequency": e["frequency"], "department": e["department"],
                        "description": e["description"], "outcomes": [],  # the catalog has NO learning outcomes
                        "excerpts": [{"id": f"aud-{e['code'].replace(' ', '').replace('/', '_')}-d{n}", "text": s}
                                     for n, s in enumerate(sents, 1)],
                        "document_id": f"aud-catalog-{a.year}", "page": e["source_page"]})
    sha = hashlib.sha256(Path(a.pdf).read_bytes()).hexdigest()
    label = f"AUD Undergraduate Catalog {a.year} (official public catalog; descriptions only, no learning outcomes)"
    (out / "courses.json").write_text(json.dumps({"catalog_version": f"aud-undergrad-{a.year}", "label": label, "source_type":
                                                  "OFFICIAL_PUBLIC_URL", "courses": courses}, indent=1, ensure_ascii=False))
    credits = Counter(e["credits"] for e in entries)
    manifest = {"catalog_version": f"aud-undergrad-{a.year}", "source_file": Path(a.pdf).name, "sha256": sha, "source_url": a.url,
                "retrieved": str(date.today()), "parser": "scripts/parse_catalog.py (regex, deterministic, no LLM)",
                "pages": len({e["source_page"] for e in entries}), "courses": len(entries), "review_needed": len(review),
                "duplicate_codes": dups, "credits_distribution": dict(sorted(credits.items())),
                "levels": dict(sorted(Counter(e["level"] for e in entries).items())),
                "restricted_by_title": [e["code"] for e in entries if e["restricted_type"]],
                "notes": ["No learning outcomes in the source; descriptions are the only content evidence.",
                          "restricted_type is inferred from titles and is UNVERIFIED.",
                          "Verify by sampling rows against the PDF pages (source_page, raw_line)."]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in ("courses", "review_needed", "duplicate_codes", "credits_distribution", "levels")}, indent=1))
    print(f"wrote {out}/ courses.csv courses.json review_needed.csv manifest.json", file=sys.stderr)


if __name__ == "__main__":
    main()
