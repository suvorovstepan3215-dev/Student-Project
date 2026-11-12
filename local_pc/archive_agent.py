# archive_agent.py
"""Агент-источник: Internet Archive (archive.org).

Ищет последние записи по темам через API расширенного поиска archive.org.
Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract
"""

import logging
from datetime import date
from typing import Dict, List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from network import public_requests as requests
from utils import resolve_date_range
from validation import bounded_int, clean_topics

logger = logging.getLogger("archiv_tracker.archive")

SOURCE_ID = "archive"
SOURCE_NAME = "archive.org"
ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"  # URL API расширенного поиска


def _as_text(value) -> str:
    """archive.org иногда возвращает поля списком – склеиваем в строку."""
    if isinstance(value, list):
        return " ".join(map(str, value))
    return str(value or "")


def build_query(topics: List[str], date_from: date, date_to: date) -> str:
    """Собрать запрос Lucene: темы через OR и диапазон publicdate.

    archive.org разворачивает даты ``YYYY-MM-DD`` на полные сутки,
    поэтому конечный день входит в выборку целиком.
    """
    topic_q = " OR ".join(f'"{topic.replace(chr(34), " ").replace(chr(92), " ")}"' for topic in topics)
    date_q = f"publicdate:[{date_from.isoformat()} TO {date_to.isoformat()}]"
    return f"({topic_q}) AND {date_q}"


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить последние записи archive.org по темам и диапазону дат."""
    topics = clean_topics(topics)
    bounded_int(rows, "rows", 1, 100)
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    query = build_query(topics, date_from, date_to)
    params = {
        "q": query,
        "fl[]": ["identifier", "title", "description", "publicdate", "creator"],
        "sort[]": "publicdate desc",
        "rows": rows,
        "output": "json",
    }
    logger.info(f"Запрос к archive.org: {query} (последние {rows} записей)")
    resp = requests.get(ARCHIVE_SEARCH_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json().get("response", {})
    docs = data.get("docs", [])
    logger.info(f"archive.org: всего совпадений {data.get('numFound', '?')}, загружено {len(docs)}")
    items = []
    for doc in docs:
        identifier = doc.get("identifier")
        if not isinstance(identifier, str) or not identifier:
            continue
        items.append(
            {
                "source": SOURCE_ID,
                "identifier": identifier,
                "title": _as_text(doc.get("title")) or "(без названия)",
                "url": f"https://archive.org/details/{quote(identifier, safe='')}",
                "abstract": BeautifulSoup(_as_text(doc.get("description")), "html.parser").get_text(
                    " ", strip=True
                ),
                "published": _as_text(doc.get("publicdate"))[:10],
                "authors": _as_text(doc.get("creator")),
            }
        )
    return items
