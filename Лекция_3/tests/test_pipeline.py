import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

import main
import mcp_service
import scraper
import summarizer
import utils
from results import SearchResults
from tests.helpers import item


@pytest.mark.parametrize(
    "text",
    [
        "Medical agents analyze papers. Clinical papers need careful review. Agents coordinate the analysis.",
        "Агенты анализируют публикации. Медицинские публикации требуют проверки. Несколько агентов координируют поиск.",
    ],
)
def test_actual_lsa_runs_offline_for_english_and_russian(text):
    result = summarizer.summarize_with_details(text, 2)
    assert result.text and result.method in {"sumy_lsa", "sumy_lsa_regex"}
    assert len(summarizer._split_sentences(result.text)) <= 2


def test_summary_fallback_reports_its_real_method(monkeypatch):
    monkeypatch.setattr(summarizer, "LsaSummarizer", lambda *a: (_ for _ in ()).throw(RuntimeError("forced")))
    result = summarizer.summarize_with_details("One sentence. Second sentence. Third sentence.", 1)
    assert result.text == "One sentence." and result.method == "first_sentences"
    assert result.notice


def test_short_abstract_does_not_fetch_navigation(monkeypatch):
    monkeypatch.setattr(
        main, "get_new_items", lambda **kwargs: SearchResults([item(abstract="Short scientific abstract.")])
    )
    monkeypatch.setattr(
        main, "extract_text_from_url", lambda _: pytest.fail("short abstract must not download menus")
    )
    result = main.process_items(["diabetes"], ["europepmc"], skip_seen=False, save_history=False, send=False)
    assert result[0]["summary"] and result[0]["summary_input"] == "abstract"


def test_catalog_metadata_is_not_summarized_as_guideline(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_new_items",
        lambda **kwargs: SearchResults(
            [item(content_kind="metadata_only", abstract="Карточка каталога: диабет.")]
        ),
    )
    monkeypatch.setattr(
        main, "extract_text_from_url", lambda _: pytest.fail("metadata page must not download")
    )
    monkeypatch.setattr(
        main, "summarize_with_details", lambda *a: pytest.fail("metadata must not call LLM/LSA")
    )
    result = main.process_items(["diabetes"], ["europepmc"], save_history=False, send=False)
    assert result[0]["summary_method"] == "metadata_only"
    assert "не резюме" in result[0]["status"]


def test_history_saved_only_after_successful_summary(monkeypatch):
    monkeypatch.setattr(
        main, "get_new_items", lambda **kwargs: SearchResults([item("good"), item("bad", abstract="FAIL")])
    )

    def summary(text, *args):
        if text == "FAIL":
            raise RuntimeError("failed")
        assert utils.get_history() == []
        return summarizer.SummaryResult("Summary.", "test")

    monkeypatch.setattr(main, "summarize_with_details", summary)
    result = main.process_items(["diabetes"], ["europepmc"], skip_seen=False, save_history=True, send=False)
    assert len(result) == 2 and result.errors
    assert utils.get_history() == ["good"]


def test_no_text_and_email_failure_leave_history_retryable(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_new_items",
        lambda **kwargs: SearchResults([item("no-text", abstract=""), item("email-fail")]),
    )
    monkeypatch.setattr(main, "extract_text_from_url", lambda _: "")
    monkeypatch.setattr(main, "send_email", lambda *a: False)
    result = main.process_items(["diabetes"], ["europepmc"], skip_seen=False, save_history=True, send=True)
    assert len(result) == 2 and not utils.get_history()
    assert {w["code"] for w in result.warnings} >= {"text_unavailable", "send_failed"}


def test_llm_fallback_is_request_local_not_function_global(monkeypatch):
    monkeypatch.setattr(main, "get_new_items", lambda **kwargs: SearchResults([item()]))
    engines = []

    def summary(text, limit, engine, *args):
        engines.append(engine)
        if engine != "sumy":
            raise summarizer.LlmUnavailable("Synthetic model outage")
        return summarizer.SummaryResult("Summary.", "first_sentences")

    monkeypatch.setattr(main, "summarize_with_details", summary)
    first = main.process_items(["diabetes"], ["europepmc"], save_history=False, engine="openai", send=False)
    second = main.process_items(["diabetes"], ["europepmc"], save_history=False, engine="sumy", send=False)
    assert first.warnings and not second.warnings
    assert not hasattr(main.process_items, "last_llm_notice")


def test_web_session_history_never_reads_or_writes_server_history(monkeypatch):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return SearchResults([item("seen"), item("new")])

    monkeypatch.setattr(main, "get_new_items", fetch)
    monkeypatch.setattr(main, "update_history", lambda *a: pytest.fail("server history must not be written"))
    result = main.process_items(
        ["diabetes"], ["europepmc"], skip_seen=True, save_history=False, session_history=["seen"], send=False
    )
    assert [entry["identifier"] for entry in result] == ["new"]
    assert calls[0]["skip_seen"] is False and calls[0]["save_history"] is False


