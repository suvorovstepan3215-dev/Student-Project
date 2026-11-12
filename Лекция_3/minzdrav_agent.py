"""Clinical recommendation catalogue: official API first, explicit mirror fallback.

API schema: GetJsonClinrecsFilterV2 (Data / TotalRecords / CodeVersion).
The mirror is THIRD-PARTY metadata, not a verified copy or a full guideline.
No PDF/bot/download link is executed. Publication dates from the API and
revision YEARS from the mirror retain their original precision.
"""

import logging
import re
import threading
import time
from collections import deque
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from network import public_requests as requests
from results import SearchResults, SourceUnavailable, diagnostic
from security import normalize_url, same_origin
from utils import load_config, resolve_date_range
from validation import bounded_int, clean_topics
from validator import _find_topic

logger = logging.getLogger("archiv_tracker.minzdrav")
SOURCE_ID = "minzdrav"
SOURCE_NAME = "Рубрикатор КР Минздрава РФ (каталог)"
OFFICIAL_API_URL = "https://apicr.minzdrav.gov.ru/api.ashx"
DEFAULT_BASE_URL = "https://minzdrav.clinirec.ru"
CLASS_SLUGS = [
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
    "xxi",
]
USER_AGENT = "multi-agent-publication-tracker/1.1 (educational metadata search)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json"}
AGE_CATEGORIES = ("Взрослые, дети", "Взрослые", "Дети", "Грудные", "Новорожденные", "Беременные")
REQUEST_PAUSE_SECONDS = 0.5
MAX_CATALOG_SECONDS = 90
MAX_MIRROR_PAGES = 80
MAX_OFFICIAL_PAGES = 20
OFFICIAL_PAGE_SIZE = 200
MAX_ALIAS_LENGTH = 200
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_CODE_RE = re.compile(r"[A-Z]\d{2}(?:\.[A-Z0-9]{1,4})?(?:\s*[-–]\s*[A-Z]?\d{2}(?:\.[A-Z0-9]{1,4})?)?")
_CATALOG_CACHE = {}
_CATALOG_LOCK = threading.Lock()


class Catalog(dict):
    def __init__(self, *args, errors=(), warnings=(), complete=True, backend="mirror", **kwargs):
        super().__init__(*args, **kwargs)
        self.errors = list(errors)
        self.warnings = list(warnings)
        self.complete = complete
        self.backend = backend


