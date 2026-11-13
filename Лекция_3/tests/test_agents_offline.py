"""Agent contracts and fixture normalization. Legacy direct entrypoint runs pytest."""

import inspect
from types import SimpleNamespace

import pytest
from defusedxml.common import DefusedXmlException

import archive_agent
import arxiv_agent
import europepmc_agent as ep
import habr_agent
import openalex_agent
import scraper
import validator
from results import SearchResults
from tests.helpers import END, HABR_XML, START, FakeResponse, ep_payload, ep_record, item


@pytest.mark.parametrize("sid,module", list(scraper.SOURCES.items()))
def test_agent_contract(sid, module):
    assert module.SOURCE_ID == sid
    assert module.SOURCE_NAME
    assert list(inspect.signature(module.fetch).parameters) == ["topics", "rows", "date_from", "date_to"]
    for value in [getattr(module, "USER_AGENT", ""), *getattr(module, "HEADERS", {}).values()]:
        value.encode("latin-1")


@pytest.mark.parametrize("module", list(scraper.SOURCES.values()))
@pytest.mark.parametrize("rows", [0, 101, True, 1.5, "10"])
def test_agents_reject_invalid_limits_before_network(module, rows):
    with pytest.raises(ValueError):
        module.fetch(["diabetes"], rows=rows)


def test_europepmc_core_normalization():
    result = ep._to_item(ep_record(pmcid="PMC123"))
    assert result["identifier"] == "europepmc:PMC123"
    assert result["url"] == "https://europepmc.org/articles/PMC123"
    assert result["title"] == "Diabetes: a study"
    assert result["venue"] == "Synthetic journal"
    assert result["keywords"] == "diabetes, review"
    assert result["cited_by_count"] == 2


def test_europepmc_preprint_does_not_disappear_without_doi():
    result = ep._to_item(ep_record("PPR12", pmid="", source="PPR", doi=""))
    assert result["identifier"] == "europepmc:PPR:PPR12"
    assert result["url"] == "https://europepmc.org/article/PPR/PPR12"


def test_europepmc_server_sort_and_cursor_fill(monkeypatch):
    calls = []
    pages = [
        ep_payload([ep_record("", pmid="", firstPublicationDate="")], nextCursorMark="next"),
        ep_payload([ep_record("2"), ep_record("2")]),
    ]

    def get(params):
        calls.append(params)
        return pages.pop(0)

    monkeypatch.setattr(ep, "_api_get", get)
    result = ep.fetch(["diabetes"], 2, START, END)
    assert len(result) == 1
    assert result.warnings
    assert calls[0]["query"].endswith(" sort_date:y")
    assert calls[0]["cursorMark"] == "*" and calls[1]["cursorMark"] == "next"
    assert calls[0]["resultType"] == "core"


def test_europepmc_partial_page_failure(monkeypatch):
    calls = []

    def get(params):
        calls.append(params)
        if len(calls) == 2:
            raise RuntimeError("Synthetic outage")
        return ep_payload([ep_record()], nextCursorMark="next")

    monkeypatch.setattr(ep, "_api_get", get)
    result = ep.fetch(["diabetes"], 2, START, END)
    assert len(result) == 1 and result.errors[0]["code"] == "page_failed"


def test_europepmc_retries_are_bounded(monkeypatch):
    calls, pauses = [], []

    def get(*args, **kwargs):
        calls.append(1)
        return FakeResponse({}, status=500)

    monkeypatch.setattr(ep, "requests", SimpleNamespace(get=get))
    monkeypatch.setattr(ep.time, "sleep", pauses.append)
    with pytest.raises(RuntimeError):
        ep.fetch(["diabetes"], 2, START, END)
    assert len(calls) == 3 and pauses == [2.0, 4.0]


def test_habr_rss_normalization():
    pairs = habr_agent.parse_feed(HABR_XML, START, END, strict=True)
    assert len(pairs) == 1
    record = pairs[0][1]
    assert record["identifier"] == "habr:123456"
    assert record["abstract"] == "Diabetes research and evidence."
    assert record["authors"] == "example" and record["tags"] == "medicine"


@pytest.mark.parametrize(
    "content",
    [
        b"<rss>",
        b"<html>Blocked</html>",
        b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel>&x;</channel></rss>',
    ],
)
def test_habr_invalid_or_entity_xml_is_reported(content):
    with pytest.raises(RuntimeError):
        habr_agent.parse_feed(content, START, END, strict=True)


