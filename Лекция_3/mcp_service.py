"""Transport-free MCP services. Searches do not read or write user history.

Read-only means no persistent user-state changes, not deterministic results:
external source contents and availability can change between identical calls.
"""

from datetime import date
from typing import Optional

from scraper import SOURCE_NAMES, SOURCES, clean_sources, fetch_source, get_new_items
from summarizer import summarize_with_details
from utils import load_config, resolve_date_range
from validation import MAX_TEXT_LENGTH, bounded_int, clean_topics, parse_iso_date

SOURCE_DESCRIPTIONS = {
    "archive": "Internet Archive: книги, статьи, документы. Поиск фразами.",
    "arxiv": "arXiv: научные препринты и обновления версий. Поиск по всем полям.",
    "openalex": "OpenAlex: граф научных работ. Поиск по заголовку и аннотации.",
    "habr": "Habr: русскоязычные статьи и новости, ограниченная поисковая RSS-лента.",
    "europepmc": "Europe PMC: биомедицинские публикации EBI, аннотации, авторы, журнал. Новые записи первыми.",
    "minzdrav": "Каталог КР: официальный API или явно помеченное стороннее зеркало. Только метаданные; полный текст и клиническая актуальность не проверяются.",
}
SOURCE_QUERY_HINTS = {
    "archive": "Фраза целиком, например medical multi-agent.",
    "arxiv": "Слова темы без фиксированного порядка.",
    "openalex": "Слова темы без фиксированного порядка.",
    "habr": "Фраза целиком; глубина RSS ограничена источником.",
    "europepmc": "Английские фразы, например diabetes или histopathology.",
    "minzdrav": "Русское название или МКБ-10, например сахарный диабет / E11. API — дата публикации реестра, зеркало — только год пересмотра (всегда, даже для одного года).",
}


def list_sources_catalog() -> dict:
    return {
        "sources": [
            {
                "id": sid,
                "name": SOURCE_NAMES[sid],
                "description": SOURCE_DESCRIPTIONS[sid],
                "query_hint": SOURCE_QUERY_HINTS[sid],
            }
            for sid in SOURCES
        ],
        "limits": {
            "topics": 10,
            "topic_length": 200,
            "rows_per_source": 100,
            "summary_text_length": MAX_TEXT_LENGTH,
        },
    }


def parse_date(value: Optional[str], field: str) -> Optional[date]:
    return parse_iso_date(value, field)


def resolve_dates(date_from: Optional[str] = None, date_to: Optional[str] = None) -> tuple[date, date]:
    return resolve_date_range(
        date_from=parse_date(date_from, "date_from"), date_to=parse_date(date_to, "date_to")
    )


def search_source(
    source_id: str,
    topics: list[str],
    rows: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    clean_sources([source_id])
    topics = clean_topics(topics)
    bounded_int(rows, "rows", 1, 100)
    start, end = resolve_dates(date_from, date_to)
    items = fetch_source(source_id, topics, rows, start, end)
    return {
        "source": source_id,
        "source_name": SOURCE_NAMES[source_id],
        "topics": topics,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "count": len(items),
        "items": list(items),
        **items.diagnostics(),
    }


def search_publications(
    topics: list[str],
    sources: Optional[list[str]] = None,
    rows: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    topics = clean_topics(topics)
    bounded_int(rows, "rows", 1, 100)
    sources = clean_sources(load_config().get("sources", list(SOURCES)) if sources is None else sources)
    start, end = resolve_dates(date_from, date_to)
    items = get_new_items(
        topics, sources, rows, skip_seen=False, save_history=False, date_from=start, date_to=end
    )
    return {
        "topics": topics,
        "sources": sources,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "count": len(items),
        "items": list(items),
        **items.diagnostics(),
    }


def summarize_text(text: str, max_sentences: int = 3) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Поле «text» должно содержать непустой текст.")
    result = summarize_with_details(text, max_sentences=max_sentences, engine="sumy")
    return {
        "summary": result.text,
        "method": result.method,
        "notice": result.notice,
        "max_sentences": max_sentences,
    }
