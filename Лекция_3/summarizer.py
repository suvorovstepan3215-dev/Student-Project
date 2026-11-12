# summarizer.py
"""Агент-суммаризатор.

``engine="sumy"`` строит резюме алгоритмом LSA из библиотеки Sumy (латиница).
``engine="ollama"`` / ``engine="openai"`` запрашивают модель через
OpenAI-совместимый endpoint из секций ``providers`` / ``llm`` в config.yaml.

Для кириллицы (например, карточки Рубрикатора КР Минздрава) Sumy не
подходит — у него нет русского токенизатора, — поэтому такой текст
резюмируется первыми предложениями. Этот же вариант используется как
запасной, если LSA или ответ модели недоступны.
"""

import logging
import os
import re

import requests

# Импорты Sumy – чистый Python, без компилируемых расширений
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer

from utils import load_config

# Дочерний логгер – вывод настраивается у родителя «archiv_tracker» (utils.py)
logger = logging.getLogger("archiv_tracker.summarizer")


class LlmUnavailable(Exception):
    """Локальный сервер модели не ответил."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


LLM_PROVIDERS = ("ollama", "openai")


def normalize_engine(engine: str | None) -> str:
    """``sumy``, ``ollama`` или ``openai``. Неизвестное значение становится ``sumy``."""
    name = str(engine or "sumy").strip().lower()
    if name not in ("sumy", *LLM_PROVIDERS):
        return "sumy"
    return name


def resolve_llm(cfg: dict | None = None, engine: str | None = None) -> dict:
    """Настройки модели для провайдера. Берёт блок ``providers``, иначе ``llm``."""
    cfg = load_config() if cfg is None else cfg
    engine = normalize_engine(engine if engine is not None else cfg.get("summarizer"))
    providers = cfg.get("providers") or {}
    stored = providers.get(engine)
    if isinstance(stored, dict):
        return dict(stored)
    return dict(cfg.get("llm") or {})


def llm_down_message(base_url: str, *, with_fallback: bool = True) -> str:
    target = base_url or "адрес не задан"
    text = f"Сервер LLM недоступен ({target})."
    if with_fallback:
        text += " Обработка резюме переключена на Sumy."
    return text


def llm_unavailable_message(llm: dict | None = None, *, with_fallback: bool = True) -> str | None:
    """Сообщение, если сервер модели не отвечает. Иначе None.

    Без ``llm`` читает активного провайдера из config.yaml. Для ``sumy`` возвращает None.
    """
    if llm is None:
        cfg = load_config()
        if normalize_engine(cfg.get("summarizer")) == "sumy":
            return None
        llm = resolve_llm(cfg)
    base_url = str(llm.get("base_url") or "").rstrip("/")
    if not base_url or not llm.get("model"):
        return llm_down_message(base_url, with_fallback=with_fallback)
    try:
        requests.get(f"{base_url}/models", timeout=3)
    except requests.RequestException:
        return llm_down_message(base_url, with_fallback=with_fallback)
    return None


def _server_down(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        return True
    return response.status_code >= 500


def _get_summarizer() -> LsaSummarizer:
    """Создать экземпляр суммаризатора Sumy LSA.

    Объект лёгкий, поэтому создаём новый при каждом вызове – накладные
    расходы ничтожны при нашем небольшом числе публикаций.
    """
    summarizer = LsaSummarizer()
    return summarizer


# Sumy поставляется только с латинскими токенизаторами (english, french, …),
# русского нет. Поэтому кириллицу резюмируем просто: первые предложения.
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def _has_cyrillic(text: str) -> bool:
    """В тексте есть хотя бы один кириллический символ."""
    return bool(_CYRILLIC_RE.search(text or ""))


def _split_sentences(text: str) -> list[str]:
    """Разбить текст на предложения по точке/восклицательному/вопросительному
    знаку и переносу строки. Простой, но достаточный для кириллицы вариант."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [part.strip() for part in parts if part.strip()]


def _summarize_first_sentences(text: str, max_sentences: int) -> str:
    """Резюме для кириллицы и как запасной вариант: первые предложения текста."""
    sentences = _split_sentences(text)
    if not sentences:
        return (text or "").strip()[:500]
    limit = max(1, int(max_sentences))
    summary = " ".join(sentences[:limit])
    logger.info("Резюме построено первыми предложениями (%d из %d).", limit, len(sentences))
    return summary


