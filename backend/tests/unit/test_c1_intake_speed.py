"""C1 step 1, intake speed: fewer and fuller blocks (cut at the widest white gap), and the agent starts with the page listing in its first message.
No model: layout is code, the agent runs on the fake provider."""
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw

from backend.app.agents.intake_agent import ADDENDUM, PROMPT, IntakeContext, page_listing, run_intake_agent
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import grade_checks as gc
from backend.app.services import vision_reader as vr
from backend.app.workers import pipeline  # noqa: F401  (imports the whole app so the settings exist)

ROOT = Path(__file__).resolve().parents[3]
V3 = ROOT / "fixtures" / "demo_v3"
DPI, MAX_H = 200, 560
TEXT_LAYER_FILES = ["transcript_a2_batch.pdf", "transcript_a_live_demo.pdf", "transcript_c_copy_error.pdf"]


def text_lines(path: Path, page: int = 0) -> list[tuple[float, float, str]]:
    """(top, bottom, text) in pixels at the reading dpi, one entry per printed line."""
    out = []
    for b in pymupdf.open(path)[page].get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                out.append((ln["bbox"][1] * DPI / 72, ln["bbox"][3] * DPI / 72, t))
    return out


def blocks_of(path: Path) -> list[tuple[int, int]]:
    return [sp for sp, _xr, _o in vr.layout(vr.render_pages(str(path), DPI)[0], MAX_H)]


def text_in(path: Path, span: tuple[int, int]) -> list[str]:
    return [t for a, b, t in text_lines(path) if a >= span[0] - 2 and b <= span[1] + 2]


def test_the_clean_transcript_is_cut_into_whole_terms_not_into_four_slices():
    path = V3 / "transcript_a2_batch.pdf"
    spans = blocks_of(path)
    assert len(spans) == 3                                      # was 4: the first course row sat in the "header" block, so it could not be skipped
    first, second, last = (" | ".join(text_in(path, sp)) for sp in spans)
    assert "AP Calculus AB" in first and "Moral Education" in first and "Spring 2026" not in first   # header + the whole Fall term
    assert "AP Computer Science A" in second and "AP Human Geography" in second and "Fall 2025" not in second   # the whole Spring term
    assert "Grading scale" in last and "AP " not in last        # the only block with no course row


@pytest.mark.parametrize("name", TEXT_LAYER_FILES)
def test_no_block_boundary_cuts_through_a_printed_line_and_no_block_is_taller_than_the_limit(name):
    path = V3 / name
    img = vr.render_pages(str(path), DPI)[0]
    spans = blocks_of(path)
    ink = (np.asarray(img.convert("L")) < 140).sum(axis=1)
    assert all(b - a <= MAX_H for a, b in spans)
    assert all(ink[b] == 0 for _a, b in spans[:-1])             # a cut is a white pixel row, so it never runs through printed text
    for top, bottom, text in text_lines(path):
        centre = (top + bottom) / 2
        assert sum(1 for a, b in spans if a <= centre <= b) == 1, f"'{text[:30]}' is not in exactly one block"


def uniform_table(rows: int, gap_every: int = 0) -> Image.Image:
    img = Image.new("RGB", (900, 60 + rows * 40 + (rows // gap_every if gap_every else 0) * 40), "white")
    d = ImageDraw.Draw(img)
    y = 40
    for i in range(rows):
        if gap_every and i and i % gap_every == 0:
            y += 40                                             # a heading gap: wider than the gap between two rows
        d.rectangle([40, y, 700, y + 18], fill="black")
        y += 40
    return img


def test_a_long_uniform_table_is_filled_up_to_the_limit_and_cut_only_in_white_rows():
    img = uniform_table(30)
    spans = vr.split_blocks(img, MAX_H)
    ink = (np.asarray(img.convert("L")) < 140).sum(axis=1)
    assert all(b - a <= MAX_H for a, b in spans)
    assert len(spans) == -(-(img.height - 40) // MAX_H) or len(spans) == -(-(img.height - 40) // MAX_H) + 1   # about height / limit, not twice that
    for _a, cut in spans[:-1]:
        assert ink[cut] == 0                                    # cut in a white row, never through a course row


def test_the_widest_gap_near_the_limit_wins_over_an_earlier_narrow_one():
    img = uniform_table(24, gap_every=8)                        # a double gap before rows 8 and 16
    spans = vr.split_blocks(img, MAX_H)
    ink = (np.asarray(img.convert("L")) < 140).sum(axis=1)
    heading_gaps = []
    y = 40
    for i in range(24):
        if i and i % 8 == 0:
            heading_gaps.append((y, y + 40))                    # white run of about 40 + 22 rows
            y += 40
        y += 40
    assert any(a <= spans[0][1] <= b + 22 for a, b in heading_gaps)   # the first cut is inside a wide heading gap
    assert all(ink[cut] == 0 for _a, cut in spans[:-1])


def test_a_white_gap_does_not_use_up_the_height_limit_of_the_next_block():
    img = Image.new("RGB", (900, 900), "white")
    d = ImageDraw.Draw(img)
    for y in list(range(30, 200, 25)) + list(range(560, 760, 25)):   # a cluster, a 360 px gap, a cluster
        d.rectangle([40, y, 600, y + 10], fill="black")
    spans = vr.split_blocks(img, 400)
    assert len(spans) == 2 and spans[1][1] - spans[1][0] < 260       # the second cluster is one block that starts at its own first row


def make_ctx(tmp_path, script):
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    for y in list(range(30, 200, 25)) + list(range(420, 600, 25)):
        d.rectangle([40, y, 600, y + 10], fill="black")
    path = str(tmp_path / "t.png")
    img.save(path)
    spans = {f"p1b{i + 1}": (1, sp) for i, sp in enumerate(vr.split_blocks(img, 300))}
    return IntakeContext(None, None, "intake", "", app_id="APP-T", provider=FakeLLMProvider(script), path=path, scale=gc.load_scale(), spans=spans,
                         lines={k: vr.count_text_lines(img, sp) for k, (_p, sp) in spans.items()})


@pytest.mark.asyncio
async def test_the_first_message_carries_the_listing_so_the_agent_needs_no_list_pages_turn(tmp_path):
    rows = [{"subject": "AP Chemistry", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0}]
    script = [[ToolCall("a", "read_block", {"targets": ["p1b1", "p1b2"]})], [ToolCall("v", "submit_block", {"rows": rows})], [ToolCall("v", "submit_block", {"rows": rows})],
              [ToolCall("a", "submit_courses", {"blocks_read": ["p1b1", "p1b2"], "note": "done"})]]
    ctx = make_ctx(tmp_path, script)
    await run_intake_agent(ctx)
    first = next(m["content"] for m in ctx.provider.requests[0].messages if m["role"] == "user")
    brief = json.loads(first)
    assert [b["key"] for pg in brief["listing"] for b in pg["blocks"]] == ["p1b1", "p1b2"]
    assert brief["listing"][0]["blocks"][0]["status"] == "unread"
    assert page_listing(ctx)["pages"][0]["blocks"][0]["status"] == "read"         # the tool still gives the live status
    assert "Do not call list_pages first" in PROMPT and ADDENDUM in PROMPT
    assert "Do not call flag_cell with kind check_failure" in PROMPT
