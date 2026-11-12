"""Source coordination with per-request diagnostics and lexical validation."""

import logging
from datetime import date
from typing import Optional

import archive_agent
import arxiv_agent
import europepmc_agent
import habr_agent
import minzdrav_agent
import openalex_agent
from results import SearchResults, diagnostic
from security import UnsafeURL, normalize_url
from utils import get_history, load_config, resolve_date_range, update_history
from validation import bounded_int, clean_topics
from validator import validate_item

logger = logging.getLogger("archiv_tracker.scraper")
SOURCES = {
    module.SOURCE_ID: module
    for module in (
        archive_agent,
        arxiv_agent,
        openalex_agent,
        habr_agent,
        europepmc_agent,
        minzdrav_agent,
    )
}
SOURCE_NAMES = {sid: module.SOURCE_NAME for sid, module in SOURCES.items()}


def clean_sources(sources) -> list[str]:
    if not isinstance(sources, (list, tuple)) or not sources or len(sources) > len(SOURCES):
        raise ValueError("Укажите от одного до шести источников.")
    if any(not isinstance(sid, str) or sid not in SOURCES for sid in sources):
        raise ValueError("Неизвестный источник. Доступны: " + ", ".join(SOURCES) + ".")
    return list(dict.fromkeys(sources))


def fetch_source(
    source_id: str,
    topics: list[str],
    rows: int,
    start: date,
    end: date,
    europepmc_fulltext_fallback: Optional[bool] = None,
) -> SearchResults:
    """A source outage is never indistinguishable from a successful empty search."""
    result = SearchResults(source_statuses={source_id: "ok"})
    try:
        if source_id == "europepmc":
            fetched = SOURCES[source_id].fetch(
                topics, rows=rows, date_from=start, date_to=end, fulltext_fallback=europepmc_fulltext_fallback
            )
        else:
            fetched = SOURCES[source_id].fetch(topics, rows=rows, date_from=start, date_to=end)
        result.errors.extend(getattr(fetched, "errors", []))
        result.warnings.extend(getattr(fetched, "warnings", []))
        if not isinstance(fetched, list):
            raise TypeError("Invalid agent result")
        seen = set()
        for raw in fetched[:rows]:
            try:
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("identifier"), str)
                    or not raw["identifier"]
                    or len(raw["identifier"]) > 512
                ):
                    raise ValueError("Invalid identifier")
                item = dict(raw, source=source_id)
                item["url"] = normalize_url(item.get("url"))
                if not isinstance(item.get("title"), str):
                    raise ValueError("Invalid title")
                item["title"] = item["title"][:1000]
                if not isinstance(item.get("abstract", ""), str):
                    raise ValueError("Invalid abstract")
                item["abstract"] = item.get("abstract", "")[:50000]
                if item["identifier"] not in seen:
                    result.append(item)
                    seen.add(item["identifier"])
            except (ValueError, TypeError):
                result.warnings.append(
                    diagnostic(
                        source_id,
                        "invalid_record",
                        "Запись с некорректными метаданными или ссылкой пропущена.",
                    )
                )
    except Exception as exc:
        logger.warning("Источник %s недоступен (%s).", source_id, type(exc).__name__)
        response = getattr(exc, "response", None)
        reason = f"HTTP {response.status_code}" if response is not None else type(exc).__name__
        message = (
            "Сетевая политика отклонила адрес источника."
            if isinstance(exc, UnsafeURL)
            else f"Не удалось получить данные источника ({reason})."
        )
        result.errors.append(diagnostic(source_id, "source_unavailable", message))
    if result.errors:
        result.source_statuses[source_id] = "partial" if result else "error"
    elif result.warnings:
        result.source_statuses[source_id] = "partial"
    return result


def get_new_items(
    topics: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    rows: int = 20,
    skip_seen: bool = True,
    save_history: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    europepmc_fulltext_fallback: Optional[bool] = None,
) -> SearchResults:
    """Search + relevance. The pipeline saves history only AFTER successful processing.

    Explicit save_history=True is retained for search-only CLI users. MCP and
    main.process_items always pass False and manage their own read/write policy.
    """
    cfg = load_config()
    topics = clean_topics(cfg.get("topics", []) if topics is None else topics)
    sources = clean_sources(cfg.get("sources", list(SOURCES)) if sources is None else sources)
    bounded_int(rows, "rows", 1, 100)
    if not isinstance(skip_seen, bool) or not isinstance(save_history, bool):
        raise ValueError("Параметры истории должны быть логическими значениями.")
    if europepmc_fulltext_fallback is not None and not isinstance(europepmc_fulltext_fallback, bool):
        raise ValueError("Флаг europepmc_fulltext_fallback должен быть логическим или None.")
    start, end = resolve_date_range(date_from=date_from, date_to=date_to)
    history = set(get_history()) if skip_seen else set()
    result = SearchResults()
    seen = set()
    for sid in sources:
        batch = fetch_source(
            sid, topics, rows, start, end, europepmc_fulltext_fallback=europepmc_fulltext_fallback
        )
        result.errors.extend(batch.errors)
        result.warnings.extend(batch.warnings)
        result.source_statuses.update(batch.source_statuses)
        for item in batch:
            identifier = item["identifier"]
            if identifier in history or identifier in seen:
                continue
            try:
                if validate_item(item, topics):
                    result.append(item)
                    seen.add(identifier)
            except Exception:
                result.warnings.append(
                    diagnostic(sid, "validation_failed", "Одну запись не удалось проверить на релевантность.")
                )
                result.source_statuses[sid] = "partial"
    if result and save_history and not update_history([item["identifier"] for item in result]):
        result.warnings.append(diagnostic("history", "write_failed", "История не сохранена."))
    logger.info(
        "Поиск завершён: записей %d, ошибок %d, предупреждений %d.",
        len(result),
        len(result.errors),
        len(result.warnings),
    )
    return result


if __name__ == "__main__":
    found = get_new_items()
    for entry in found:
        print(entry["source"], entry["title"], entry["url"])
    for error in found.errors:
        print(error["source"], error["message"])
