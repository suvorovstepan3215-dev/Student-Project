# europepmc_agent.py
"""Агент-источник: Europe PMC.

Europe PMC — открытая биомедицинская база (EBI): PubMed, PubMed Central,
препринты (bioRxiv/medRxiv через SRC:PPR), патенты и диссертации с
метаданными, аннотациями и ссылками на открытые полные тексты. Ключ API
не нужен, ответ — JSON, поэтому агент устроен так же, как OpenAlex.

Поиск идёт через REST API https://www.ebi.ac.uk/europepmc/webservices/rest/search
с ``resultType=core``: в ответе есть аннотация, авторы, журнал, DOI, даты,
список полных текстов и число цитирований. Запрос — темы фразами через OR
и диапазон дат ``FIRST_PDATE`` (дата первой публикации); границы
включительные, как и у других источников проекта.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published
и добавляет doi, venue, cited_by_count, keywords, pmid, pmcid.
"""

import html
import logging
import os
import re
import time
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import load_config, resolve_date_range

logger = logging.getLogger("archiv_tracker.europepmc")

SOURCE_ID = "europepmc"
SOURCE_NAME = "Europe PMC"
EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC отдаёт не больше 1000 записей за один запрос.
PAGE_SIZE_MAX = 1000

USER_AGENT = "multi-agent-publication-tracker/1.0 (MAS course project)"

# Теги разметки в заголовках и аннотациях Europe PMC (<i>, <sub>, <sup>…).
_TAG = re.compile(r"<[^>]+>")


