"""Public-safe Streamlit UI. Settings/history are session-local, secrets server-owned."""

from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import fulltext_enrich
import mcp_service
import translator
from main import process_items
from scraper import SOURCE_NAMES
from summarizer import llm_unavailable_message, normalize_engine, resolve_llm
from utils import load_config, resolve_date_range
from validation import clean_topics

_MCP_TOOLS = {
    "list_sources": "каталог источников (id, описание, лимиты)",
    "search_publications": "поиск по нескольким источникам с валидацией",
    "search_europepmc": "Europe PMC — биомедицинские публикации",
    "search_openalex": "OpenAlex — открытый граф научных работ",
    "search_arxiv": "arXiv — препринты",
    "search_archive": "archive.org — книги и документы",
    "search_habr": "Habr — русскоязычная лента",
    "search_minzdrav": "Каталог КР Минздрава РФ (метаданные)",
    "summarize_text": "резюме текста локально (Sumy LSA)",
}

PROVIDER_LABELS = {"sumy": "Локальный LSA (без LLM)", "ollama": "Ollama", "openai": "OpenAI-совместимый API"}
METHOD_LABELS = {
    "sumy_lsa": "Sumy LSA / NLTK",
    "sumy_lsa_regex": "Sumy LSA / автономный токенизатор",
    "first_sentences": "Первые предложения (резервный вариант)",
    "metadata_only": "Описание метаданных, не резюме КР",
    "none": "Не построено",
}

# Calm beige/white theme: paper background, white cards, deep taupe accent.
# Streamlit widget colours come from .streamlit/config.toml [theme]; the rest is CSS.
_STYLE = """
<style>
.stApp { background: #FAF7F0; }
[data-testid="stSidebar"] { background: #F2EEE3; border-right: 1px solid #E3DCCF; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #4A443B; }
h1, h2, h3, h4 {
    font-family: Georgia, "Times New Roman", serif;
    color: #3F3A33;
    font-weight: 600;
    letter-spacing: 0.01em;
}
.app-header { border-bottom: 2px solid #E3DCCF; padding: 0.25rem 0 1.1rem; margin-bottom: 1.1rem; }
.app-kicker {
    font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: #8A6D4B; font-weight: 700; margin-bottom: 0.4rem;
}
.app-title {
    font-family: Georgia, "Times New Roman", serif; font-size: 2rem; line-height: 1.15;
    color: #3F3A33; margin: 0 0 0.4rem; font-weight: 600;
}
.app-sub { color: #7D766B; font-size: 0.93rem; max-width: 72ch; margin: 0; line-height: 1.45; }
button[kind="primary"], [data-testid="stBaseButton-primary"] {
    background: #8A6D4B !important; border: 1px solid #6F5740 !important; color: #FFFDF8 !important;
    border-radius: 6px !important; font-weight: 600 !important; letter-spacing: 0.02em;
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
    background: #6F5740 !important; border-color: #5C4936 !important; color: #FFFDF8 !important;
}
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
    background: #FFFFFF !important; color: #8A6D4B !important; border: 1px solid #D8CFBE !important;
    border-radius: 6px !important;
}
button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
    border-color: #8A6D4B !important; color: #6F5740 !important;
}
.stTabs [data-baseweb="tab-list"] { gap: 1.75rem; border-bottom: 1px solid #E3DCCF; }
.stTabs [data-baseweb="tab"] { color: #7D766B; font-weight: 600; padding: 0.55rem 0.15rem; }
.stTabs [aria-selected="true"] { color: #8A6D4B !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: #8A6D4B !important; height: 3px; }
.stTabs [data-baseweb="tab-border"] { background: #E3DCCF; }
[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #E3DCCF; border-radius: 8px;
    padding: 0.85rem 1.1rem; box-shadow: 0 1px 2px rgba(63, 58, 51, 0.05);
}
[data-testid="stMetricLabel"] p {
    color: #7D766B !important; font-size: 0.72rem !important;
    text-transform: uppercase; letter-spacing: 0.09em;
}
[data-testid="stMetricValue"] { font-family: Georgia, "Times New Roman", serif; color: #3F3A33; }
[data-testid="stExpander"] {
    background: #FFFFFF; border: 1px solid #E3DCCF !important; border-radius: 8px !important;
    box-shadow: 0 1px 2px rgba(63, 58, 51, 0.04);
}
[data-testid="stExpander"] summary { font-weight: 600; color: #3F3A33; }
[data-testid="stAlert"] { border-radius: 8px; }
.stLinkButton a, [data-testid="stLinkButton"] a {
    color: #8A6D4B !important; border: 1px solid #C9BBA4 !important; background: #FFFDF8 !important;
    border-radius: 6px !important;
}
.stLinkButton a:hover, [data-testid="stLinkButton"] a:hover {
    border-color: #8A6D4B !important; color: #6F5740 !important;
}
[data-testid="stDataFrame"] { border: 1px solid #E3DCCF; border-radius: 8px; }
.stTextArea textarea, .stTextInput input { border-radius: 6px !important; }
[data-testid="stCaptionContainer"], .stCaption { color: #8D8578; }
</style>
"""


