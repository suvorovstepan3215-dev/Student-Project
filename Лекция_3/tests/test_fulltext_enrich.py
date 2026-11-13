"""Экспериментальная догрузка аннотации (fulltext_enrich). Главный поиск не обязан её знать."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import fulltext_enrich
from tests.helpers import item

PROJECT = Path(__file__).resolve().parents[1]

FULLTEXT_XML = b"""<?xml version="1.0"?>
<!DOCTYPE article [<!ENTITY internal "never-expanded">]>
<article xmlns:jats="https://jats.nlm.nih.gov">
  <front><article-meta>
    <abstract><p>Diabetes and glycaemic control: abstract from full text.</p></abstract>
  </article-meta></front>
</article>
"""


def test_extract_abstract_handles_namespaced_jats():
    assert fulltext_enrich.extract_abstract(FULLTEXT_XML.decode()) == (
        "Diabetes and glycaemic control: abstract from full text."
    )


def test_extract_abstract_does_not_expand_doctype_entities():
    xml = '<!DOCTYPE article [<!ENTITY boom "x">]><article><abstract><p>Diabetes &amp; care</p></abstract></article>'
    assert fulltext_enrich.extract_abstract(xml) == "Diabetes & care"


def test_extract_abstract_missing_returns_empty():
    assert fulltext_enrich.extract_abstract("<article><front></front></article>") == ""
    assert fulltext_enrich.extract_abstract("") == ""
    assert fulltext_enrich.extract_abstract("<not-xml") == ""


def test_enrich_replaces_abstract_and_resummarizes(monkeypatch):
    monkeypatch.setattr(
        fulltext_enrich,
        "fetch_abstract_for_record",
        lambda **kwargs: "Diabetes needs careful study. More evidence is required for treatment regimens.",
    )
    items = [item("europepmc:9", abstract="", pmid="9", pmcid="PMC9")]
    out, stats = fulltext_enrich.enrich_results(items, max_sentences=2)
    assert stats == {"attempted": 1, "enriched": 1, "failed": 0, "skipped_limit": 0}
    assert out[0]["abstract_origin"] == "fulltext_xml"
    assert out[0]["summary_input"] == "fulltext_xml"
    assert out[0]["summary"]
    # Оригинальная карточка не мутирована.
    assert items[0]["abstract"] == ""


def test_enrich_network_failure_keeps_item_untouched(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("Synthetic outage")

    monkeypatch.setattr(fulltext_enrich, "fetch_abstract_for_record", boom)
    items = [item("europepmc:9", abstract="", pmcid="PMC9")]
    out, stats = fulltext_enrich.enrich_results(items)
    assert out[0]["abstract"] == ""
    assert "summary" not in out[0]
    assert out[0]["identifier"] == "europepmc:9"
    assert stats == {"attempted": 1, "enriched": 0, "failed": 1, "skipped_limit": 0}


def test_enrich_respects_max_fetch(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return "Diabetes needs careful study. More evidence is required."

    monkeypatch.setattr(fulltext_enrich, "fetch_abstract_for_record", fake)
    items = [item(f"europepmc:{n}", abstract="", pmcid=f"PMC{n}") for n in range(12)]
    out, stats = fulltext_enrich.enrich_results(items, max_fetch=10)
    assert len(calls) == 10
    assert stats["attempted"] == 10 and stats["skipped_limit"] == 2


def test_enrich_skips_items_that_do_not_need_it(monkeypatch):
    monkeypatch.setattr(
        fulltext_enrich,
        "fetch_abstract_for_record",
        lambda **kwargs: pytest.fail("fetch must not be called"),
    )
    items = [
        item("europepmc:1", abstract="Already here."),  # аннотация есть
        item("openalex:1", source="openalex", abstract="", pmcid="PMC1"),  # другой источник
        item("europepmc:2", abstract=""),  # нет идентификаторов для fullTextXML
    ]
    out, stats = fulltext_enrich.enrich_results(items)
    assert stats == {"attempted": 0, "enriched": 0, "failed": 0, "skipped_limit": 0}
    assert [card["identifier"] for card in out] == [card["identifier"] for card in items]


def test_ui_has_experimental_checkbox_off_by_default():
    app = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=30).run()
    assert not app.exception
    checkbox = next(c for c in app.checkbox if c.key == "exp_fulltext_fallback")
    assert checkbox.value is False
