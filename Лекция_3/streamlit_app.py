"""Public-safe Streamlit UI. Settings/history are session-local, secrets server-owned."""

from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from main import process_items
from scraper import SOURCE_NAMES
from summarizer import llm_unavailable_message, normalize_engine, resolve_llm
from utils import load_config, resolve_date_range
from validation import clean_topics

PROVIDER_LABELS = {"sumy": "Локальный LSA (без LLM)", "ollama": "Ollama", "openai": "OpenAI-совместимый API"}
METHOD_LABELS = {
    "sumy_lsa": "Sumy LSA / NLTK",
    "sumy_lsa_regex": "Sumy LSA / автономный токенизатор",
    "first_sentences": "Первые предложения (резервный вариант)",
    "metadata_only": "Описание метаданных, не резюме КР",
    "none": "Не построено",
}

st.set_page_config(page_title="Трекер медицинских публикаций", page_icon="📚", layout="wide")
cfg = load_config()
llm_enabled = (cfg.get("ui") or {}).get("enable_llm") is True
allowed_providers = list(PROVIDER_LABELS) if llm_enabled else ["sumy"]
if st.session_state.get("provider") not in allowed_providers:
    configured = normalize_engine(cfg.get("summarizer"))
    st.session_state["provider"] = configured if configured in allowed_providers else "sumy"
if "seen_ids" not in st.session_state:
    st.session_state["seen_ids"] = []

with st.sidebar:
    st.header("⚙️ Настройки поиска")
    topics_text = st.text_area(
        "Темы (по одной в строке)",
        value="\n".join(cfg.get("topics", [])),
        height=110,
        max_chars=2100,
        help="Не больше 10 тем по 200 символов. Для Europe PMC удобнее английский, для КР — русский или код МКБ-10.",
    )
    topics_error = None
    try:
        topics = clean_topics(topics_text.splitlines())
    except ValueError as exc:
        topics, topics_error = [], str(exc)
    source_ids = list(SOURCE_NAMES)
    sources = st.multiselect(
        "Подключённые источники",
        options=source_ids,
        default=[sid for sid in cfg.get("sources", ["europepmc", "minzdrav"]) if sid in SOURCE_NAMES],
        format_func=lambda sid: SOURCE_NAMES[sid],
    )
    period = st.selectbox(
        "Диапазон дат",
        ["week", "month", "custom"],
        format_func=lambda key: {"week": "Неделя", "month": "Месяц", "custom": "Произвольный"}[key],
    )
    if period == "custom":
        default_from, default_to = resolve_date_range()
        date_from = st.date_input("С", value=default_from)
        date_to = st.date_input("По", value=default_to)
    else:
        date_from, date_to = resolve_date_range(preset=period)
        st.caption(f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}, обе даты включены")
    dates_ok = bool(date_from and date_to and date_from <= date_to)
    if not dates_ok:
        st.warning("Дата «С» должна быть не позже даты «По».")
    if "minzdrav" in sources:
        st.caption(
            "КР: API фильтруется по дате реестра; зеркало — только по году пересмотра. Для старых КР расширьте период."
        )
    rows = st.slider("Записей с каждого источника", 5, 100, 20, step=5)
    st.subheader("Резюме")
    engine = st.session_state["provider"]
    st.caption(PROVIDER_LABELS[engine] + ". Выбор — на вкладке «Провайдеры».")
    max_sentences = st.slider("Предложений в резюме", 1, 10, 3)
    text_limit = st.slider("Лимит входного текста (символов)", 500, 10000, 2000, step=500)
    st.subheader("История этого сеанса")
    skip_seen = st.checkbox("Пропускать просмотренные в этом сеансе", value=False)
    save_history = st.checkbox("Запоминать обработанные в этом сеансе", value=False)
    st.caption(
        f"Запомнено: {len(st.session_state['seen_ids'])}. Серверная история не читается и не изменяется."
    )
    if st.button("Очистить историю сеанса", width="stretch"):
        st.session_state["seen_ids"] = []
        st.rerun()
    st.caption("Почта в веб-интерфейсе отключена. Планировщик запускается отдельно: python main.py.")

st.title("📚 Трекер медицинских публикаций")
st.caption(
    "Поиск → лексическая проверка релевантности → резюме. Не медицинская рекомендация и не проверка достоверности публикаций."
)
tab_search, tab_providers, tab_diagram = st.tabs(["🔎 Поиск", "🤖 Провайдеры", "🗺 Схема агентов"])