def _grouped(entries):
    """Collapse identical (source, code, message) diagnostics, keeping first-seen order."""
    counts = Counter((e.get("source"), e.get("code"), e.get("message")) for e in entries)
    seen = set()
    for entry in entries:
        key = (entry.get("source"), entry.get("code"), entry.get("message"))
        if key in seen:
            continue
        seen.add(key)
        yield entry, counts[key]


st.set_page_config(page_title="Трекер медицинских публикаций", layout="wide")
st.markdown(_STYLE, unsafe_allow_html=True)
cfg = load_config()
llm_enabled = (cfg.get("ui") or {}).get("enable_llm") is True
allowed_providers = list(PROVIDER_LABELS) if llm_enabled else ["sumy"]
if st.session_state.get("provider") not in allowed_providers:
    configured = normalize_engine(cfg.get("summarizer"))
    st.session_state["provider"] = configured if configured in allowed_providers else "sumy"
if "seen_ids" not in st.session_state:
    st.session_state["seen_ids"] = []

with st.sidebar:
    st.header("Настройки поиска")
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
        default=[
            sid for sid in cfg.get("sources", ["europepmc", "openalex", "minzdrav"]) if sid in SOURCE_NAMES
        ],
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
    fulltext_fallback = st.checkbox(
        "Эксперимент: догрузить пустые аннотации Europe PMC из полного текста",
        value=False,
        key="exp_fulltext_fallback",
        help="После обычного поиска (он не меняется): для записей Europe PMC с пометкой "
        "«No abstract provided» <abstract> берётся из открытого JATS XML полного текста. "
        "Не больше 10 догрузок за запуск; сбой никак не влияет на результаты поиска.",
    )
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

st.markdown(
    """<div class="app-header">
  <div class="app-kicker">Европейская медлитература · КР Минздрава РФ</div>
  <h1 class="app-title">Трекер медицинских публикаций</h1>
  <p class="app-sub">Поиск, лексическая проверка релевантности и локальное резюме публикаций. Не медицинская рекомендация и не проверка достоверности публикаций.</p>
</div>""",
    unsafe_allow_html=True,
)
tab_search, tab_providers, tab_diagram, tab_mcp, tab_slides = st.tabs(
    ["Поиск", "Провайдеры", "Схема агентов", "MCP", "Презентация"]
)

