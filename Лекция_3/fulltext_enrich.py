"""Экспериментальная догрузка аннотации из открытого полного текста (НЕОБЯЗАТЕЛЬНО).

Почему отдельный модуль и отдельный этап: главный поиск его не импортирует и
не знает о его существовании. Вызывается ТОЛЬКО из веб-интерфейса, ТОЛЬКО по
явной галочке и ТОЛЬКО после того, как поиск уже завершён и результаты уже
готовы. Любой сбой (сеть, XML, таймаут) оставляет обычные результаты нетронутыми:
каждая запись обрабатывается отдельно, ошибка догрузки — не ошибка поиска.

Что делает: для записей Europe PMC с пустой аннотацией ("No abstract
provided") берёт <abstract> из JATS XML открытого полного текста
(/rest/{source}/{id}/fullTextXML; сначала PMC/{pmcid}, запасной вариант
MED/{pmid}) и заново строит резюме. Догруженная аннотация помечается
``abstract_origin="fulltext_xml"``; результат показывается той же карточкой.

Ограничения: догружается не больше MAX_PER_RUN записей за один запуск;
текст аннотации — не более MAX_ABSTRACT_CHARS; DOCTYPE вырезается до разбора
(внутренние сущности не раскрываются); внешние сущности ElementTree не
обрабатывает. Полнотекстовое лицензирование статей разное (CC-BY и CC-BY-NC);
мы читаем аннотацию и строим локальное резюме, не перераспространяя текст.
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from network import public_requests as requests
from summarizer import summarize_with_details
from validation import MAX_TEXT_LENGTH

logger = logging.getLogger("archiv_tracker.fulltext_enrich")

USER_AGENT = "multi-agent-publication-tracker/1.0 (MAS course project)"
FULLTEXT_XML_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{record_id}/fullTextXML"
MAX_XML_BYTES = 5 * 1024 * 1024
MAX_ABSTRACT_CHARS = 15000
MAX_PER_RUN = 10
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^\[]*(\[[\s\S]*?\])[^>]*>|<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def extract_abstract(xml_text: str) -> str:
    """Вырезать текст элемента <abstract> из JATS XML полного текста."""
    if not xml_text:
        return ""
    cleaned = _DOCTYPE_RE.sub("", xml_text)[:MAX_XML_BYTES]
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return ""
    for node in root.iter():
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag.rsplit("}", 1)[-1].lower() == "abstract":
            joined = " ".join("".join(node.itertext()).split())
            joined = _TAG.sub(" ", joined)
            joined = html.unescape(joined)
            joined = re.sub(r"\s+", " ", joined).strip()
            return joined[:MAX_ABSTRACT_CHARS]
    return ""


def fetch_abstract_for_record(*, pmcid: str = "", pmid: str = "") -> str:
    """Догрузить аннотацию из fullTextXML. Ошибки сети/HTTP всплывают исключением."""
    pmcid, pmid = str(pmcid or "").strip(), str(pmid or "").strip()
    candidates = [
        FULLTEXT_XML_URL.format(source=source, record_id=quote(identifier, safe=""))
        for source, identifier in (("PMC", pmcid), ("MED", pmid))
        if identifier
    ]
    for url in candidates:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, max_bytes=MAX_XML_BYTES)
        resp.raise_for_status()
        content_type = (resp.headers or {}).get("Content-Type", "").lower()
        if "html" in content_type:
            continue
        body = resp.text if isinstance(resp.text, str) else resp.content.decode("utf-8", "replace")
        abstract = extract_abstract(body)
        if abstract:
            return abstract
    return ""


def _needs_enrichment(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and item.get("source") == "europepmc"
        and not str(item.get("abstract") or "").strip()
        and bool(str(item.get("pmcid") or "").strip() or str(item.get("pmid") or "").strip())
    )


def enrich_results(
    items,
    *,
    max_sentences: int = 3,
    engine: str = "sumy",
    max_fetch: int = MAX_PER_RUN,
) -> tuple[list[dict], dict]:
    """Догрузить аннотации в уже готовые результаты поиска. None внешне не допускает исключений.

    Возвращает (новые_карточки, статистика). Исходный список и карточки не мутируются:
    изменённые записи возвращаются копиями.
    """
    stats = {"attempted": 0, "enriched": 0, "failed": 0, "skipped_limit": 0}
    out: list[dict] = []
    for raw in items:
        item = dict(raw) if isinstance(raw, dict) else raw
        try:
            if _needs_enrichment(item):
                if stats["attempted"] >= max_fetch:
                    stats["skipped_limit"] += 1
                else:
                    stats["attempted"] += 1
                    abstract = fetch_abstract_for_record(
                        pmcid=item.get("pmcid") or "", pmid=item.get("pmid") or ""
                    )
                    if abstract:
                        item["abstract"] = abstract
                        item["abstract_origin"] = "fulltext_xml"
                        summary = None
                        for candidate_engine in (engine, "sumy"):
                            try:
                                summary = summarize_with_details(
                                    abstract[:MAX_TEXT_LENGTH], max_sentences, candidate_engine
                                )
                                break
                            except Exception as exc:
                                logger.warning(
                                    "Повторное резюме движком %s не построено (%s).",
                                    candidate_engine,
                                    type(exc).__name__,
                                )
                        if summary is not None:
                            item["summary"] = summary.text
                            item["summary_method"] = summary.method
                            item["summary_input"] = "fulltext_xml"
                            if summary.notice:
                                item["summary_notice"] = summary.notice
                            else:
                                item.pop("summary_notice", None)
                        stats["enriched"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.warning("Догрузка аннотации не удалась (%s).", type(exc).__name__)
        out.append(item)
    return out, stats
