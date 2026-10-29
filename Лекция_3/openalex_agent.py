# openalex_agent.py
"""Агент-источник: OpenAlex.

OpenAlex — открытый граф научных знаний: сотни миллионов работ (статьи,
препринты, книги, главы, диссертации) с метаданными, авторами, аннотациями
и ссылками на открытые версии. Источник пополняется из издательств,
репозиториев и других баз, поэтому в нём есть и записи arXiv, и записи
журналов, которых нет ни на archive.org, ни в arXiv.

Поиск идёт через REST API https://api.openalex.org/works, ответ — JSON,
разбор выполняется стандартной библиотекой. Ключ API не обязателен: без
ключа запросы попадают в общий бесплатный дневной лимит на IP-адрес,
поэтому агент подставляет ``mailto`` и (если задан) ``api_key``.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published
и добавляет doi, venue, cited_by_count.
"""

import logging
import os
import re
import time
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import load_config, resolve_date_range

logger = logging.getLogger("archiv_tracker.openalex")

SOURCE_ID = "openalex"
SOURCE_NAME = "openalex.org"
OPENALEX_API_URL = "https://api.openalex.org/works"

# OpenAlex отдаёт не больше 200 записей за одну страницу.
PER_PAGE_MAX = 200

# Просим только нужные поля: полный ответ по работе занимает десятки
# килобайт (аффилиации авторов, концепты, ссылки на другие сущности).
SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_date",
        "publication_year",
        "cited_by_count",
        "authorships",
        "primary_location",
        "open_access",
        "abstract_inverted_index",
    ]
)

USER_AGENT = "multi-agent-publication-tracker/1.0 (MAS course project)"

# Символы, которые ломают синтаксис фильтра OpenAlex: запятая разделяет
# фильтры, двоеточие отделяет имя фильтра от значения, «|» означает OR,
# «&» — AND, кавычки задают точную фразу.
_FILTER_SPECIAL = re.compile(r"[,:|&\"']")


def _clean(text) -> str:
    """Убрать переносы строк и лишние пробелы из полей OpenAlex."""
    if isinstance(text, list):
        text = " ".join(map(str, text))
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _search_value(topic: str) -> str:
    """Тема как значение поиска OpenAlex.

    Пробел между словами OpenAlex трактует как AND: «medical multi-agent»
    находит работы, где встретились оба слова, порядок не важен. Служебные
    символы фильтра заменяем пробелом, чтобы запрос не распался.
    """
    cleaned = _FILTER_SPECIAL.sub(" ", topic)
    return re.sub(r"\s+", " ", cleaned).strip()


def build_text_query(topics: List[str]) -> str:
    """Слова одной темы через пробел (AND), темы между собой через «|» (OR).

    «medical multi-agent» и «clinical decision support» превращаются в
    ``medical multi-agent|clinical decision support`` — то есть подходят
    работы, где есть обе слова первой темы или все три слова второй.
    """
    values = [_search_value(topic) for topic in topics]
    return "|".join(value for value in values if value)


def build_query(topics: List[str], date_from: date, date_to: date) -> str:
    """Собрать значение параметра ``filter``: поиск и диапазон дат публикации.

    Поиск идёт по заголовку и аннотации (``title_and_abstract.search``),
    даты — по ``publication_date``; границы включительные, как и у других
    источников проекта.
    """
    text_query = build_text_query(topics)
    if not text_query:
        return ""
    return (
        f"title_and_abstract.search:{text_query},"
        f"from_publication_date:{date_from.isoformat()},"
        f"to_publication_date:{date_to.isoformat()}"
    )


def reconstruct_abstract(index: Optional[Dict]) -> str:
    """Собрать аннотацию из «перевёрнутого индекса» OpenAlex.

    API отдаёт аннотацию не строкой, а словарём «слово → номера позиций»:
    ``{"Large": [0], "language": [1, 132], ...}``. Расставляем слова по
    позициям и склеиваем — получается исходный текст аннотации.
    """
    if not isinstance(index, dict) or not index:
        return ""
    positions: Dict[int, str] = {}
    for word, slots in index.items():
        if not isinstance(slots, list):
            continue
        for slot in slots:
            try:
                positions[int(slot)] = str(word)
            except (TypeError, ValueError):
                continue
    if not positions:
        return ""
    return " ".join(positions[slot] for slot in sorted(positions))


def _work_url(work: Dict) -> str:
    """Ссылка на работу: страница источника, затем открытый файл, DOI, OpenAlex.

    Сначала берём ``landing_page_url`` (HTML-страница издателя или
    репозитория — её текст умеет читать оркестратор), потом открытый файл
    из ``open_access``, потом DOI и только в конце страницу самой работы
    в OpenAlex.
    """
    location = work.get("primary_location") or {}
    open_access = work.get("open_access") or {}
    for candidate in (
        location.get("landing_page_url"),
        open_access.get("oa_url"),
        work.get("doi"),
        work.get("id"),
    ):
        if candidate:
            return str(candidate).replace("http://", "https://")
    return ""


def _bare_doi(value) -> str:
    """DOI без префикса ``https://doi.org/`` — в ответе OpenAlex он ссылкой."""
    doi = _clean(value)
    return doi.rsplit("doi.org/", 1)[-1] if doi else ""