with tab_search:
    if topics_error:
        st.warning(topics_error)
    if not sources:
        st.warning("Подключите хотя бы один источник.")
    run = st.button(
        "Запустить поиск сейчас",
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
                items = list(result)
                if fulltext_fallback:
                    try:
                        items, enrich_stats = fulltext_enrich.enrich_results(
                            items, max_sentences=max_sentences, engine=engine
                        )
                        st.session_state["enrich_stats"] = enrich_stats
                    except Exception:  # экспериментальный этап не имеет права ронять поиск
                        st.session_state["enrich_stats"] = None
                        st.warning("Экспериментальная догрузка не удалась — показаны обычные результаты.")
                else:
                    st.session_state["enrich_stats"] = None
                st.session_state["results"] = items
                st.session_state["diagnostics"] = result.diagnostics()
                st.session_state["run_error"] = None
                if save_history:
                    successful = [
                        item["identifier"]
                        for item in items
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
    enrich_stats = st.session_state.get("enrich_stats")
    if enrich_stats is not None:
        st.caption(
            "Экспериментальный режим: догружено аннотаций из полного текста — "
            f"{enrich_stats['enriched']} (попыток: {enrich_stats['attempted']}, "
            f"ошибок: {enrich_stats['failed']}, не дошли лимит {enrich_stats['skipped_limit']})."
        )
    diagnostics = st.session_state.get("diagnostics", {})
    for error, count in _grouped(diagnostics.get("errors", [])):
        suffix = f" (повторяется: {count})" if count > 1 else ""
        st.error(f"{SOURCE_NAMES.get(error['source'], error['source'])}: {error['message']}{suffix}")
    for warning, count in _grouped(diagnostics.get("warnings", [])):
        message = warning["message"]
        if count > 1:
            if warning.get("code") == "text_unavailable":
                message = f"У {count} записей нет текста для резюме; в историю они не записаны."
            else:
                message = f"{message} (повторяется: {count})"
        st.warning(f"{SOURCE_NAMES.get(warning['source'], warning['source'])}: {message}")
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
            cards, table = st.tabs(["Карточки", "Таблица"])
            with cards:
                for index, item in enumerate(results, 1):
                    source = SOURCE_NAMES.get(item.get("source"), item.get("source", "—"))
                    with st.expander(
                        f"{index}. {source} · {item.get('published') or 'дата неизвестна'}",
                        expanded=index <= 3,
                    ):
                        # External titles/summaries are plain text, not executable Markdown/HTML.
                        st.text(item.get("title") or "Без названия")
                        st.link_button("Открыть запись источника", item["url"])
                        st.caption("ID: " + item["identifier"])
                        if item.get("authors"):
                            st.text("Авторы: " + item["authors"])
                        if item.get("abstract_origin") == "fulltext_xml":
                            st.caption(
                                "Аннотация догружена из открытого полного текста (экспериментальный режим)."
                            )
                        if item.get("provenance_notice"):
                            st.info(item["provenance_notice"])
                        method = item.get("summary_method", "none")
                        st.markdown(
                            "**Описание карточки (не резюме КР):**"
                            if method == "metadata_only"
                            else "**Резюме текста:**"
                        )
                        st.text(item.get("summary") or "Резюме отсутствует.")
                        summary_text = (item.get("summary") or "").strip()
                        if summary_text and method != "metadata_only":
                            # Перевод — только по кнопке; текст уходит на внешний сервер (см. translator.py).
                            target = "ru" if not translator.mostly_cyrillic(summary_text) else "en"
                            direction = "на русский" if target == "ru" else "на английский"
                            translation_key = f"tr:{item['identifier']}"
                            translated = st.session_state.get(translation_key)
                            if not translated and st.button(
                                f"Перевести резюме {direction}", key=f"btn:{item['identifier']}"
                            ):
                                with st.spinner("Переводим резюме…"):
                                    try:
                                        translated = translator.translate_text(summary_text, target)
                                    except Exception:
                                        translated = None
                                        st.warning(
                                            "Не удалось перевести: сервис перевода недоступен. Оригинал показан выше."
                                        )
                                    else:
                                        st.session_state[translation_key] = translated
                            if translated and st.button("Скрыть перевод", key=f"hidetr:{item['identifier']}"):
                                st.session_state[translation_key] = None
                                translated = None
                            if translated:
                                st.info(translated)
                                note = (
                                    "Перевод выполнен внешним сервисом Google Translate (неофициальный endpoint); "
                                    "текст резюме отправлен на серверы Google. Оригинал показан выше."
                                )
                                if len(summary_text) > translator.MAX_INPUT_CHARS:
                                    note += " Переведены первые 2000 символов."
                                st.caption(note)
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

with tab_mcp:
    st.caption(
        "MCP (Model Context Protocol) — stdio-интерфейс для внешних ИИ-клиентов. "
        "Этот сайт MCP-сервером не является и JSON-RPC по stdio не принимает; "
        "ниже — живая песочница: те же инструменты и тот же сервисный слой, что в MCP (mcp_service.py), "
        "с теми же проверками. Запросы search_* ходят в интернет с сервера."
    )
    tool = st.selectbox(
        "Инструмент", _MCP_TOOLS, format_func=lambda key: f"{key} — {_MCP_TOOLS[key]}", key="mcp_tool"
    )
    raw_topics = ""
    rows = None
    date_from = date_to = None
    sources = None
    if tool == "summarize_text":
        text_for_summary = st.text_area("Текст для резюме", height=140, max_chars=10000)
        max_sentences = st.slider("Предложений в резюме", 1, 10, 3, key="mcp_max_sentences")
    elif tool == "list_sources":
        st.caption("Каталог источников не требует параметров.")
    else:
        raw_topics = st.text_area(
            "Темы (по одной в строке)", value="\n".join(cfg.get("topics", [])), height=80, max_chars=2100
        )
        if tool == "search_publications":
            sources = st.multiselect(
                "Источники",
                list(SOURCE_NAMES),
                default=[
                    sid
                    for sid in cfg.get("sources", ["europepmc", "openalex", "minzdrav"])
                    if sid in SOURCE_NAMES
                ],
                format_func=lambda sid: SOURCE_NAMES[sid],
                key="mcp_sources",
            )
        default_from, default_to = resolve_date_range(preset="week")
        col_from, col_to = st.columns(2)
        date_from = col_from.date_input("С", value=default_from, key="mcp_date_from")
        date_to = col_to.date_input("По", value=default_to, key="mcp_date_to")
        rows = st.slider("Записей с источника", 1, 10, 3, key="mcp_rows")
    if st.button("Вызвать инструмент", key="mcp_call"):
        with st.spinner("Вызываем инструмент…"):
            try:
                if tool == "list_sources":
                    result = mcp_service.list_sources_catalog()
                elif tool == "summarize_text":
                    result = mcp_service.summarize_text(text_for_summary, max_sentences)
                elif tool == "search_publications":
                    topics = clean_topics(raw_topics.splitlines())
                    result = mcp_service.search_publications(
                        topics,
                        sources=sources,
                        rows=rows,
                        date_from=date_from.isoformat() if date_from else None,
                        date_to=date_to.isoformat() if date_to else None,
                    )
                else:
                    topics = clean_topics(raw_topics.splitlines())
                    result = mcp_service.search_source(
                        tool.removeprefix("search_"),
                        topics,
                        rows=rows,
                        date_from=date_from.isoformat() if date_from else None,
                        date_to=date_to.isoformat() if date_to else None,
                    )
            except ValueError as exc:
                st.warning(str(exc))
            except Exception:
                st.warning(
                    "Инструмент не ответил или источник недоступен. Подробности скрыты; попробуйте позже."
                )
            else:
                st.json(result)
    with st.expander("Как подключить настоящий MCP (для преподавателя, локально)"):
        st.markdown(
            "Песочница — не протокол. Настоящий MCP работает по stdio на вашем компьютере:\n\n"
            "1. Скачайте репозиторий или папку `local_pc` из релиза/клона и выполните `setup.sh` (Linux/macOS) или `setup.ps1` (Windows).\n"
            "2. Подключите MCP-сервер к клиенту: Atomic Chat, Cursor или Claude Desktop. "
            "Готовый генератор конфигурации — `local_pc/tools/mcp_config.py` (форматы `mcp` и `atomic`), "
            "инструкции — `VARIANTS.md` в корне репозитория: "
            "https://github.com/suvorovstepan3215-dev/Student-Project/blob/main/VARIANTS.md\n"
            "3. 9 инструментов: `list_sources`, `search_publications`, `search_europepmc`, `search_openalex`, "
            "`search_arxiv`, `search_archive`, `search_habr`, `search_minzdrav`, `summarize_text`.\n\n"
            "Из этой страницы MCP недоступен: браузер не может открыть stdio-процесс на чужом сервере. "
            "Песочница выше — честная замена для оценки поведения инструментов."
        )

with tab_slides:
    st.caption(
        "Материалы для выступления. Листайте стрелками влево/вправо или кнопками внизу слайда. "
        "Презентация самодостаточна и не отправляет данные наружу."
    )
    deck = Path(__file__).parent / "diagr" / "presentation.html"
    if deck.is_file():
        components.html(deck.read_text(encoding="utf-8"), height=640, scrolling=False)
    else:
        st.info("Файл diagr/presentation.html не найден.")
