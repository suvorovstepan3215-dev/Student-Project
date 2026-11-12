# habr_agent.py
"""Агент-источник: Habr (habr.com).

Русскоязыческий технический сайт: статьи, новости и переводы с авторами,
датами публикации, хабами и тегами. Полнотекстового API у Habr нет, поэтому
агент читает RSS поиска:
    https://habr.com/ru/rss/search/?q=<тема>&target_type=posts

Ответ — RSS 2.0, разбор выполняется стандартной библиотекой; HTML из лида
статьи вычищается BeautifulSoup. Обе зависимости уже есть в requirements.txt.

Особенности источника:
- RSS отдаёт короткую ленту и не гарантирует сортировку по дате, поэтому
  агент сам отбирает записи по ``pubDate`` и сортирует их по убыванию;
- запрос делается по одному на каждую тему, результаты склеиваются по
  идентификатору публикации — темы ищутся фразой целиком, как на archive.org;
- в RSS лежит только лид статьи. Если слов темы в лиде нет, валидатор
  проверяет текст страницы, а оркестратор берёт его же для резюме.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published
и добавляет tags.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from network import public_requests as requests
from results import SearchResults, SourceUnavailable, diagnostic
from utils import load_config, resolve_date_range
from validation import bounded_int, clean_topics

logger = logging.getLogger("archiv_tracker.habr")

SOURCE_ID = "habr"
SOURCE_NAME = "habr.com"
HABR_RSS_URL = "https://habr.com/ru/rss/search/"

# Значения HTTP-заголовков кодируются latin-1, поэтому здесь только ASCII.
USER_AGENT = "Mozilla/5.0 (compatible; multi-agent-tracker/1.0; educational project)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

# Пауза между запросами по разным темам: не частим к чужому сайту.
REQUEST_PAUSE_SECONDS = 1.0

# Habr кладёт логин автора в dc:creator; в некоторых лентах бывает author.
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"

# Хвост лида «Читать далее» / «Read more» — это не текст статьи.
READ_MORE = re.compile(r"(Читать далее|Read more)\s*$", re.IGNORECASE)

# Числовой идентификатор публикации в ссылке: /articles/1037770/, /news/1025736/,
# /companies/<login>/articles/1053878/.
POST_ID = re.compile(r"/(?:articles|news|posts)/(\d+)")
FALLBACK_POST_ID = re.compile(r"/(\d{5,})")


def build_params(topic: str) -> Dict[str, str]:
    """Параметры RSS-поиска Habr для одной темы.

    ``target_type=posts`` ограничивает выдачу публикациями (статьи и новости),
    ``order=date`` просит ленту посвежее — но полагаться на неё нельзя,
    поэтому агент сортирует записи сам.
    """
    return {"q": topic, "target_type": "posts", "order": "date"}


def _clean(text) -> str:
    """Убрать переносы строк и лишние пробелы."""
    if isinstance(text, list):
        text = " ".join(map(str, text))
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_pubdate(value: str) -> Optional[date]:
    """Дата публикации из формата RFC 822: «Thu, 21 May 2026 11:51:41 GMT»."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value).strip()).date()
    except (TypeError, ValueError, IndexError):
        return None