with tab_search:
    if topics_error:
        st.warning(topics_error)
    if not sources:
        st.warning("Подключите хотя бы один источник.")
    run = st.button(
        "🚀 Запустить поиск сейчас",
        type="primary",
        disabled=bool(topics_error) or not sources or not dates_ok,
    )
    if run:
        with st.spinner("Ищем публикации и составляем резюме…"):
            try:
                result = process_items(
                    topics=topics,
                    sources=sources,
                    rows=rows,
                    skip_seen=skip_seen,
                    save_history=False,
                    session_history=st.session_state["seen_ids"],
                    max_sentences=max_sentences,
                    text_limit=text_limit,
                    engine=engine,
                    date_from=date_from,
                    date_to=date_to,
                    send=False,
                )
                st.session_state["results"] = list(result)
                st.session_state["diagnostics"] = result.diagnostics()
                st.session_state["run_error"] = None
                if save_history:
                    successful = [
                        item["identifier"]
                        for item in result
                        if item.get("summary") and item.get("summary_method") != "none"
                    ]
                    st.session_state["seen_ids"] = list(
                        dict.fromkeys(st.session_state["seen_ids"] + successful)
                    )[-1000:]
            except ValueError as exc:
                st.session_state["run_error"] = str(exc)
                st.session_state["results"] = None
                st.session_state["diagnostics"] = {}
            except Exception:
                st.session_state["run_error"] = (
                    "Не удалось выполнить цикл обработки. Проверьте серверный журнал; секреты и подробности ошибки скрыты."
                )
                st.session_state["results"] = None
                st.session_state["diagnostics"] = {}
    if st.session_state.get("run_error"):
        st.error(st.session_state["run_error"])
    diagnostics = st.session_state.get("diagnostics", {})
    for error in diagnostics.get("errors", []):
        st.error(f"{SOURCE_NAMES.get(error['source'], error['source'])}: {error['message']}")
    for warning in diagnostics.get("warnings", []):
        st.warning(f"{SOURCE_NAMES.get(warning['source'], warning['source'])}: {warning['message']}")
    statuses = diagnostics.get("source_statuses", {})
    if statuses:
        labels = {"ok": "доступен", "partial": "неполные данные / ограничения", "error": "ошибка"}
        st.caption(
            " · ".join(
                f"{SOURCE_NAMES.get(sid, sid)}: {labels.get(status, status)}"
                for sid, status in statuses.items()
            )
        )
    results = st.session_state.get("results")
    if results is None:
        if not st.session_state.get("run_error"):
            st.info("Нажмите «Запустить поиск сейчас», чтобы выполнить один цикл обработки.")
    else:
        st.header("Результаты поиска")
        c1, c2, c3 = st.columns(3)
        c1.metric("Записей", len(results))
        c2.metric(
            "С резюме текста",
            sum(bool(r.get("summary")) and r.get("summary_method") != "metadata_only" for r in results),
        )
        c3.metric(
            "Карточек КР (метаданные)", sum(r.get("summary_method") == "metadata_only" for r in results)
        )
        if not results:
            if diagnostics.get("errors"):
                st.warning(
                    "Результатов нет, но были ошибки источников. Это не доказывает отсутствие публикаций."
                )
            else:
                st.info(
                    "Подходящих записей в диапазоне не найдено. Попробуйте изменить тему, расширить даты или выключить пропуск просмотренных."
                )
        else:
            cards, table = st.tabs(["🗂 Карточки", "📋 Таблица"])
            with cards:
                for index, item in enumerate(results, 1):
                    source = SOURCE_NAMES.get(item.get("source"), item.get("source", "—"))
                    with st.expander(
                        f"{index}. {source} · {item.get('published') or 'дата неизвестна'}",
                        expanded=index <= 3,
                    ):
                        # External titles/summaries are plain text, not executable Markdown/HTML.
                        st.text(item.get("title") or "Без названия")
                        st.link_button("Открыть запись источника ↗", item["url"])
                        st.caption("ID: " + item["identifier"])
                        if item.get("authors"):
                            st.text("Авторы: " + item["authors"])
                        if item.get("provenance_notice"):
                            st.info(item["provenance_notice"])
                        method = item.get("summary_method", "none")
                        st.markdown(
                            "**Описание карточки (не резюме КР):**"
                            if method == "metadata_only"
                            else "**Резюме текста:**"
                        )
                        st.text(item.get("summary") or "Резюме отсутствует.")
                        st.caption("Метод: " + METHOD_LABELS.get(method, method))
                        if item.get("summary_notice"):
                            st.caption(item["summary_notice"])
                        st.caption("Статус: " + item.get("status", ""))
            with table:
                frame = pd.DataFrame(results).rename(
                    columns={
                        "source": "Источник",
                        "identifier": "ID",
                        "title": "Название",
                        "url": "Ссылка",
                        "published": "Дата / год",
                        "summary": "Резюме / описание",
                        "summary_method": "Метод",
                        "provenance": "Происхождение",
                        "status": "Статус",
                    }
                )
                frame["Источник"] = frame["Источник"].map(lambda sid: SOURCE_NAMES.get(sid, sid))
                st.dataframe(
                    frame,
                    width="stretch",
                    hide_index=True,
                    column_config={"Ссылка": st.column_config.LinkColumn("Ссылка")},
                )

with tab_providers:
    st.header("Провайдеры резюме")
    st.radio(
        "Активный провайдер",
        allowed_providers,
        format_func=lambda key: PROVIDER_LABELS[key],
        horizontal=True,
        key="provider",
    )
    current = st.session_state["provider"]
    if current == "sumy":
        st.info(
            "Локальный Sumy LSA поддерживает английский и русский текст. Если данных NLTK нет, используется автономный токенизатор. При сбое LSA показываются первые предложения с явной пометкой."
        )
    else:
        preset = resolve_llm(cfg, current)
        st.text("Серверная модель: " + str(preset.get("model") or "не задана"))
        if st.button("Проверить подключение", key="check_provider"):
            notice = llm_unavailable_message(engine=current, with_fallback=False)
            if notice:
                st.warning(notice)
            else:
                st.success(
                    "API отвечает, авторизация принята, настроенная модель есть в /models. Генерация отдельно не проверялась."
                )
    st.caption(
        "Адрес API, модель и имя переменной с ключом задаются администратором в config.yaml. Ввод URL/ключей и запись серверной конфигурации из браузера запрещены."
    )
    if not llm_enabled:
        st.caption(
            "LLM отключены в публичном режиме. В доверенной среде администратор может включить ui.enable_llm; для платного API нужна внешняя авторизация и ограничение запросов."
        )

with tab_diagram:
    st.caption(
        "Streamlit вызывает Python-пайплайн напрямую. MCP — отдельный stdio-интерфейс для внешнего клиента, не URL этого сайта."
    )
    diagram = Path(__file__).parent / "diagr" / "agents.html"
    if diagram.is_file():
        components.html(diagram.read_text(encoding="utf-8"), height=920, scrolling=True)
    else:
        st.info("Схема diagr/agents.html не найдена.")