def _summarize_lsa(text: str, max_sentences: int) -> str:
    """Построить резюме алгоритмом LSA (английский токенизатор)."""
    parser = PlaintextParser.from_string(text, Tokenizer("english"))
    summary_sentences = _get_summarizer()(parser.document, max_sentences)
    summary = " ".join(str(sentence) for sentence in summary_sentences)
    logger.info("Резюме построено алгоритмом LSA (%d предложений).", max_sentences)
    return summary


def _fallback_summary(text: str, max_sentences: int) -> str:
    """Резюме, не требующее внешних моделей: первые предложения.

    Для кириллицы — единственный рабочий вариант (Sumy без русского
    токенизатора). Для латиницы используем LSA, а если он по какой-то
    причине не сработал — тоже первые предложения.
    """
    if _has_cyrillic(text):
        return _summarize_first_sentences(text, max_sentences)
    try:
        return _summarize_lsa(text, max_sentences)
    except Exception as exc:  # Sumy может упасть на коротком/странном тексте
        logger.warning("Sumy LSA не сработал (%s); используются первые предложения.", exc)
        return _summarize_first_sentences(text, max_sentences)


def _summarize_llm(text: str, max_sentences: int, llm: dict) -> str:
    """Запросить резюме у модели через ``/v1/chat/completions``."""
    base_url = str(llm["base_url"]).rstrip("/")
    model = llm["model"]
    api_key = llm.get("api_key") or os.environ.get(str(llm.get("api_key_env") or ""), "ollama")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0.2,
            "max_tokens": max(64, max_sentences * 80),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Сожми текст. "
                        f"Лимит предложений: {max_sentences}. "
                        "Пиши на языке исходного текста. "
                        "Только резюме, без вступления."
                    ),
                },
                {"role": "user", "content": text},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    summary = (content or "").strip()
    logger.info("Резюме построено моделью %s (лимит %d).", model, max_sentences)
    return summary


def summarize(
    text: str,
    max_sentences: int = 3,
    engine: str = "sumy",
    llm: dict | None = None,
) -> str:
    """Вернуть краткое резюме текста *text*.

    Параметры
    ---------
    text: str
        Полный текст публикации.
    max_sentences: int, необязательный
        Максимальное число предложений в резюме. По умолчанию 3.
    engine: str, необязательный
        ``sumy`` (по умолчанию), ``ollama`` или ``openai``.
    llm: dict, необязательный
        Подключение модели. Если не задано, берётся из config.yaml.

    Возвращает
    ----------
    str
        Готовое резюме. Если выбрана модель, а сервер не отвечает,
        выбрасывается ``LlmUnavailable``.
    """
    if not text:
        logger.warning("Empty text received for summarization.")
        return ""

    engine = normalize_engine(engine)
    if engine != "sumy":
        if llm is None:
            llm = resolve_llm(engine=engine)
        base_url = str(llm.get("base_url") or "").rstrip("/")
        if not (base_url and llm.get("model")):
            raise LlmUnavailable(llm_down_message(base_url))
        try:
            summary = _summarize_llm(text, max_sentences, llm)
        except requests.RequestException as exc:
            if _server_down(exc):
                raise LlmUnavailable(llm_down_message(base_url)) from exc
            detail = ""
            resp = getattr(exc, "response", None)
            if resp is not None and resp.text:
                detail = f": {resp.text[:300]}"
            logger.warning(
                "Не удалось получить резюме от %s (%s%s). Используется запасной вариант.",
                llm.get("model"),
                exc,
                detail,
            )
            return _fallback_summary(text, max_sentences)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning(
                "Ответ модели %s не разобран (%s). Используется запасной вариант.",
                llm.get("model"),
                exc,
            )
            return _fallback_summary(text, max_sentences)
        if summary:
            return summary
        logger.warning("Модель %s вернула пустое резюме. Используется запасной вариант.", llm.get("model"))
        return _fallback_summary(text, max_sentences)

    # Движок sumy: кириллицу резюмируем первыми предложениями, латиницу — LSA.
    if _has_cyrillic(text):
        return _summarize_first_sentences(text, max_sentences)
    try:
        return _summarize_lsa(text, max_sentences)
    except Exception as exc:
        logger.warning("Sumy LSA не сработал (%s); используются первые предложения.", exc)
        return _summarize_first_sentences(text, max_sentences)