def test_habr_reports_per_topic_outage(monkeypatch):
    def fetch(topic, *args):
        if topic == "down":
            raise RuntimeError("Synthetic outage")
        return habr_agent.parse_feed(HABR_XML, START, END)

    monkeypatch.setattr(habr_agent, "_fetch_topic", fetch)
    monkeypatch.setattr(habr_agent.time, "sleep", lambda _: None)
    result = habr_agent.fetch(["diabetes", "down"], 10, START, END)
    assert len(result) == 1 and result.partial
    assert result.errors[0]["code"] == "topic_failed"


def test_archive_query_quoting_and_html_metadata(monkeypatch):
    monkeypatch.setattr(
        archive_agent,
        "requests",
        SimpleNamespace(
            get=lambda *a, **k: FakeResponse(
                {
                    "response": {
                        "docs": [
                            {
                                "identifier": "test/doc",
                                "title": ["A", "title"],
                                "description": "<p>Diabetes evidence.</p>",
                                "publicdate": "2026-02-15T00:00:00Z",
                            }
                        ]
                    }
                }
            )
        ),
    )
    record = archive_agent.fetch(['diabetes" OR *:*'], 5, START, END)[0]
    assert record["url"].endswith("test%2Fdoc")
    assert record["abstract"] == "Diabetes evidence."
    assert record["published"] == "2026-02-15"
    assert archive_agent.build_query(['one" two'], START, END).count('"') == 2


def test_arxiv_entity_xml_rejected():
    with pytest.raises(DefusedXmlException):
        arxiv_agent.ET.fromstring(b'<!DOCTYPE feed [<!ENTITY x "boom">]><feed>&x;</feed>')


def test_arxiv_updated_pass_failure_is_visible(monkeypatch):
    monkeypatch.setattr(arxiv_agent, "_search", lambda *a: [item(source="arxiv")])
    monkeypatch.setattr(
        arxiv_agent, "_recently_updated", lambda *a: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = arxiv_agent.fetch(["diabetes"], 2, START, END)
    assert len(result) == 1 and result.warnings[0]["code"] == "updates_failed"


def test_openalex_abstract_and_metadata():
    record = openalex_agent._to_item(
        {
            "id": "https://openalex.org/W1",
            "title": "Paper",
            "cited_by_count": 1,
            "publication_date": "2026-02-15",
            "abstract_inverted_index": {"A": [0], "paper": [1]},
            "primary_location": {"landing_page_url": "https://example.com/paper"},
        }
    )
    assert record["abstract"] == "A paper" and record["identifier"] == "openalex:W1"


@pytest.mark.parametrize(
    "topic,text,expected",
    [
        ("патология", "Разбор патологии тканей", True),
        ("сахарный диабет", "Сахарного диабета", True),
        ("RAG", "storage fragment", False),
        ("AI", "clinical AI system", True),
        ("J45", "МКБ-10: J45.0", True),
        ("J45", "J450", False),
        ("medical multi-agent", "Medical agents in a multi step workflow", True),
    ],
)
def test_validator_word_boundaries_and_russian(topic, text, expected):
    assert (
        validator.validate_item(item(title=text, abstract="", content_kind="metadata_only"), [topic])
        is expected
    )


def test_validator_metadata_only_never_downloads(monkeypatch):
    monkeypatch.setattr(
        validator, "extract_text_from_url", lambda _: pytest.fail("metadata must not download menus")
    )
    assert not validator.validate_item(
        item(title="Asthma", abstract="Catalogue", content_kind="metadata_only"), ["diabetes"]
    )


def test_source_failure_is_structured_not_empty_success(monkeypatch):
    monkeypatch.setattr(
        ep, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret=never show this"))
    )
    result = scraper.get_new_items(["diabetes"], ["europepmc"], skip_seen=False)
    assert not result and result.errors and result.source_statuses == {"europepmc": "error"}
    assert "never show" not in str(result.diagnostics())


def test_partial_source_failure_keeps_other_results(monkeypatch):
    monkeypatch.setattr(ep, "fetch", lambda *a, **k: [item()])
    monkeypatch.setattr(
        habr_agent,
        "fetch",
        lambda *a, **k: SearchResults(errors=[{"source": "habr", "code": "down", "message": "Unavailable"}]),
    )
    result = scraper.get_new_items(["diabetes"], ["europepmc", "habr"], skip_seen=False)
    assert len(result) == 1 and result.partial
    assert result.source_statuses == {"europepmc": "ok", "habr": "error"}


def test_unsafe_source_link_is_not_returned(monkeypatch):
    monkeypatch.setattr(ep, "fetch", lambda *a, **k: [item(url="javascript:alert(1)")])
    result = scraper.get_new_items(["diabetes"], ["europepmc"], skip_seen=False)
    assert not result and result.warnings[0]["code"] == "invalid_record"


if __name__ == "__main__":
    from pathlib import Path

    raise SystemExit(pytest.main([str(Path(__file__).resolve().parent), "-q"]))
