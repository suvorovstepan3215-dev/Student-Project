# minzdrav_agent.py
"""Агент-источник: Рубрикатор клинических рекомендаций Минздрава России.

Официальный рубрикатор — https://cr.minzdrav.gov.ru/ — публикуется как
одобренный Научно-практическим советом Минздрава РФ каталог клинических
рекомендаций (КР). Публичного поискового API у него нет, сайт защищён от
автоматического обхода, поэтому агент работает с открытым зеркалом каталога
minzdrav.clinirec.ru, которое дублирует те же карточки КР.

Рубрикатор — это не лента новостей, а справочник: у каждой КР есть
идентификатор, версия, год пересмотра, коды МКБ-10, возрастная категория и
разработчик. Поэтому агент строит каталог из страниц классов МКБ-10
(https://minzdrav.clinirec.ru/mkb-10/<класс>/), где перечислены все КР
класса с компактными метаданными, и фильтрует его по теме (название
заболевания, синоним или код МКБ-10). Полный текст PDF выдаёт Telegram-бот,
поэтому в поле pdf_hint лежит подсказка, как его получить.

Поскольку это каталог, а не еженедельная лента, фильтр по дате здесь —
по году пересмотра: остаются КР, пересмотренные в пределах
[date_from.year, date_to.year]. Для поиска конкретной КР обычно имеет
смысл расширить диапазон дат (в интерфейсе — «Произвольный» на несколько
лет), иначе при узком окне «неделя» вернутся только свежие пересмотры.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published
и добавляет icd_codes, age_category, revision_year, developer, status, pdf_hint.
"""

import logging
import re
from datetime import date
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from utils import load_config, resolve_date_range

logger = logging.getLogger("archiv_tracker.minzdrav")

SOURCE_ID = "minzdrav"
SOURCE_NAME = "Рубрикатор КР Минздрава РФ"

# Зеркало рубрикатора: открытый сайт, дублирующий карточки КР Минздрава.
# Адрес можно переопределить в config.yaml (блок minzdrav.base_url).
DEFAULT_BASE_URL = "https://minzdrav.clinirec.ru"

# Классы МКБ-10, по которым рубрикатор группирует КР (римские номера страниц).
# Класс XX (внешние причины) в навигации сайта пропущен, но страница существует —
# агент молча пропустит 404.
CLASS_SLUGS = [
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx", "xxi",
]

# Значения HTTP-заголовков кодируются latin-1, поэтому здесь только ASCII.
USER_AGENT = "Mozilla/5.0 (compatible; multi-agent-tracker/1.0; educational project)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

# Возрастные категории КР. Порядок важен: составные — раньше простых.
AGE_CATEGORIES = (
    "Взрослые, дети", "Взрослые", "Дети",
    "Грудные", "Новорожденные", "Беременные",
)

# Пауза между запросами страниц классов: не частим к чужому сайту.
REQUEST_PAUSE_SECONDS = 0.5

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_CODES_RE = re.compile(r"\s*([A-Z0-9][A-Z0-9.,\s]*)")

# Кэш каталога между вызовами fetch в рамках одного процесса.
_CATALOG_CACHE: Optional[Dict[str, Dict]] = None


def _base_url() -> str:
    """Базовый адрес рубрикатора из config.yaml или по умолчанию."""
    cfg = load_config().get("minzdrav") or {}
    base = str(cfg.get("base_url") or DEFAULT_BASE_URL).strip()
    if not base:
        base = DEFAULT_BASE_URL
    return base.rstrip("/")


def _http_get(url: str) -> str:
    """Загрузить HTML страницы; при ошибке — пустая строка (страница пропускается)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.content.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"⚠️ Минздрав: не удалось загрузить {url} ({exc})")
        return ""


def _class_page_urls() -> List[str]:
    """Абсолютные URL страниц всех классов МКБ-10."""
    base = _base_url()
    return [f"{base}/mkb-10/{slug}/" for slug in CLASS_SLUGS]


def _abs_url(href: str) -> str:
    """Сделать ссылку абсолютной относительно базы рубрикатора."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return _base_url() + (href if href.startswith("/") else "/" + href)


def _parse_entry(text: str, href: str, slug: str) -> Optional[Dict]:
    """Разобрать текст ссылки КР: «Название МКБ-10: КОДЫ ВОЗРАСТ ГОД»."""
    if "МКБ-10:" not in text:
        return None
    title, _, meta = text.partition("МКБ-10:")
    title = title.strip()
    if not title:
        return None

    codes = ""
    m = _CODES_RE.match(meta)
    if m:
        codes = m.group(1).strip(" ,")

    year_match = _YEAR_RE.search(meta)
    year = int(year_match.group(0)) if year_match else None

    age = ""
    for token in AGE_CATEGORIES:
        if token in meta:
            age = token
            break

    return {
        "slug": slug,
        "title": title,
        "codes": codes,
        "age": age,
        "year": year,
        "url": _abs_url(href),
    }


