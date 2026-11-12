from datetime import date
from types import SimpleNamespace

import pytest

import minzdrav_agent as mz
from tests.helpers import END, START, FakeResponse, mirror_entry, mirror_html, official_record


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(mz, "_CATALOG_CACHE", {})
    monkeypatch.setattr(mz.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        mz, "_settings", lambda: {"mode": "mirror", "base_url": mz.DEFAULT_BASE_URL, "ttl": 900}
    )


def test_mirror_pagination_includes_diabetes_page_two_and_paces(monkeypatch):
    first = mz.DEFAULT_BASE_URL + "/mkb-10/iv/"
    second = first + "page-2/"
    pages = {
        first: mirror_html(
            extra='<a href="page-2/">2</a><a href="https://evil.example/mkb-10/iv/page-3/">evil</a>'
        ),
        second: mirror_html("diabetes", "Сахарный диабет", 2026, extra='<a href="../">1</a>'),
    }
    calls, pauses = [], []
    monkeypatch.setattr(mz, "_class_page_urls", lambda base=None: [first])
    monkeypatch.setattr(mz.time, "sleep", pauses.append)

    def get(url, **kwargs):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(mz, "_http_get", get)
    result = mz.fetch(["сахарный диабет"], 5, START, END)
    assert len(result) == 1 and result[0]["identifier"] == "minzdrav:diabetes"
    assert calls == [first, second] and pauses == [mz.REQUEST_PAUSE_SECONDS]


def test_mirror_pagination_loop_is_bounded(monkeypatch):
    first = mz.DEFAULT_BASE_URL + "/mkb-10/i/"
    monkeypatch.setattr(mz, "_class_page_urls", lambda base=None: [first])
    monkeypatch.setattr(mz, "MAX_MIRROR_PAGES", 2)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return mirror_html(extra=f'<a href="/mkb-10/i/page-{len(calls) + 1}/">next</a>')

    monkeypatch.setattr(mz, "_http_get", get)
    catalog = mz.get_catalog()
    assert len(calls) == 2 and not catalog.complete
    assert catalog.warnings[0]["code"] == "catalog_limit" and not mz._CATALOG_CACHE


@pytest.mark.parametrize(
    "start,end,year,expected",
    [
        (date(2025, 1, 1), date(2025, 12, 31), 2024, False),
        (date(2025, 6, 1), date(2025, 6, 7), 2025, True),
        (date(2025, 12, 31), date(2026, 1, 1), 2024, False),
        (date(2025, 12, 31), date(2026, 1, 1), 2026, True),
        (date(2024, 1, 1), date(2026, 1, 1), None, False),
    ],
)
def test_year_filter_is_consistent_even_single_year(monkeypatch, start, end, year, expected):
    monkeypatch.setattr(mz, "get_catalog", lambda: mz.Catalog({"x": mirror_entry(year=year)}))
    result = mz.fetch(["астма"], 5, start, end)
    assert bool(result) is expected
    assert any(w["code"] == "year_precision" for w in result.warnings)
    if result:
        assert result[0]["published"] == str(year)  # Never a fabricated January 1.
        assert result[0]["content_kind"] == "metadata_only"
        assert result[0]["provenance"] == "third_party_mirror"
        assert not result[0]["official_id"] and not result[0]["full_text_available"]


def test_catalog_parser_codes_age_and_relative_urls():
    entry = mz._parse_class_page(mirror_html())[0]
    assert entry["title"] == "Бронхиальная астма"
    assert entry["codes"] == "J45, J46" and entry["age"] == "Взрослые, дети"
    assert entry["url"].endswith("/kr/asthma/") and entry["year"] == 2024


def test_failed_empty_catalog_not_cached_and_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(mz, "_class_page_urls", lambda base=None: [mz.DEFAULT_BASE_URL + "/mkb-10/i/"])

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("offline")
        return mirror_html()

    monkeypatch.setattr(mz, "_http_get", get)
    assert not mz.get_catalog() and not mz._CATALOG_CACHE
    assert mz.get_catalog() and len(calls) == 2
    assert mz.get_catalog() and len(calls) == 2


def test_cache_ttl_url_key_and_offline_fixture_isolation(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(mz.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mz, "_class_page_urls", lambda base=None: [base + "/mkb-10/i/"])
    monkeypatch.setattr(mz, "_http_get", lambda url, **kwargs: calls.append(url) or mirror_html())
    mz.get_catalog()
    mz.get_catalog(pages={"fixture": mirror_html("fixture")})
    assert "fixture" not in mz.get_catalog() and len(calls) == 1
    clock[0] = 901
    mz.get_catalog()
    assert len(calls) == 2
    monkeypatch.setattr(
        mz, "_settings", lambda: {"mode": "mirror", "base_url": "https://example.com", "ttl": 900}
    )
    mz.get_catalog()
    assert len(calls) == 3 and len(mz._CATALOG_CACHE) == 2


def test_official_api_pagination_and_ids(monkeypatch):
    calls = []
    responses = [
        {"Data": [official_record()], "TotalRecords": 2},
        {"Data": [official_record("456_2")], "TotalRecords": 2},
    ]

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(mz, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(
        mz, "_settings", lambda: {"mode": "official", "base_url": mz.DEFAULT_BASE_URL, "ttl": 900}
    )
    result = mz.fetch(["сахарный диабет"], 5, START, END)
    assert len(result) == 2 and not result.partial
    assert [call[1]["json"]["currentPage"] for call in calls] == [1, 2]
    assert result[0]["url"].startswith("https://cr.minzdrav.gov.ru/view-cr/")
    assert result[0]["provenance"] == "official_api" and result[0]["published"] == "2026-02-15"
    assert result[0]["date_precision"] == "day" and result[0]["official_id"]


def test_official_failure_has_explicit_fallback_warning(monkeypatch):
    monkeypatch.setattr(
        mz, "_settings", lambda: {"mode": "auto", "base_url": mz.DEFAULT_BASE_URL, "ttl": 900}
    )
    monkeypatch.setattr(mz, "_official_catalog", lambda: (_ for _ in ()).throw(RuntimeError("blocked")))
    monkeypatch.setattr(mz, "build_catalog", lambda **kwargs: mz.Catalog({"x": mirror_entry()}))
    result = mz.fetch(["астма"], 5, date(2024, 1, 1), END)
    assert len(result) == 1 and result.warnings[0]["code"] == "official_unavailable"
    assert "стороннего зеркала" in result.warnings[0]["message"]


def test_official_only_never_silently_falls_back(monkeypatch):
    monkeypatch.setattr(
        mz, "_settings", lambda: {"mode": "official", "base_url": mz.DEFAULT_BASE_URL, "ttl": 900}
    )
    monkeypatch.setattr(mz, "_official_catalog", lambda: (_ for _ in ()).throw(RuntimeError("blocked")))
    monkeypatch.setattr(mz, "build_catalog", lambda **kwargs: pytest.fail("unexpected mirror"))
    with pytest.raises(RuntimeError, match="Официальный API"):
        mz.get_catalog()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("15.02.2026", "2026-02-15"),
        ("2026-02-15", "2026-02-15"),
        ("/Date(0)/", "1970-01-01"),
        ("unknown", ""),
        (None, ""),
    ],
)
def test_official_date_parsing(value, expected):
    assert mz._official_date(value) == expected


def test_official_unknown_id_is_not_fabricated():
    assert mz._official_entry(official_record("mirror-slug")) is None
