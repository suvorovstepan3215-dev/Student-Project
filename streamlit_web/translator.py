"""Онлайн-перевод резюме внешним сервисом — только по явному нажатию кнопки.

Используется бесплатный неофициальный endpoint Google Translate
(``translate.googleapis.com``, ``client=gtx``). Ключ не требуется. Текст
резюме (это публичные аннотации/резюме, не персональные данные) отправляется
на серверы Google только в момент нажатия кнопки «Перевести». Модуль
находится вне пайплайна поиска и не участвует в истории сеанса.

Любой сбой — контролируемое исключение ``TranslatorUnavailable`` без
внутренних деталей; исходное резюме не заменяется и не изменяется.
"""

import logging

from network import public_requests as requests

logger = logging.getLogger("archiv_tracker.translator")

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MAX_INPUT_CHARS = 2000
TARGET_LANGUAGES = ("ru", "en")
TIMEOUT_SECONDS = 10


class TranslatorUnavailable(RuntimeError):
    """Сервис перевода недоступен или ответил непонятно. Без деталей наружу."""


def mostly_cyrillic(text: str) -> bool:
    """Грубое определение «текст в основном русский»: ≥30% кириллических букв."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    cyrillic = sum(1 for ch in letters if 0x0400 <= ord(ch) <= 0x04FF)
    return cyrillic / len(letters) >= 0.3


def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """Перевести текст. Бросает TranslatorUnavailable при любом сбое."""
    if target_lang not in TARGET_LANGUAGES:
        raise ValueError("target_lang: допустимы 'ru' и 'en'.")
    payload = str(text or "").strip()
    if not payload:
        raise TranslatorUnavailable("Нечего переводить.")
    payload = payload[:MAX_INPUT_CHARS]
    try:
        response = requests.get(
            ENDPOINT,
            params={"client": "gtx", "sl": source_lang, "tl": target_lang, "dt": "t", "q": payload},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        parts = [
            segment[0]
            for segment in data[0]
            if isinstance(segment, (list, tuple)) and segment and isinstance(segment[0], str)
        ]
    except TranslatorUnavailable:
        raise
    except Exception as exc:  # requests/JSON/сеть: наружу только обезличенная ошибка
        logger.warning("Перевод не удался (%s).", type(exc).__name__)
        raise TranslatorUnavailable("Сервис перевода недоступен.") from exc
    translated = "".join(parts).strip()
    if not translated:
        raise TranslatorUnavailable("Сервис перевода вернул пустой ответ.")
    return translated
