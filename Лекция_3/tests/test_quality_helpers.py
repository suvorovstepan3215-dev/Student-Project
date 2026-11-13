"""Офлайн-тесты helper'ов качества выдачи: типы публикации, DOI, сортировка, фильтр."""

from pathlib import Path

import europepmc_agent
import utils
from tests.helpers import ep_record, item
from validator import relevance_match


def test_to_item_parses_pub_type_list_variants():
    assert europepmc_agent._to_item(ep_record("1", pubTypeList={"pubType": ["Review", "Journal Article"]}))[
        "article_types"
    ] == ["Review", "Journal Article"]
    assert europepmc_agent._to_item(ep_record("1", pubTypeList=["Editorial"]))["article_types"] == ["Editorial"]
    assert europepmc_agent._to_item(ep_record("1", pubTypeList="Letter"))["article_types"] == ["Letter"]
    assert europepmc_agent._to_item(ep_record("1", pubTypeList={"pubType": 5}))["article_types"] == []
    assert europepmc_agent._to_item(ep_record("1", pubTypeList={"pubType": ["Review", "Review", ""]}))[
        "article_types"
    ] == ["Review"]
    assert europepmc_agent._to_item(ep_record("1"))["article_types"] == []


def test_normalize_doi():
    assert utils.normalize_doi("https://doi.org/10.1234/ABC.") == "10.1234/abc"
    assert utils.normalize_doi("http://dx.doi.org/10.5/XYZ/") == "10.5/xyz"
    assert utils.normalize_doi("DOI: 10.2/x;") == "10.2/x"
    assert utils.normalize_doi(" 10.2/plain ") == "10.2/plain"
    assert utils.normalize_doi("") == ""
    assert utils.normalize_doi(None) == ""


def test_duplicate_doi_map_groups_across_sources():
    items = [
        item("europepmc:1", doi="10.1/ABC"),
        item("openalex:1", source="openalex", doi="https://doi.org/10.1/abc"),
        item("openalex:2", source="openalex", doi="10.1/other"),
        item("minzdrav:kr1", source="minzdrav", doi=""),
    ]
    assert utils.duplicate_doi_map(items) == {"10.1/abc": ["europepmc:1", "openalex:1"]}


def test_sort_items_by_cited_and_date():
    rows = [
        item("a", cited_by_count=5, published="2024-03-01"),
        item("b", cited_by_count=50, published="2020-01-01"),
        item("c", cited_by_count=0, published="2026-11-12"),
    ]
    assert [i["identifier"] for i in utils.sort_items(rows, "cited")] == ["b", "a", "c"]
    assert [i["identifier"] for i in utils.sort_items(rows, "date")] == ["c", "a", "b"]
    assert [i["identifier"] for i in utils.sort_items(rows, "nonsense")] == ["c", "a", "b"]


def test_filter_by_min_citations_keeps_missing_metric():
    rows = [
        item("x", cited_by_count=3),
        item("y", cited_by_count=30),
        item("z"),  # Минздрав-подобная карточка без метрики — не прячем
    ]
    assert [i["identifier"] for i in utils.filter_by_min_citations(rows, 10)] == ["y", "z"]
    assert [i["identifier"] for i in utils.filter_by_min_citations(rows, 0)] == ["x", "y", "z"]
    assert utils.filter_by_min_citations(rows, "bad") == rows


def test_relevance_match_returns_topic_or_empty():
    assert relevance_match(item("x", title="Insulin resistance in adults"), ["Metformin", "Insulin"]) == "Insulin"
    assert relevance_match(item("y", title="Cats", abstract="Nothing here"), ["Insulin"]) == ""
    assert relevance_match(item("z", title="", abstract=""), ["Insulin"]) == ""


def test_ui_has_sort_and_citation_filter():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30)
    app.session_state["results"] = [item("europepmc:1", cited_by_count=42, doi="10.1/ABC")]
    app.session_state["diagnostics"] = {}
    app.run()
    assert not app.exception
    assert any(s.key == "results_sort" for s in app.selectbox)
    assert any(s.label == "Минимум цитирований" for s in app.slider)
