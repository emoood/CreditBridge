from backend.app.services import vision_reader as vr
from backend.app.services.grade_checks import load_scale

SCALE = load_scale()


def row(subject, mark, letter, points, credits):
    return {"subject": subject, "mark": mark, "letter": letter, "points": points, "credits": credits, "flags": [], "uncertain": []}


def block(rows, **totals):
    return {"rows": rows, "flags": [], "totals": totals}


def test_non_course_rows_are_dropped_and_their_flags_removed():
    legend = {"subject": "Grading scale:", "mark": None, "letter": None, "points": None, "credits": None, "flags": [], "uncertain": []}
    b = {"rows": [row("AP Calculus AB", 94, "A", 4.0, 1.0), legend], "flags": ["Grading scale:: no_grade_read"], "totals": {}}
    m = vr.merge_checked([b], SCALE)
    assert [r["subject"] for r in m["rows"]] == ["AP Calculus AB"] and m["dropped_rows"] == 1 and m["flags"] == []


def test_term_total_split_across_blocks_is_checked_against_all_its_rows_without_false_flags():
    b1 = block([row("A", 94, "A", 4.0, 1.0), row("B", 88, "B+", 3.3, 1.0)])
    b2 = block([row("C", 95, "A", 4.0, 0.5)], term_credits=2.5, term_quality_points=9.3, term_gpa=3.72)  # 4.0 + 3.3 + 2.0 = 9.3; 9.3 / 2.5 = 3.72
    assert vr.merge_checked([b1, b2], SCALE)["flags"] == []


def test_a_missing_row_is_caught_by_the_printed_totals():
    b1 = block([row("A", 94, "A", 4.0, 1.0)])  # the second row was never read
    b2 = block([], term_credits=2.0, term_quality_points=7.3, term_gpa=3.65)
    flags = vr.merge_checked([b1, b2], SCALE)["flags"]
    assert any("term_credits_mismatch" in f for f in flags) and any("term_points_mismatch" in f for f in flags)


def test_cumulative_totals_catch_a_missing_row_too():
    b = block([row("A", 94, "A", 4.0, 1.0)], cumulative_credits=1.5, cumulative_quality_points=6.0, cumulative_gpa=4.0)
    assert any("cumulative_credits_mismatch" in f for f in vr.merge_checked([b], SCALE)["flags"])


def test_split_blocks_never_returns_more_than_max_height_and_skips_blank_pages():
    from PIL import Image
    blank = Image.new("RGB", (800, 1200), "white")
    assert vr.split_blocks(blank, 560) == []
    img = Image.new("RGB", (800, 1500), "white")
    for y in range(100, 1400, 40):  # rows of ink
        for x in range(50, 700):
            img.putpixel((x, y), (0, 0, 0))
    spans = vr.split_blocks(img, 560)
    assert len(spans) >= 3 and all(b - a <= 560 for a, b in spans) and spans[0][0] < 120


def test_a_reply_that_is_a_list_of_terms_with_letter_marks_is_normalised_not_dropped():
    from backend.app.services.vision_reader import BlockReading, normalize_reading
    reply = [{"term": "Fall", "rows": [{"subject": "AP Biology", "mark": "A-", "credits": "1.0"}], "term_credits": None},
             {"term": "Spring", "rows": [{"subject": "AP Statistics", "mark": "85", "credits": 1.0}], "term_credits": 3.0}]
    b = BlockReading.model_validate(normalize_reading(reply))
    assert [r.subject for r in b.rows] == ["AP Biology", "AP Statistics"] and b.rows[0].mark is None and b.rows[0].letter == "A-" and b.rows[1].mark == 85 and b.term_credits == 3.0


def test_side_by_side_tables_become_separate_blocks_and_a_one_column_page_is_untouched():
    from PIL import Image, ImageDraw

    from backend.app.services import vision_reader as vr
    img = Image.new("RGB", (1600, 1200), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([60, 40, 1540, 50], fill="black")                      # a full-width title
    for y in range(160, 900, 14):
        d.rectangle([60, y, 760, y + 8], fill="black")
        d.rectangle([840, y, 1540, y + 8], fill="black")              # two tables with a gutter between them
    d.rectangle([60, 1000, 1540, 1010], fill="black")
    lay = vr.layout(img, 560)
    assert len({x for _sp, x, _o in lay if x}) == 2 and any(x is None for _sp, x, _o in lay)   # a left and a right table, plus the full-width title and footer
    one = Image.new("RGB", (1600, 800), "white")
    ImageDraw.Draw(one).rectangle([60, 100, 1540, 700], fill="black")
    assert all(x is None for _sp, x, _o in vr.layout(one, 560))