def html_to_text(html: str) -> str:
    """Видимый текст из HTML-лида статьи, без хвоста «Читать далее»."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = _clean(soup.get_text(" "))
    return _clean(READ_MORE.sub("", text))


def _item_url(entry: ET.Element) -> str:
    """Ссылка на публикацию: из ``link``, иначе из ``guid`` без utm-меток."""
    link = _clean(entry.findtext("link"))
    if link:
        return link
    guid = _clean(entry.findtext("guid"))
    return guid.split("?")[0] if guid else ""


def _author(entry: ET.Element) -> str:
    """Автор публикации — логин на Habr.

    В RSS поле ``author`` бывает вида ``nickname (mail@example.com)`` —
    оставляем только логин. Если автора в ленте нет, поле пустое.
    """
    raw = _clean(entry.findtext(DC_CREATOR)) or _clean(entry.findtext("author"))
    return raw.split("(")[0].strip() if raw else ""


def _tags(entry: ET.Element) -> str:
    """Теги публикации через запятую (лежат в ``category``, иногда в обёртке)."""
    found = [_clean(tag.text) for tag in entry.findall(".//category")]
    return ", ".join(tag for tag in found if tag)


def _to_item(entry: ET.Element, day: Optional[date] = None) -> Optional[Dict]:
    """Привести элемент RSS к общему формату записи трекера."""
    url = _item_url(entry)
    if not url:
        return None
    match = POST_ID.search(url) or FALLBACK_POST_ID.search(url)
    post_id = match.group(1) if match else url
    if day is None:
        day = parse_pubdate(_clean(entry.findtext("pubDate")))
    return {
        "source": SOURCE_ID,
        "identifier": f"{SOURCE_ID}:{post_id}",
        "title": _clean(entry.findtext("title")) or "(без названия)",
        "url": url,
        "abstract": html_to_text(entry.findtext("description") or ""),
        "authors": _author(entry),
        "published": day.isoformat() if day else "",
        "tags": _tags(entry),
    }


def parse_feed(
    content: bytes, date_from: date, date_to: date, *, strict: bool = False
) -> List[Tuple[date, Dict]]:
    """Разобрать ленту RSS и оставить записи внутри диапазона дат.

    Возвращает пары ``(дата, запись)``, чтобы вызывающий код мог отсортировать
    их по убыванию даты. Записи без ``pubDate`` отбрасываются: проверить их
    на попадание в диапазон нельзя.
    """
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, DefusedXmlException) as exc:
        if strict:
            raise SourceUnavailable("Habr вернул некорректный или небезопасный RSS.") from exc
        return []
    if strict and root.tag != "rss":
        raise SourceUnavailable("Habr вернул не RSS, возможно страницу защиты от ботов.")
    entries = root.findall(".//item")
    pairs: List[Tuple[date, Dict]] = []
    for entry in entries:
        day = parse_pubdate(_clean(entry.findtext("pubDate")))
        item = _to_item(entry, day)
        if item is None:
            continue
        if day is None:
            logger.info(f"⏭ {item['identifier']}: нет даты публикации – пропуск")
            continue
        if date_from <= day <= date_to:
            pairs.append((day, item))
    logger.info(f"Habr: в ленте {len(entries)} записей, в диапазоне дат {len(pairs)}")
    return pairs


def _fetch_topic(topic: str, date_from: date, date_to: date) -> List[Tuple[date, Dict]]:
    """Один запрос к RSS-поиску Habr по теме."""
    params = build_params(topic)
    logger.info(f"Запрос к Habr: q={topic!r} (лента поиска)")
    resp = requests.get(HABR_RSS_URL, params=params, headers=HEADERS, timeout=20)
    if resp.status_code in (403, 429):
        logger.warning(
            f"Habr: HTTP {resp.status_code} – сайт отклонил запрос "
            f"(возможны антибот-защита или ограничения по частоте)"
        )
    resp.raise_for_status()
    return parse_feed(resp.content, date_from, date_to, strict=True)


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить публикации Habr по темам и диапазону дат.

    По каждой теме делается отдельный запрос; одинаковые публикации из разных
    тем склеиваются по идентификатору. Записи возвращаются по убыванию даты
    публикации, не больше ``rows`` штук.
    """
    wanted = clean_topics(topics)
    bounded_int(rows, "rows", 1, 100)
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    logger.info(f"Habr: тем {len(wanted)}, диапазон {date_from} — {date_to} (до {rows} публикаций)")

    errors = []
    by_id: Dict[str, Tuple[date, Dict]] = {}
    for index, topic in enumerate(wanted):
        if index:
            time.sleep(REQUEST_PAUSE_SECONDS)
        try:
            for day, item in _fetch_topic(topic, date_from, date_to):
                by_id.setdefault(item["identifier"], (day, item))
        except Exception as exc:
            # Ошибка по одной теме не должна ронять остальные источники цикла.
            logger.warning("Habr: запрос одной темы не удался (%s).", type(exc).__name__)
            errors.append(
                diagnostic(
                    SOURCE_ID, "topic_failed", f"Habr: запрос темы №{index + 1} не выполнен; выдача неполная."
                )
            )

    pairs = sorted(by_id.values(), key=lambda pair: pair[0], reverse=True)
    items = [item for _, item in pairs]
    if len(items) > rows:
        logger.info(f"Habr: в диапазоне {len(items)}, отдано последних {rows}")
        items = items[:rows]
    else:
        logger.info(f"Habr: отдано {len(items)}")
    return SearchResults(items, errors=errors)


if __name__ == "__main__":
    for record in fetch(load_config().get("topics") or ["multi-agent"], rows=5):
        print(record["identifier"], "|", record["published"], "|", record["title"][:80])
        print("   ", record["authors"] or "(автор не указан)", "|", record["url"])