def _parse_topic_aliases(raw) -> dict:
    """Опциональное расширение лексического поиска карточек КР.

    Словарь «тема: [алиасы]» из конфигурации сервера: алиасы — слова и
    коды МКБ-10 (например, ``"E10"``), которые могут встречаться в
    заголовке/кодах карточки, но отсутствуют в самой теме. Поиск по КР
    всё равно остаётся лексическим — это не клинический фактчекинг.
    Ключи словаря сравниваются по casefold.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("minzdrav.topic_aliases должен быть словарём «тема: [алиасы]».")
    aliases: dict[str, list[str]] = {}
    for topic, values in raw.items():
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Ключи minzdrav.topic_aliases должны быть непустыми строками.")
        if not isinstance(values, list) or not values:
            raise ValueError(f"minzdrav.topic_aliases[{topic!r}]: нужен непустой список алиасов.")
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"minzdrav.topic_aliases[{topic!r}]: алиасы должны быть строками.")
            alias = value.strip()
            if not alias or len(alias) > MAX_ALIAS_LENGTH or any(ord(ch) < 32 for ch in alias):
                raise ValueError(f"minzdrav.topic_aliases[{topic!r}]: недопустимый алиас {value!r}.")
            if alias not in cleaned:
                cleaned.append(alias)
        aliases[topic.strip().casefold()] = cleaned
    return aliases


def _settings() -> dict:
    cfg = load_config().get("minzdrav") or {}
    mode = cfg.get("mode", "auto")
    if mode not in {"auto", "official", "mirror"}:
        raise ValueError("minzdrav.mode: допустимы auto, official, mirror.")
    ttl = bounded_int(cfg.get("cache_ttl_seconds", 900), "cache_ttl_seconds", 0, 3600)
    base = normalize_url(str(cfg.get("base_url") or DEFAULT_BASE_URL)).rstrip("/")
    if urlsplit(base).query or urlsplit(base).path:
        raise ValueError("minzdrav.base_url должен содержать только схему и имя узла.")
    return {
        "mode": mode,
        "base_url": base,
        "ttl": ttl,
        "topic_aliases": _parse_topic_aliases(cfg.get("topic_aliases")),
    }


def _expand_topics(topics: list[str], aliases: dict) -> tuple[list[str], list[str]]:
    """Темы + их алиасы (без дублей, порядок сохранён). Возвращает (все, применённые)."""
    expanded = list(topics)
    applied: list[str] = []
    seen = {topic.casefold() for topic in expanded}
    for topic in topics:
        for alias in aliases.get(topic.casefold(), []):
            if alias.casefold() in seen:
                continue
            seen.add(alias.casefold())
            expanded.append(alias)
            applied.append(alias)
    return expanded, applied


def _base_url() -> str:
    return _settings()["base_url"]


def _http_get(url: str, timeout: float = 15) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _class_page_urls(base: Optional[str] = None) -> list[str]:
    return [f"{base or _base_url()}/mkb-10/{slug}/" for slug in CLASS_SLUGS]


def _abs_url(href: str, base: Optional[str] = None) -> str:
    base = base or _base_url()
    target = normalize_url(urljoin(base + "/", href))
    if not same_origin(base, target):
        raise ValueError("Внешняя ссылка в каталоге КР пропущена.")
    return target


def _parse_entry(text: str, href: str, slug: str, base: Optional[str] = None) -> Optional[dict]:
    if "МКБ-10:" not in text:
        return None
    title, _, meta = text.partition("МКБ-10:")
    if not title.strip():
        return None
    years = _YEAR_RE.findall(meta)
    return {
        "slug": slug,
        "title": title.strip(),
        "codes": ", ".join(dict.fromkeys(_CODE_RE.findall(meta))),
        "age": next((age for age in AGE_CATEGORIES if age.casefold() in meta.casefold()), ""),
        "year": int(years[-1]) if years else None,
        "url": _abs_url(href, base),
        "backend": "mirror",
        "published": years[-1] if years else "",
        "date_precision": "year" if years else "unknown",
    }


def _parse_class_page(html: str, base: Optional[str] = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        path = urlsplit(anchor["href"]).path
        match = re.fullmatch(r"/kr/([^/]+)/?", path)
        if not match or match[1] in seen:
            continue
        try:
            entry = _parse_entry(anchor.get_text(" ", strip=True), anchor["href"], match[1], base)
        except ValueError:
            continue
        if entry:
            seen.add(match[1])
            entries.append(entry)
    return entries


def _pagination_links(html: str, current: str, base: str) -> list[str]:
    """Follow only same-origin catalogue pagination, never arbitrary page links."""
    links = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        try:
            target = _abs_url(urljoin(current, anchor["href"]), base)
        except ValueError:
            continue
        if (
            re.fullmatch(r"/mkb-10/[ivx]+/(?:page-\d+/?)?", urlsplit(target).path)
            and not urlsplit(target).query
        ):
            links.append(target)
    return list(dict.fromkeys(links))


def build_catalog(pages: Optional[dict[str, str]] = None, *, base: Optional[str] = None) -> Catalog:
    """Mirror catalogue with pagination, bounded traversal, pacing and diagnostics.

    pages is an explicit offline snapshot; it never populates the runtime cache.
    Empty/partial network snapshots are never cached as a successful catalogue.
    """
    base = base or _base_url()
    catalog = Catalog(backend="mirror")
    if pages is not None:
        for html in pages.values():
            for entry in _parse_class_page(html, base):
                catalog.setdefault(entry["slug"], entry)
        return catalog
    queue = deque(_class_page_urls(base))
    visited = set()
    deadline = time.monotonic() + MAX_CATALOG_SECONDS
    consecutive_errors = 0
    while queue:
        if len(visited) >= MAX_MIRROR_PAGES or time.monotonic() >= deadline:
            catalog.complete = False
            catalog.warnings.append(
                diagnostic(
                    SOURCE_ID,
                    "catalog_limit",
                    "Обход зеркала ограничен по времени/числу страниц; результаты неполные.",
                )
            )
            break
        url = queue.popleft()
        if url in visited:
            continue
        if visited:
            time.sleep(REQUEST_PAUSE_SECONDS)
        visited.add(url)
        try:
            html = _http_get(url, timeout=max(0.1, min(12, deadline - time.monotonic())))
            entries = _parse_class_page(html, base)
            if not entries:
                # A login/anti-bot/template change is not a valid empty search.
                raise SourceUnavailable("Не найдены карточки на странице класса.")
            for entry in entries:
                catalog.setdefault(entry["slug"], entry)
            for target in _pagination_links(html, url, base):
                if target not in visited and target not in queue:
                    queue.append(target)
            consecutive_errors = 0
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 404 and url.endswith("/mkb-10/xx/"):
                continue  # The mirror omits class XX on some versions.
            catalog.complete = False
            catalog.errors.append(
                diagnostic(
                    SOURCE_ID,
                    "mirror_page_failed",
                    f"Не удалось прочитать страницу каталога {urlsplit(url).path}; данные неполные.",
                )
            )
            consecutive_errors += 1
            logger.warning("Страница зеркала не прочитана (%s).", type(exc).__name__)
            if consecutive_errors >= 3:
                catalog.warnings.append(
                    diagnostic(
                        SOURCE_ID,
                        "mirror_unavailable",
                        "Обход остановлен после трёх последовательных ошибок. Повторите поиск позже.",
                    )
                )
                break
    if not catalog:
        catalog.complete = False
        if not catalog.errors:
            catalog.errors.append(
                diagnostic(SOURCE_ID, "empty_catalog", "Зеркало не вернуло распознаваемый каталог.")
            )
    return catalog


def _official_date(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value[:10], pattern).date().isoformat()
        except ValueError:
            continue
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", value)
    if match:
        try:
            return datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return ""


def _official_entry(record: dict) -> Optional[dict]:
    code = str(record.get("CodeVersion") or "")
    title = str(record.get("Name") or "").strip()
    if not re.fullmatch(r"\d+_\d+", code) or not title:
        return None  # Never fabricate an official identifier from a mirror slug.
    published = _official_date(record.get("PublishDateStr") or record.get("PublishDate"))
    codes = record.get("Mkbs") or []
    codes = (
        [str(item.get("MkbCode") or "") for item in codes if isinstance(item, dict)]
        if isinstance(codes, list)
        else []
    )
    return {
        "slug": code,
        "official_id": code,
        "title": title,
        "codes": ", ".join(code for code in codes if code),
        "age": str(record.get("AgeCategoryStr") or ""),
        "published": published,
        "year": int(published[:4]) if published else None,
        "date_precision": "day" if published else "unknown",
        "backend": "official_api",
        "url": f"https://cr.minzdrav.gov.ru/view-cr/{code}",
        "registry_status": record.get("Status"),
        "version": str(record.get("Version") or ""),
    }


def _official_catalog() -> Catalog:
    catalog = Catalog(backend="official_api")
    deadline = time.monotonic() + MAX_CATALOG_SECONDS
    for page in range(1, MAX_OFFICIAL_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_PAUSE_SECONDS)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceUnavailable("Превышено время чтения официального каталога.")
        response = requests.post(
            OFFICIAL_API_URL,
            params={"op": "GetJsonClinrecsFilterV2"},
            headers=HEADERS,
            json={
                "filters": [
                    {
                        "fieldName": "status",
                        "filterType": 1,
                        "filterValueType": 2,
                        "value1": 0,
                        "value2": "",
                        "values": [],
                    }
                ],
                "sortOption": {"fieldName": "publishdate", "sortType": 2},
                "pageSize": OFFICIAL_PAGE_SIZE,
                "currentPage": page,
                "useANDoperator": True,
                "columns": [],
            },
            timeout=min(12, remaining),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("Data"), list):
            raise SourceUnavailable("Изменился формат официального каталога.")
        records = payload["Data"]
        added = 0
        for record in records:
            entry = _official_entry(record) if isinstance(record, dict) else None
            if entry is None:
                catalog.complete = False
                continue
            if entry["slug"] not in catalog:
                catalog[entry["slug"]] = entry
                added += 1
        total = payload.get("TotalRecords")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise SourceUnavailable("Официальный API не сообщил корректный размер каталога.")
        if len(catalog) >= total and total > 0:
            if not catalog.complete:
                catalog.warnings.append(
                    diagnostic(
                        SOURCE_ID, "invalid_record", "Часть записей официального каталога не распознана."
                    )
                )
            return catalog
        if not records or not added:
            break
    catalog.complete = False
    if not catalog:
        raise SourceUnavailable("Официальный API вернул пустой или нераспознанный каталог.")
    catalog.warnings.append(
        diagnostic(
            SOURCE_ID,
            "official_catalog_incomplete",
            "Официальный каталог прочитан не полностью: достигнут лимит или часть записей не распознана.",
        )
    )
    return catalog


def get_catalog(force: bool = False, pages: Optional[dict[str, str]] = None) -> Catalog:
    settings = _settings()
    if pages is not None:
        return build_catalog(pages, base=settings["base_url"])
    key = (settings["mode"], settings["base_url"])
    with _CATALOG_LOCK:
        cached = _CATALOG_CACHE.get(key)
        if cached and not force and time.monotonic() < cached[0]:
            return cached[1]
        if settings["mode"] == "mirror":
            catalog = build_catalog(base=settings["base_url"])
        else:
            try:
                catalog = _official_catalog()
            except Exception as exc:
                logger.warning("Официальный каталог недоступен (%s).", type(exc).__name__)
                if settings["mode"] == "official":
                    raise SourceUnavailable(
                        "Официальный API Минздрава недоступен. Пустой результат не означает отсутствие КР."
                    ) from exc
                catalog = build_catalog(base=settings["base_url"])
                catalog.warnings.insert(
                    0,
                    diagnostic(
                        SOURCE_ID,
                        "official_unavailable",
                        "Официальный API недоступен: использованы только метаданные стороннего зеркала. Актуальность и соответствие официальным КР не проверены.",
                    ),
                )
        if catalog and catalog.complete and settings["ttl"]:
            ttl = (
                min(settings["ttl"], 30)
                if any(w["code"] == "official_unavailable" for w in catalog.warnings)
                else settings["ttl"]
            )
            _CATALOG_CACHE[key] = (time.monotonic() + ttl, catalog)
            # Bounded cache even if a local administrator frequently changes URLs.
            while len(_CATALOG_CACHE) > 8:
                _CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))
        else:
            _CATALOG_CACHE.pop(key, None)
        return catalog


def _topic_matches(text: str, topic: str) -> bool:
    return _find_topic(text, [topic]) is not None


def _to_item(entry: dict) -> dict:
    official = entry.get("backend") == "official_api"
    published = entry.get("published") or (str(entry["year"]) if entry.get("year") else "")
    description = f"Карточка каталога: {entry['title']}."
    if entry.get("codes"):
        description += f" МКБ-10: {entry['codes']}."
    if entry.get("age"):
        description += f" Возраст: {entry['age']}."
    if published:
        description += (
            f" Дата публикации в реестре: {published}."
            if official
            else f" Год пересмотра по данным зеркала: {published}."
        )
    return {
        "source": SOURCE_ID,
        "identifier": f"minzdrav:{'official:' if official else ''}{entry['slug']}",
        "title": entry["title"],
        "url": entry["url"],
        "abstract": description,
        "authors": "",
        "published": published,
        "date_precision": entry.get("date_precision", "year" if published else "unknown"),
        "date_kind": "registry_publication" if official else "mirror_revision_year",
        "icd_codes": entry.get("codes", ""),
        "age_category": entry.get("age", ""),
        "revision_year": "" if official else entry.get("year") or "",
        "official_id": entry.get("official_id", ""),
        "version": entry.get("version", ""),
        "registry_status": entry.get("registry_status"),
        "content_kind": "metadata_only",
        "provenance": "official_api" if official else "third_party_mirror",
        "full_text_available": False,
        "currency_verified": False,
        "provenance_notice": (
            "Метаданные официального API. Полный текст не загружен; это не резюме клинической рекомендации."
            if official
            else "Стороннее зеркало minzdrav.clinirec.ru. Полный текст, официальный ID и актуальность КР не проверены."
        ),
    }


def fetch(
    topics: list[str], rows: int = 20, date_from: Optional[date] = None, date_to: Optional[date] = None
) -> SearchResults:
    topics = clean_topics(topics)
    bounded_int(rows, "rows", 1, 100)
    start, end = resolve_date_range(date_from=date_from, date_to=date_to)
    search_topics, applied_aliases = _expand_topics(topics, _settings().get("topic_aliases") or {})
    catalog = get_catalog()
    result = SearchResults(errors=getattr(catalog, "errors", []), warnings=getattr(catalog, "warnings", []))
    if applied_aliases:
        result.warnings.append(
            diagnostic(
                SOURCE_ID,
                "topic_aliases",
                "К поиску по КР применены алиасы тем (слова/коды МКБ-10): "
                + ", ".join(applied_aliases)
                + ".",
            )
        )
    if getattr(catalog, "backend", "mirror") == "mirror":
        result.warnings.append(
            diagnostic(
                SOURCE_ID,
                "year_precision",
                "В зеркале известен только год: фильтр включает ВСЕ КР с годом в заданных границах, даже для окна «неделя». День публикации неизвестен.",
            )
        )
        result.warnings.append(
            diagnostic(
                SOURCE_ID,
                "mirror_metadata_only",
                "Использованы метаданные стороннего зеркала, не полный текст и не проверка актуальности КР.",
            )
        )
    unknown_dates = 0
    for entry in catalog.values():
        if not any(
            _topic_matches(f"{entry['title']} {entry.get('codes', '')} {entry.get('age', '')}", topic)
            for topic in search_topics
        ):
            continue
        if entry.get("backend") == "official_api":
            published = entry.get("published", "")
            if not published:
                unknown_dates += 1
                continue
            if not start.isoformat() <= published <= end.isoformat():
                continue
        else:
            year = entry.get("year")
            if year is None:
                unknown_dates += 1
                continue
            if not start.year <= year <= end.year:
                continue
        result.append(_to_item(entry))
    if unknown_dates:
        result.warnings.append(
            diagnostic(
                SOURCE_ID,
                "unknown_dates",
                f"Пропущено подходящих карточек без известной даты/года: {unknown_dates}.",
            )
        )
    result.sort(key=lambda item: (item["published"], item["identifier"]), reverse=True)
    del result[rows:]
    return result


if __name__ == "__main__":
    found = fetch(load_config().get("topics") or ["астма"], rows=5)
    for record in found:
        print(record["identifier"], "|", record["published"], "|", record["title"])
    for notice in found.errors + found.warnings:
        print(notice["message"])
