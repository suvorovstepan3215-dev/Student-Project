"""Configuration, bounded text extraction, dates and atomic local history."""

import calendar
import json
import logging
import os
import re
import tempfile
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from bs4 import BeautifulSoup
from filelock import FileLock, Timeout

from network import public_requests as requests
from security import normalize_model_base, redact_text

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DATA_DIR = Path(os.environ.get("TRACKER_DATA_DIR", str(BASE_DIR)))
HISTORY_PATH = DATA_DIR / "history.json"
LOG_PATH = DATA_DIR / "main.log"


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_text(super().format(record))


logger = logging.getLogger("archiv_tracker")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = RedactingFormatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    handler = logging.StreamHandler()  # stderr: stdout belongs to MCP JSON-RPC.
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Файловый журнал недоступен; используется stderr.")


def load_config() -> Dict:
    if not CONFIG_PATH.is_file():
        return {}
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if not isinstance(config, dict):
        raise ValueError("Корень config.yaml должен быть словарём.")
    return config


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_model_settings(engine: str, providers: Dict) -> None:
    """Local administration only. The public UI never calls this function."""
    if engine not in {"sumy", "ollama", "openai"} or not isinstance(providers, dict):
        raise ValueError("Некорректный провайдер.")
    public = {}
    for name, entry in providers.items():
        if name not in {"ollama", "openai"} or not isinstance(entry, dict):
            raise ValueError("Некорректные настройки провайдера.")
        public[name] = {
            key: entry[key] for key in ("base_url", "model", "api_key_env", "allow_private") if key in entry
        }
        if entry.get("base_url"):
            public[name]["base_url"] = normalize_model_base(entry["base_url"])
    config = load_config()
    config.update(summarizer=engine, providers=public)
    config.pop("llm", None)  # Avoid two competing copies of a provider preset.
    _atomic_write(CONFIG_PATH, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))


def _subtract_months(day: date, months: int) -> date:
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def resolve_date_range(
    preset: str = "week",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    today: Optional[date] = None,
) -> Tuple[date, date]:
    """Inclusive bounds. A single supplied bound is preserved, never ignored."""
    today = today or date.today()
    end = date_to if date_to is not None else today
    if date_from is not None:
        start = date_from
    elif preset == "month":
        start = _subtract_months(end, 1)
    elif preset == "week":
        start = end - timedelta(days=6)  # Seven calendar days including end.
    else:
        raise ValueError("Неизвестный диапазон дат.")
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError("Дата «С» должна быть не позже даты «По».")
    return start, end


def topic_tokens(topic: str) -> List[str]:
    return [part for part in re.split(r"[\s\-]+", topic.strip()) if part]


def token_is_exact(token: str) -> bool:
    letters = "".join(ch for ch in token if ch.isalpha())
    return bool(letters) and letters.isupper() and len(letters) <= 5


_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def normalize_doi(value) -> str:
    """Нормализовать DOI: нижний регистр, без префикса URL/«doi:», без хвостовой пунктуации."""
    text = str(value or "").strip().lower()
    text = _DOI_PREFIX_RE.sub("", text)
    return text.rstrip("./;,")


def duplicate_doi_map(items) -> Dict[str, List[str]]:
    """Карта «нормализованный DOI -> идентификаторы» только для DOI, встретившихся более одного раза."""
    groups: Dict[str, List[str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        doi = normalize_doi(item.get("doi"))
        identifier = str(item.get("identifier") or "")
        if not doi or not identifier:
            continue
        ids = groups.setdefault(doi, [])
        if identifier not in ids:
            ids.append(identifier)
    return {doi: ids for doi, ids in groups.items() if len(ids) > 1}


def _cited_value(item: Dict) -> int:
    try:
        return int(item.get("cited_by_count") or 0)
    except (TypeError, ValueError):
        return 0


def sort_items(items, order: str = "date") -> List[Dict]:
    """Стабильная сортировка выдачи: по дате (новые сверху) или по цитируемости."""
    data = list(items or [])
    if order == "cited":
        return sorted(data, key=lambda i: (_cited_value(i), str(i.get("published") or "")), reverse=True)
    return sorted(data, key=lambda i: str(i.get("published") or ""), reverse=True)


def filter_by_min_citations(items, minimum) -> List[Dict]:
    """Оставить записи не ниже порога цитируемости; источники без метрики (Минздрав) не скрываются."""
    try:
        floor = max(0, int(minimum))
    except (TypeError, ValueError):
        floor = 0
    out: List[Dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cited = item.get("cited_by_count")
        if cited is None:
            out.append(item)
            continue
        try:
            if int(cited) >= floor:
                out.append(item)
        except (TypeError, ValueError):
            out.append(item)
    return out


def extract_text_from_url(url: str) -> str:
    """Read only bounded HTML/text from a public address, without navigation."""
    try:
        response = requests.get(url, timeout=15, max_bytes=2 * 1024 * 1024)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
            logger.warning("Источник вернул не HTML/текст; извлечение пропущено.")
            return ""
        if "text/plain" in content_type:
            return response.text.strip()[:50000]
        soup = BeautifulSoup(response.content, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "aside"]):
            tag.decompose()
        body = soup.find("article") or soup.find("main") or soup.body or soup
        return " ".join(body.stripped_strings)[:50000]
    except Exception as exc:
        logger.warning("Извлечение текста не удалось (%s).", type(exc).__name__)
        return ""


def _read_history() -> List[str]:
    if not HISTORY_PATH.is_file():
        return []
    try:
        if HISTORY_PATH.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("history too large")
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        logger.warning("История недоступна или повреждена; используется пустая история.")
        return []


def get_history() -> List[str]:
    """Reading absent history does not create a file (including in read-only deployments)."""
    try:
        if not HISTORY_PATH.exists():
            return []
        with FileLock(str(HISTORY_PATH) + ".lock", timeout=5):
            return _read_history()
    except (OSError, Timeout):
        logger.warning("Не удалось прочитать историю.")
        return []


def update_history(new_ids: List[str]) -> bool:
    """Cross-process lock + atomic replace; only successfully processed IDs belong here."""
    try:
        maximum = load_config().get("history_size", 1000)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100000:
            raise ValueError("Некорректный history_size.")
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(HISTORY_PATH) + ".lock", timeout=5):
            # Repeated IDs are moved to the end so the newest successful use is retained.
            combined = _read_history() + [item for item in new_ids if isinstance(item, str)]
            newest = list(dict.fromkeys(reversed(combined)))[:maximum]
            _atomic_write(HISTORY_PATH, json.dumps(list(reversed(newest)), ensure_ascii=False, indent=2))
        return True
    except (OSError, ValueError, Timeout):
        logger.warning("История не сохранена; результаты поиска остаются доступны.")
        return False
