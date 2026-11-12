# mcp_service.py
"""Сервисный слой MCP: чистая логика tools без транспорта MCP.

MCP-сервер (``mcp_server.py``) только оборачивает функции отсюда в tools.
Разделение повторяет MCP_calc: ``service.py`` считает, ``server.py`` ведёт
протокол. Благодаря этому логику можно проверять офлайн-тестами без пакета
``mcp`` и без stdio-транспорта.

Все функции возвращают JSON-сериализуемые словари и не пишут в историю:
поиск через MCP — read-only, повторный вызов с теми же аргументами даёт
тот же результат.
"""

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from scraper import SOURCES, SOURCE_NAMES, get_new_items
from summarizer import summarize
from utils import resolve_date_range

logger = logging.getLogger("archiv_tracker.mcp_service")

# Человекочитаемые описания источников для каталога tools.
SOURCE_DESCRIPTIONS = {
    "archive": "Internet Archive (archive.org): книги, статьи и документы. Поиск фразой целиком.",
    "arxiv": "arXiv.org: препринты по физике, математике, CS и смежным областям. Поиск по всем полям.",
    "openalex": "OpenAlex: открытый граф научных знаний — статьи, препринты, книги. Поиск по заголовку и аннотации.",
    "habr": "Habr (habr.com): русскоязычные статьи и новости. Поиск по RSS-ленте, тема ищется фразой целиком.",
    "europepmc": "Europe PMC: биомедицинская база EBI (PubMed, PMC, препринты). Поиск фразами по заголовку и аннотации, аннотация в ответе.",
    "minzdrav": "Рубрикатор клинических рекомендаций Минздрава РФ: справочник КР по заболеваниям, кодам МКБ-10 и возрастам. Источник русскоязычный, данные — метаданные карточек (полный текст — в Telegram-боте).",
}

# Подсказки по языку запросов для каждого источника.
SOURCE_QUERY_HINTS = {
    "archive": "Темы — фразы целиком, например «medical multi-agent».",
    "arxiv": "Темы — слова или фразы; слова одной темы ищутся без порядка.",
    "openalex": "Темы — слова или фразы; слова одной темы ищутся без порядка.",
    "habr": "Темы — фразы целиком, например «multi-agent».",
    "europepmc": "Темы — фразы целиком, например «medical multi-agent».",
    "minzdrav": "Темы — название заболевания, синоним или код МКБ-10, например «астма» или «J45». Поиск по каталогу КР, фильтр по году пересмотра.",
}


def list_sources_catalog() -> Dict[str, Any]:
    """Каталог источников: id, название, описание и подсказка по запросам."""
    sources = []
    for sid in SOURCES:
        sources.append(
            {
                "id": sid,
                "name": SOURCE_NAMES.get(sid, sid),
                "description": SOURCE_DESCRIPTIONS.get(sid, ""),
                "query_hint": SOURCE_QUERY_HINTS.get(sid, ""),
            }
        )
    return {"sources": sources}


def parse_date(value: Optional[str], field: str) -> Optional[date]:
    """Разобрать дату ``ГГГГ-ММ-ДД``; пустое значение — None (по умолчанию)."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(
            f"Поле «{field}» должно быть датой в формате ГГГГ-ММ-ДД, получено: {value!r}."
        ) from exc


def resolve_dates(
    date_from: Optional[str] = None, date_to: Optional[str] = None
) -> tuple[date, date]:
    """Границы дат из строк MCP: обе заданы — берём их, иначе неделя по умолчанию."""
    parsed_from = parse_date(date_from, "date_from")
    parsed_to = parse_date(date_to, "date_to")
    if parsed_from is not None and parsed_to is not None:
        if parsed_from > parsed_to:
            raise ValueError("Дата «date_from» не может быть позже «date_to».")
        return parsed_from, parsed_to
    return resolve_date_range(date_from=parsed_from, date_to=parsed_to)


def _clean_topics(topics: List[str]) -> List[str]:
    """Темы без пустых строк; пустой список — ошибка с понятным текстом."""
    cleaned = [str(topic).strip() for topic in (topics or []) if str(topic).strip()]
    if not cleaned:
        raise ValueError("Укажите хотя бы одну тему (topics).")
    return cleaned


def _clean_rows(rows: int) -> int:
    """Лимит записей с одного источника: 1–100."""
    try:
        value = int(rows)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Поле «rows» должно быть целым числом, получено: {rows!r}.") from exc
    if value < 1 or value > 100:
        raise ValueError("Поле «rows» должно быть от 1 до 100.")
    return value


def search_source(
    source_id: str,
    topics: List[str],
    rows: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Сырой поиск по одному источнику: записи агента без валидатора.

    Возвращает записи в общем формате трекера. Релевантность теме здесь не
    проверяется — для проверенного поиска используйте ``search_publications``.
    """
    agent = SOURCES.get(source_id)
    if agent is None:
        known = ", ".join(sorted(SOURCES))
        raise ValueError(f"Неизвестный источник «{source_id}». Доступны: {known}.")
    cleaned_topics = _clean_topics(topics)
    limit = _clean_rows(rows)
    start, end = resolve_dates(date_from, date_to)
    items = agent.fetch(cleaned_topics, rows=limit, date_from=start, date_to=end)
    return {
        "source": source_id,
        "source_name": SOURCE_NAMES.get(source_id, source_id),
        "topics": cleaned_topics,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "count": len(items),
        "items": items,
    }


def search_publications(
    topics: List[str],
    sources: Optional[List[str]] = None,
    rows: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Проверенный поиск по нескольким источникам (скрапер + валидатор).

    Опрашивает агенты-источники и оставляет только записи, где все слова
    хотя бы одной темы есть в заголовке, аннотации или тексте страницы.
    История не читается и не пополняется: вызов read-only.
    """
    cleaned_topics = _clean_topics(topics)
    limit = _clean_rows(rows)
    start, end = resolve_dates(date_from, date_to)
    if sources:
        cleaned_sources = [str(source).strip() for source in sources if str(source).strip()]
        unknown = [source for source in cleaned_sources if source not in SOURCES]
        if unknown:
            known = ", ".join(sorted(SOURCES))
            raise ValueError(
                f"Неизвестные источники: {', '.join(unknown)}. Доступны: {known}."
            )
    else:
        cleaned_sources = list(SOURCES)
    items = get_new_items(
        topics=cleaned_topics,
        sources=cleaned_sources,
        rows=limit,
        skip_seen=False,
        save_history=False,
        date_from=start,
        date_to=end,
    )
    return {
        "topics": cleaned_topics,
        "sources": cleaned_sources,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "count": len(items),
        "items": items,
    }


def summarize_text(text: str, max_sentences: int = 3) -> Dict[str, Any]:
    """Резюме текста.

    Латиница — алгоритмом Sumy LSA (как в оркестраторе по умолчанию).
    Кириллица (например, карточки Рубрикатора КР Минздрава) резюмируется
    первыми предложениями, так как у Sumy нет русского токенизатора.
    """
    if not str(text or "").strip():
        raise ValueError("Поле «text» пустое: нечего резюмировать.")
    try:
        limit = int(max_sentences)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Поле «max_sentences» должно быть целым числом, получено: {max_sentences!r}."
        ) from exc
    if limit < 1 or limit > 10:
        raise ValueError("Поле «max_sentences» должно быть от 1 до 10.")
    summary = summarize(str(text), max_sentences=limit, engine="sumy")
    return {"summary": summary, "max_sentences": limit}