def _parse_class_page(html: str) -> List[Dict]:
    """Извлечь записи КР со страницы класса МКБ-10 (по ссылкам /kr/)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries: List[Dict] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/kr/" not in href:
            continue
        slug = href.strip("/").split("/kr/")[-1].strip("/").rstrip("/")
        if not slug or slug in seen:
            continue
        text = a.get_text(" ").strip()
        entry = _parse_entry(text, href, slug)
        if entry:
            seen.add(slug)
            entries.append(entry)
    return entries


def build_catalog(pages: Optional[Dict[str, str]] = None) -> Dict[str, Dict]:
    """Построить каталог КР: slug → запись.

    ``pages`` — необязательный словарь {url: html} для офлайн-тестов; если не
    задан, страницы классов загружаются по сети. Записи с одинаковым slug из
    разных классов склеиваются (оставляем первую встреченную).
    """
    if pages is None:
        pages = {url: _http_get(url) for url in _class_page_urls()}
    catalog: Dict[str, Dict] = {}
    for html in pages.values():
        for entry in _parse_class_page(html):
            catalog.setdefault(entry["slug"], entry)
    logger.info(f"📚 Минздрав: в каталоге {len(catalog)} клинических рекомендаций")
    return catalog


def get_catalog(force: bool = False, pages: Optional[Dict[str, str]] = None) -> Dict[str, Dict]:
    """Каталог с простым кэшем в рамках процесса."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None or force or pages is not None:
        _CATALOG_CACHE = build_catalog(pages=pages)
    return _CATALOG_CACHE


def _topic_matches(text: str, topic: str) -> bool:
    """Тема есть в тексте: все слова темы (длиной >1) встречаются как подстроки.

    Для русского и латинских кодов МКБ-10 подстроковое совпадение достаточно:
    «астма» находит «Бронхиальная астма», «J45» — в списке кодов.
    """
    text = text.lower()
    tokens = [t for t in topic.lower().split() if len(t) > 1]
    if not tokens:
        return topic.lower() in text
    return all(token in text for token in tokens)


def _to_item(entry: Dict) -> Dict:
    """Привести запись каталога к общему формату записи трекера."""
    year = entry["year"]
    published = f"{year}-01-01" if year else ""
    codes = entry["codes"]
    age = entry["age"]
    abstract = f"Клиническая рекомендация Минздрава РФ: {entry['title']}."
    if codes:
        abstract += f" МКБ-10: {codes}."
    if age:
        abstract += f" Возраст: {age}."
    if year:
        abstract += f" Пересмотр {year}."
    return {
        "source": SOURCE_ID,
        "identifier": f"{SOURCE_ID}:{entry['slug']}",
        "title": entry["title"],
        "url": entry["url"],
        "abstract": abstract,
        "authors": "",
        "published": published,
        "icd_codes": codes,
        "age_category": age,
        "revision_year": year if year else "",
        "developer": "",
        "status": "Клиническая рекомендация Минздрава РФ",
        "pdf_hint": "Полный текст — в Telegram-боте @clinirecbot (по названию или коду МКБ-10)",
    }


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить КР Минздрава по темам и диапазону лет пересмотра.

    Строит каталог КР (страницы классов МКБ-10), оставляет записи, где все
    слова темы встречаются в названии, кодах МКБ-10 или возрасте. Фильтр по
    году пересмотра включается только когда запрошенный диапазон охватывает
    несколько лет (пользователь выбрал «Произвольный» на широкий период):
    рубрикатор — справочник с точностью до года, и при узком окне «неделя»/
    «месяц» жёсткий отсев по году сделал бы демо по умолчанию пустым.
    Записи возвращаются по убыванию года пересмотра, не больше ``rows`` штук.
    """
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    wanted = [topic.strip() for topic in topics if topic and topic.strip()]
    if not wanted:
        logger.warning("Темы для Минздрава пустые – поиск не выполняется.")
        return []
    catalog = get_catalog()
    if not catalog:
        logger.warning("Каталог Минздрава пуст – поиск не выполняется.")
        return []

    # Годовой фильтр только при пересечении границы лет (широкий диапазон).
    cross_year = date_to.year > date_from.year

    by_year = sorted(
        catalog.values(),
        key=lambda e: (e["year"] or 0),
        reverse=True,
    )
    matched: List[Dict] = []
    for entry in by_year:
        year = entry["year"]
        if cross_year and year is not None and (year < date_from.year or year > date_to.year):
            continue
        searchable = f"{entry['title']} {entry['codes']} {entry['age']}"
        if any(_topic_matches(searchable, topic) for topic in wanted):
            matched.append(_to_item(entry))

    matched.sort(key=lambda item: item["published"], reverse=True)
    if len(matched) > rows:
        logger.info(f"📥 Минздрав: найдено {len(matched)}, отдано последних {rows}")
        matched = matched[:rows]
    else:
        logger.info(f"📥 Минздрав: отдано {len(matched)}")
    return matched


if __name__ == "__main__":
    # Логгер настраивать не нужно: это делает utils.py.
    for record in fetch(load_config().get("topics") or ["астма"], rows=5):
        print(record["identifier"], "|", record["revision_year"], "|", record["title"][:80])
        print("   ", record["icd_codes"] or "—", "|", record["url"])