def test_history_concurrent_updates_are_atomic_and_corruption_safe():
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(lambda n: utils.update_history([str(n)]), range(24)))
    assert set(utils.get_history()) == {str(n) for n in range(24)}
    assert isinstance(json.loads(utils.HISTORY_PATH.read_text()), list)
    utils.HISTORY_PATH.write_text("{broken", encoding="utf-8")
    assert utils.get_history() == []
    assert utils.update_history(["recovered"]) and utils.get_history() == ["recovered"]


def test_history_read_does_not_create_file():
    assert not utils.HISTORY_PATH.exists() and utils.get_history() == []
    assert not utils.HISTORY_PATH.exists()


def test_scheduler_first_run_is_not_paused_and_no_overlap():
    scheduler = main.create_scheduler({"check_interval_seconds": 60})
    job = scheduler.get_jobs()[0]
    assert job.next_run_time is not None
    assert job.max_instances == 1 and job.coalesce is True


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (None, None, (date(2026, 11, 6), date(2026, 11, 12))),
        (date(2020, 1, 1), None, (date(2020, 1, 1), date(2026, 11, 12))),
        (None, date(2020, 1, 7), (date(2020, 1, 1), date(2020, 1, 7))),
    ],
)
def test_dates_preserve_single_supplied_bound(start, end, expected):
    assert utils.resolve_date_range(date_from=start, date_to=end, today=date(2026, 11, 12)) == expected


def test_month_end_and_reverse_dates():
    assert utils.resolve_date_range("month", today=date(2024, 3, 31))[0] == date(2024, 2, 29)
    with pytest.raises(ValueError):
        utils.resolve_date_range(date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))


@pytest.mark.parametrize(
    "call",
    [
        lambda: mcp_service.search_publications(["x"], sources=[]),
        lambda: mcp_service.search_publications(["x"], sources=["unknown"]),
        lambda: mcp_service.search_publications("not-a-list"),
        lambda: mcp_service.search_publications(["x"] * 11),
        lambda: mcp_service.search_publications(["x" * 201]),
        lambda: mcp_service.search_publications(["x"], rows=True),
        lambda: mcp_service.search_publications(["x"], rows=1.2),
        lambda: mcp_service.summarize_text("x" * 10001),
        lambda: mcp_service.summarize_text("   "),
        lambda: mcp_service.summarize_text("text", True),
        lambda: mcp_service.parse_date("20260101", "date_from"),
        lambda: mcp_service.parse_date("2026-02-30", "date_from"),
    ],
)
def test_mcp_limits_and_strict_types(call):
    with pytest.raises(ValueError):
        call()


def test_mcp_search_readonly_and_structured_errors(monkeypatch):
    monkeypatch.setattr(utils, "get_history", lambda: pytest.fail("MCP must not read history"))
    monkeypatch.setattr(scraper, "get_history", lambda: pytest.fail("MCP must not read history"))
    monkeypatch.setattr(
        scraper.SOURCES["europepmc"], "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = mcp_service.search_publications(["diabetes"], ["europepmc"], 5, "2026-01-01", "2026-12-31")
    assert result["count"] == 0 and result["errors"] and result["partial"]
    assert not utils.HISTORY_PATH.exists()


def test_mcp_raw_source_reports_outage(monkeypatch):
    monkeypatch.setattr(
        scraper.SOURCES["europepmc"], "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = mcp_service.search_source("europepmc", ["diabetes"], 5)
    assert result["errors"] and result["source_statuses"]["europepmc"] == "error"


def test_mcp_summary_exposes_actual_method():
    result = mcp_service.summarize_text("A medical agent reads papers. Another agent checks the evidence.", 1)
    assert result["summary"] and result["method"].startswith("sumy_lsa")


def test_lsa_concurrency_limit_is_explicit_fallback(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(summarizer, "_LSA_SLOTS", SimpleNamespace(acquire=lambda **kwargs: False))
    result = summarizer.summarize_with_details("First sentence. Another sentence.", 1)
    assert result.method == "first_sentences" and "занят" in result.notice


def test_lsa_sentence_budget_is_bounded_and_visible(monkeypatch):
    received = []
    monkeypatch.setattr(
        summarizer,
        "_lsa_summary",
        lambda text, limit: received.append(text) or summarizer.SummaryResult("Result.", "sumy_lsa_regex"),
    )
    result = summarizer.summarize_with_details("A sentence. " * 201, 1)
    assert len(summarizer._split_sentences(received[0])) == 200
    assert "первые 200" in result.notice


def test_pipeline_budget_does_not_mark_unprocessed_items_seen(monkeypatch):
    monkeypatch.setattr(main, "MAX_PROCESSED_ITEMS", 2)
    monkeypatch.setattr(
        main, "get_new_items", lambda **kwargs: SearchResults([item(str(i)) for i in range(4)])
    )
    monkeypatch.setattr(
        main, "summarize_with_details", lambda *a: summarizer.SummaryResult("Summary.", "test")
    )
    result = main.process_items(["diabetes"], ["europepmc"], save_history=True, send=False)
    assert len(result) == 2 and result.warnings[0]["code"] == "processing_limit"
    assert utils.get_history() == ["0", "1"]
