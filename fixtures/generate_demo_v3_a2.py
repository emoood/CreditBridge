"""Transcript A2: the same courses and grades as Transcript A, for a DIFFERENT invented student (Rania Haddad, SYN-1004).
It is the rehearsal and batch copy of the live-demo transcript; Transcript A itself is never processed before the demo and is not touched here."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_demo_v3 import A_TERMS, OUT, STUDENTS, text_pdf

path = OUT / "transcript_a2_batch.pdf"
truth = text_pdf(path, "A2", A_TERMS)
exp = json.loads((OUT / "expected.json").read_text())
exp["A2_batch"] = {**truth, "file": path.name, "text_layer": True, "same_courses_as": "transcript_a_live_demo.pdf",
                   "expected_statuses": exp["A_live_demo"]["expected_statuses"]}
(OUT / "expected.json").write_text(json.dumps(exp, indent=1))
(OUT / "students.json").write_text(json.dumps({k: {"name": v[0], "student_id": v[1]} for k, v in STUDENTS.items()}, indent=1))
print("wrote", path)
