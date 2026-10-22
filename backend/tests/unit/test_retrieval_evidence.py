from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import DemoRepo

repo = DemoRepo()


def test_syllabus_ingested_with_ids_and_hashes():
    sc = repo.sources["COMP101"]
    assert len(sc.excerpt_ids) == 5
    ex = repo.store.get(sc.excerpt_ids[0])
    assert ex.page == 1 and ex.text.startswith("Write Python") and len(ex.excerpt_hash) == 64
    assert repo.sources["ENGL101"].syllabus_doc_id is None


def test_citation_validation_rejects_fabricated_and_foreign_ids():
    s = repo.store
    sc = repo.sources["COMP101"]
    valid, invalid = s.validate_citations([sc.excerpt_ids[0], "src-FAKE-p9-e9", "tgt-CS101-o1"], {sc.syllabus_doc_id})
    assert valid == [sc.excerpt_ids[0]] and invalid == ["src-FAKE-p9-e9", "tgt-CS101-o1"]
    assert not s.verify_hash(sc.excerpt_ids[0], "tampered text")


def test_wrapped_injection_outcome_is_one_excerpt_and_just_data():
    from backend.app.services.demo_repo import FIXTURES
    from backend.app.services.evidence import EvidenceStore, ingest_syllabus_pdf
    exs = ingest_syllabus_pdf(EvidenceStore(), FIXTURES / "syllabi" / "INJECT999.pdf", "d", "inj")
    assert len(exs) == 2 and "IGNORE ALL PREVIOUS" in exs[1].text and "policy rule." in exs[1].text


def test_retriever_caps_top_k_and_finds_intended_targets():
    r = CatalogRetriever(list(repo.targets.values()))
    assert len(r.search("programming", top_k=10)) <= 3
    assert r.search("   ") == []
    import json

    from backend.app.services.demo_repo import FIXTURES
    exp = json.loads((FIXTURES / "expected_results.json").read_text())
    for sc in exp["scenarios"]:
        src = repo.sources[sc["source_code"]]
        if not src.syllabus_doc_id:
            continue
        q = src.title + " " + " ".join(repo.store.get(i).text for i in src.excerpt_ids)
        got = [c.course_id for c in r.search(q)]
        for must in sc["retrieval_must_include_top3"]:
            assert must in got, (sc["scenario"], got)


def test_school_subject_ranking_prefers_the_introductory_course_with_matching_title():
    cat = [{"code": "PSYC 210", "title": "ABNORMAL PSYCHOLOGY", "level": 200, "description": "psychology theories psychological",
            "outcomes": []},
           {"code": "PSYC 201", "title": "GENERAL PSYCHOLOGY", "level": 200, "description": "psychology theories psychological", "outcomes": []},
           {"code": "PSYC 450", "title": "ADVANCED PSYCHOLOGY SEMINAR", "level": 400, "description": "psychological theories disorders", "outcomes": []}]
    r = CatalogRetriever(cat)
    plain = [c.course_id for c in r.search("psychology disorders theories psychological")]
    boosted = [c.course_id for c in r.search("psychology disorders theories psychological", subject_title="AP Psychology")]
    assert plain[0] == "PSYC 210" and boosted[0] == "PSYC 201"  # a tie on content: the school-subject boost prefers the general intro course
