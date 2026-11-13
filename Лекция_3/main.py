"""Publication pipeline and optional CLI scheduler; Streamlit runs one cycle directly."""

import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from notifier import send_email
from results import SearchResults, diagnostic
from scraper import SOURCE_NAMES, clean_sources, get_new_items
from summarizer import LlmUnavailable, normalize_engine, summarize_with_details
from utils import extract_text_from_url, load_config, resolve_date_range, update_history
from validation import bounded_int, clean_topics

logger = logging.getLogger("archiv_tracker.main")
MAX_PROCESSED_ITEMS = 100
MAX_PROCESSING_SECONDS = 120


def process_items(
    topics: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    rows: int = 20,
    skip_seen: bool = True,
    save_history: bool = True,
    max_sentences: int = 3,
    text_limit: int = 2000,
    engine: Optional[str] = None,
    llm: Optional[dict] = None,
    send: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    session_history: Optional[list[str]] = None,
) -> SearchResults:
    """A result contains items AND request-local errors/warnings/source_statuses.

    Prefer even a short supplied abstract to downloading a navigation page.
    Catalogue metadata is labelled as metadata, never as a guideline summary.
    Only successful summaries/notifications are added to durable history.
    """
    started = time.monotonic()
    cfg = load_config()
    topics = clean_topics(cfg.get("topics", []) if topics is None else topics)
    sources = clean_sources(cfg.get("sources", list(SOURCE_NAMES)) if sources is None else sources)
    bounded_int(rows, "rows", 1, 100)
    bounded_int(max_sentences, "max_sentences", 1, 10)
    bounded_int(text_limit, "text_limit", 100, 10000)
    if (
        not isinstance(skip_seen, bool)
        or not isinstance(save_history, bool)
        or (send is not None and not isinstance(send, bool))
    ):
        raise ValueError("Флаги запуска должны быть логическими значениями.")
    if session_history is not None and (
        not isinstance(session_history, list)
        or len(session_history) > 1000
        or any(not isinstance(item, str) for item in session_history)
    ):
        raise ValueError("Некорректная история сеанса.")
    session_seen = set(session_history or [])
    start, end = resolve_date_range(date_from=date_from, date_to=date_to)
    engine = normalize_engine(engine if engine is not None else cfg.get("summarizer"))
    send = cfg.get("send_email", False) is True if send is None else send
    email_cfg = cfg.get("email") or {}
    fetched = get_new_items(
        topics=topics,
        sources=sources,
        rows=rows,
        skip_seen=skip_seen and session_history is None,
        save_history=False,
        date_from=start,
        date_to=end,
    )
    results = SearchResults(
        errors=getattr(fetched, "errors", []),
        warnings=getattr(fetched, "warnings", []),
        source_statuses=getattr(fetched, "source_statuses", {}),
    )
    successful_ids = []
    llm_failed = False  # Request-local, not a shared function attribute/session leak.
    deadline = time.monotonic() + MAX_PROCESSING_SECONDS
    for item in fetched:
        if skip_seen and item["identifier"] in session_seen:
            continue
        if len(results) >= MAX_PROCESSED_ITEMS or time.monotonic() >= deadline:
            results.warnings.append(
                diagnostic(
                    "pipeline",
                    "processing_limit",
                    "Достигнут лимит обработки одного цикла (100 записей / 120 секунд после поиска). Остальное не записано в историю.",
                )
            )
            break
        result = dict(item, summary="", status="", summary_method="none", summary_input="")
        metadata_only = item.get("content_kind") == "metadata_only"
        raw_text = str(item.get("abstract") or "").strip()
        if metadata_only:
            # This description was built from known fields. No LLM and no page
            # fetch can turn a title/code/year into nonexistent clinical content.
            result.update(
                summary=raw_text[:text_limit],
                summary_method="metadata_only",
                summary_input="catalog_metadata",
                status="Описание карточки, не резюме полного текста КР",
            )
        else:
            result["summary_input"] = "abstract" if raw_text else "web_page"
            if not raw_text:
                raw_text = extract_text_from_url(item["url"])
            raw_text = raw_text[:text_limit]
            if not raw_text.strip():
                result["status"] = "Не удалось извлечь текст"
                results.warnings.append(
                    diagnostic(
                        item["source"],
                        "text_unavailable",
                        "У одной записи нет текста для резюме; в историю она не записана.",
                    )
                )
                results.append(result)
                continue
            try:
                try:
                    summary = summarize_with_details(
                        raw_text, max_sentences, "sumy" if llm_failed else engine, llm
                    )
                except LlmUnavailable as exc:
                    llm_failed = True
                    results.warnings.append(diagnostic("llm", "local_fallback", exc.message))
                    summary = summarize_with_details(raw_text, max_sentences, "sumy")
                result["summary"] = summary.text
                result["summary_method"] = summary.method
                if summary.notice:
                    result["summary_notice"] = summary.notice
            except Exception as exc:
                logger.warning("Резюме одной записи не построено (%s).", type(exc).__name__)
                result["status"] = "Ошибка построения резюме"
                results.errors.append(
                    diagnostic(
                        item["source"],
                        "summary_failed",
                        "Не удалось построить резюме одной записи; повторная обработка остаётся возможной.",
                    )
                )
                results.append(result)
                continue
        success = bool(result["summary"])
        if send and success:
            source_name = SOURCE_NAMES.get(item["source"], item["source"])
            subject = f"Новая запись ({source_name}): {item['title']}"
            body = f"URL: {item['url']}\nМетод: {result['summary_method']}\n\n{result['summary']}\n"
            success = send_email(subject, body, email_cfg)
            result["status"] = "Отправлено на почту" if success else "Ошибка отправки; в историю не записано"
            if not success:
                results.warnings.append(
                    diagnostic(
                        "email",
                        "send_failed",
                        "Письмо не отправлено. Запись не добавлена в историю для повторной попытки.",
                    )
                )
        elif not metadata_only:
            result["status"] = (
                "Резюме готово; почта выключена" if success else "Резюме пустое; в историю не записано"
            )
        if success:
            successful_ids.append(item["identifier"])
        results.append(result)
    if save_history and successful_ids and not update_history(successful_ids):
        results.warnings.append(diagnostic("history", "write_failed", "Не удалось сохранить историю."))
    logger.info(
        "Цикл завершён за %.1f с: записей %d, успешно %d.",
        time.monotonic() - started,
        len(results),
        len(successful_ids),
    )
    return results


def create_scheduler(config: Optional[dict] = None) -> BackgroundScheduler:
    config = load_config() if config is None else config
    interval = bounded_int(
        config.get("check_interval_seconds", 3600), "check_interval_seconds", 60, 86400 * 30
    )
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        process_items,
        "interval",
        seconds=interval,
        id="publication_check",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True,
    )
    return scheduler


def main():
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Планировщик запущен; первый цикл выполняется сразу.")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка планировщика…")
    finally:
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
