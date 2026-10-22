from backend.app.domain.schemas import MatchOutput
from backend.app.services.curriculum import Subject, qualification_hint, score
from backend.app.services.extraction import normalize, parse_line


def test_parse_digital_rows_in_different_layouts():
    a = parse_line("AP Precalculus                              5     2025")
    assert a["title"] == "AP Precalculus" and a["grade"] == "5" and a["term"] == "2025" and a["code"] == ""
    b = parse_line("0625   Physics                         IGCSE       A*      June 2024")
    assert b["code"] == "0625" and b["grade"] == "A*" and b["term"] == "June 2024" and "Physics" in b["title"]
    c = parse_line("COMP101  Introduction to Programming       3        A-     Fall 2024")
    assert c["code"] == "COMP101" and c["credits"] == "3" and c["grade"] == "A-"


def test_headers_and_prose_are_not_rows():
    assert parse_line("Code   Subject   Level   Grade   Session") is None
    assert parse_line("Example International School - Academic Record") is None


def test_arabic_digits_normalised():
    assert normalize("٣") == "3" and parse_line("COMP101  Intro to Programming  ٣  A-")["credits"] == "3"


def test_code_match_beats_name_match_and_hints_narrow_qualification():
    phys_ig = Subject("1", "Physics", "0625", "Cambridge IGCSE", "IGCSE", "Cambridge")
    phys_a = Subject("2", "Physics", "9702", "Cambridge International AS & A Level", "A Level", "Cambridge")
    assert score("Physics IGCSE", "0625", phys_ig) == 1.0 > score("Physics IGCSE", "0625", phys_a)
    assert qualification_hint("Physics IGCSE") == {"Cambridge IGCSE"}
    assert qualification_hint("AP Precalculus") == {"Advanced Placement"}
    assert qualification_hint("Physics") is None


def test_agent_output_ignores_harmless_extras_but_rejects_approval_keys():
    assert MatchOutput.model_validate({"recommendation": "NO_DEFENSIBLE_MATCH", "explanation": "x"}).recommendation == "NO_DEFENSIBLE_MATCH"
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MatchOutput.model_validate({"recommendation": "POTENTIAL_MATCH", "approved": True})