def _clean(text) -> str:
    """Убрать HTML-теги и лишние пробелы из полей Europe PMC."""
    if isinstance(text, list):
        text = " ".join(map(str, text))
    text = _TAG.sub(" ", str(text or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    # Тег мог оставить пробел перед знаком препинания: «MedScrubCrew : …».
    text = re.sub(r"\s+([:;,.!?%\)\]])", r"\1", text)
    return text.strip()


def _search_value(topic: str) -> str:
    """Тема как фраза для запроса Europe PMC.

    Кавычки внутри темы убираем, чтобы фраза не распалась: Europe PMC ищет
    фразу целиком, как archive.org и Habr.
    """
    cleaned = str(topic or "").replace('"', " ").strip()
    return re.sub(r"\s+", " ", cleaned)


def build_text_query(topics: List[str]) -> str:
    """Темы фразами через OR: ``("medical multi-agent" OR "clinical AI")``."""
    values = [_search_value(topic) for topic in topics]
    phrases = [f'"{value}"' for value in values if value]
    return "(" + " OR ".join(phrases) + ")" if phrases else ""


def build_query(topics: List[str], date_from: date, date_to: date) -> str:
    """Собрать запрос: темы и диапазон дат первой публикации.

    ``FIRST_PDATE:[ГГГГ-ММ-ДД TO ГГГГ-ММ-ДД]`` — дата первой публикации
    записи; границы включительные, как и у других источников проекта.
    """
    text_query = build_text_query(topics)
    if not text_query:
        return ""
    return (
        f"{text_query} AND "
        f"FIRST_PDATE:[{date_from.isoformat()} TO {date_to.isoformat()}]"
    )


def _published(record: Dict) -> str:
    """Дата публикации: первая публикация, затем электронная, затем год."""
    for key in ("firstPublicationDate", "electronicPublicationDate"):
        value = _clean(record.get(key))[:10]
        if value:
            return value
    journal_info = record.get("journalInfo") or {}
    value = _clean(journal_info.get("printPublicationDate"))[:10]
    if value:
        return value
    return _clean(record.get("pubYear"))


def _venue(record: Dict) -> str:
    """Где опубликована работа: название журнала."""
    journal = (record.get("journalInfo") or {}).get("journal") or {}
    return _clean(journal.get("title") or journal.get("medlineAbbreviation"))


def _keywords(record: Dict) -> str:
    """Ключевые слова записи через запятую."""
    keywords = (record.get("keywordList") or {}).get("keyword") or []
    return ", ".join(_clean(word) for word in keywords if _clean(word))


def _article_url(record: Dict) -> str:
    """Ссылка на запись в Europe PMC.

    Сначала страница статьи по PMCID, затем карточка PubMed по PMID,
    затем DOI. Страница Europe PMC открыта и читается оркестратором,
    если аннотации в метаданных не хватило для резюме.
    """
    pmcid = _clean(record.get("pmcid"))
    pmid = _clean(record.get("pmid"))
    if pmcid:
        return f"https://europepmc.org/articles/{pmcid}"
    if pmid:
        return f"https://europepmc.org/article/MED/{pmid}"
    doi = _clean(record.get("doi"))
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def _to_item(record: Dict) -> Dict:
    """Привести ответ Europe PMC к общему формату записи трекера."""
    pmcid = _clean(record.get("pmcid"))
    pmid = _clean(record.get("pmid"))
    short_id = pmcid or pmid or _clean(record.get("id"))
    try:
        cited = int(record.get("citedByCount") or 0)
    except (TypeError, ValueError):
        cited = 0
    return {
        "source": SOURCE_ID,
        "identifier": f"{SOURCE_ID}:{short_id}" if short_id else SOURCE_ID,
        "title": _clean(record.get("title")) or "(без названия)",
        "url": _article_url(record),
        "abstract": _clean(record.get("abstractText")),
        "authors": _clean(record.get("authorString")),
        "published": _published(record),
        "doi": _clean(record.get("doi")),
        "venue": _venue(record),
        "cited_by_count": cited,
        "keywords": _keywords(record),
        "pmid": pmid,
        "pmcid": pmcid,
    }


def _client_params() -> Dict[str, str]:
    """Параметры клиента: ``email`` для «вежливого» пула, если задан.

    Значение берётся из блока ``europepmc`` в config.yaml, а если там пусто —
    из переменной окружения ``EUROPEPMC_EMAIL``. Без email запросы тоже
    работают, но идут в общий лимит на IP-адрес.
    """
    cfg = load_config().get("europepmc") or {}
    email = _clean(cfg.get("email") or os.getenv("EUROPEPMC_EMAIL"))
    return {"email": email} if email else {}


def _error_message(resp: requests.Response) -> str:
    """Текст ошибки из ответа API, если он там есть."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        return _clean(data.get("message") or data.get("error") or "")[:200]
    return ""


def _api_get(params: Dict) -> Dict:
    """Запрос к API с повторами при 429 и 5xx (всего три попытки)."""
    for attempt in range(3):
        resp = requests.get(
            EUROPEPMC_API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 2.0 * (attempt + 1)
            logger.warning(
                f"⚠️ Europe PMC: HTTP {resp.status_code} {_error_message(resp)} – "
                f"повтор через {wait:.0f} с"
            )
            if attempt == 2:
                break
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            logger.error(f"❗ Europe PMC: HTTP {resp.status_code} {_error_message(resp)}")
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Europe PMC не ответил после трёх попыток")


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить работы Europe PMC по темам и диапазону дат.

    Europe PMC не гарантирует сортировку по дате, поэтому записи сортируются
    по убыванию ``published`` на стороне агента, как у Habr. Записи без даты
    отбрасываются: их нельзя отнести к диапазону.
    """
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    query = build_query(topics, date_from, date_to)
    if not query:
        logger.warning("Темы для Europe PMC пустые – поиск не выполняется.")
        return []
    limit = max(1, min(int(rows or 20), PAGE_SIZE_MAX))
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": limit,
    }
    params.update(_client_params())
    logger.info(f"🌐 Запрос к Europe PMC: {query} (до {limit} работ)")
    data = _api_get(params)
    logger.info(f"📥 Europe PMC: всего совпадений {data.get('hitCount', '?')}")
    records = (data.get("resultList") or {}).get("result") or []
    items = [_to_item(record) for record in records]
    items = [item for item in items if item["url"] and item["published"]]
    items.sort(key=lambda item: item["published"], reverse=True)
    items = items[:limit]
    logger.info(f"📥 Europe PMC: отдано {len(items)}")
    return items


if __name__ == "__main__":
    # Логгер настраивать не нужно: это делает utils.py, иначе строки дублируются.
    for record in fetch(load_config().get("topics") or ["medical multi-agent"], rows=5):
        print(record["identifier"], "|", record["published"], "|", record["title"][:80])
        print("   ", record["url"])