def _authors(work: Dict) -> str:
    """Авторы работы через запятую.

    OpenAlex хранит имя в двух вариантах: ``display_name`` (может быть
    в национальном написании) и ``raw_author_name`` (как указано в самой
    работе). Берём первое непустое, порядок авторов не меняем.
    """
    names = []
    for authorship in work.get("authorships") or []:
        authorship = authorship or {}
        author = authorship.get("author") or {}
        name = _clean(author.get("display_name")) or _clean(authorship.get("raw_author_name"))
        if name:
            names.append(name)
    return ", ".join(names)


def _venue(work: Dict) -> str:
    """Где опубликована работа: журнал, репозиторий или издатель."""
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return _clean(source.get("display_name") or location.get("raw_source_name"))


def _work_id(work: Dict) -> str:
    """Короткий идентификатор работы вида ``W4417100132``."""
    raw = _clean((work.get("ids") or {}).get("openalex") or work.get("id"))
    return raw.rsplit("/", 1)[-1] if raw else ""


def _to_item(work: Dict) -> Dict:
    """Привести ответ OpenAlex к общему формату записи трекера."""
    published = _clean(work.get("publication_date"))[:10]
    if not published:
        # У части работ точной даты нет — известен только год.
        published = _clean(work.get("publication_year"))
    return {
        "source": SOURCE_ID,
        "identifier": f"{SOURCE_ID}:{_work_id(work)}",
        "title": _clean(work.get("title") or work.get("display_name")) or "(без названия)",
        "url": _work_url(work),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "authors": _authors(work),
        "published": published,
        "doi": _bare_doi(work.get("doi")),
        "venue": _venue(work),
        "cited_by_count": int(work.get("cited_by_count") or 0),
    }


def _client_params() -> Dict[str, str]:
    """Параметры клиента: ``mailto`` для «вежливого» пула и ``api_key``, если есть.

    Значения берутся из блока ``openalex`` в config.yaml, а если там пусто —
    из переменных окружения ``OPENALEX_MAILTO`` и ``OPENALEX_API_KEY``.
    Секреты в конфигурационный файл лучше не записывать.
    """
    cfg = load_config().get("openalex") or {}
    mailto = _clean(cfg.get("mailto") or os.getenv("OPENALEX_MAILTO"))
    api_key = _clean(cfg.get("api_key") or os.getenv("OPENALEX_API_KEY"))
    params: Dict[str, str] = {}
    if mailto:
        params["mailto"] = mailto
    if api_key:
        params["api_key"] = api_key
    return params


def _error_message(resp: requests.Response) -> str:
    """Текст ошибки из ответа API, если он там есть."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    return _clean(data.get("message") or data.get("error") or "")[:200]


def _api_get(params: Dict) -> Dict:
    """Запрос к API с повторами при 429 и 5xx.

    Без ключа OpenAlex может ответить 429 (исчерпан общий дневной бюджет) —
    тогда ждём и пробуем ещё раз, всего три попытки.
    """
    for attempt in range(3):
        resp = requests.get(
            OPENALEX_API_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 2.0 * (attempt + 1)
            logger.warning(
                f"⚠️ OpenAlex: HTTP {resp.status_code} {_error_message(resp)} – "
                f"повтор через {wait:.0f} с"
            )
            if attempt == 2:
                break
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            logger.error(f"❗ OpenAlex: HTTP {resp.status_code} {_error_message(resp)}")
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("OpenAlex не ответил после трёх попыток")


def _search(params: Dict, limit: int) -> List[Dict]:
    """Забрать до ``limit`` записей, листая API курсором."""
    items: List[Dict] = []
    cursor: Optional[str] = "*"
    total: Optional[int] = None
    while cursor and len(items) < limit:
        page = dict(params, per_page=min(PER_PAGE_MAX, limit - len(items)), cursor=cursor)
        data = _api_get(page)
        meta = data.get("meta") or {}
        if total is None:
            try:
                total = int(meta.get("count") or 0)
            except (TypeError, ValueError):
                total = 0
            logger.info(f"📥 OpenAlex: всего совпадений {total}")
        batch = [_to_item(work) for work in data.get("results") or []]
        batch = [item for item in batch if item["url"] and item["identifier"] != f"{SOURCE_ID}:"]
        if not batch:
            break
        items.extend(batch)
        cursor = meta.get("next_cursor")
    return items[:limit]


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить работы OpenAlex по темам и диапазону дат публикации.

    OpenAlex сортирует выдачу по убыванию ``publication_date``, поэтому
    возвращаются самые свежие записи диапазона. В выборку не попадают
    работы, у которых в источнике нет точной даты публикации (только год):
    фильтр по дате их не находит.
    """
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    query = build_query(topics, date_from, date_to)
    if not query:
        logger.warning("Темы для OpenAlex пустые – поиск не выполняется.")
        return []
    params = {"filter": query, "select": SELECT_FIELDS, "sort": "publication_date:desc"}
    params.update(_client_params())
    logger.info(f"🌐 Запрос к OpenAlex: {query} (до {rows} работ)")
    items = _search(params, rows)
    logger.info(f"📥 OpenAlex: отдано {len(items)}")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for record in fetch(load_config().get("topics") or ["medical multi-agent"], rows=5):
        print(record["identifier"], "|", record["published"], "|", record["title"][:80])
        print("   ", record["url"])
