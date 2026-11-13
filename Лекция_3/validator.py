"""Lexical relevance checks. A keyword match is not medical fact checking."""

import logging
import re

from nltk.stem.snowball import SnowballStemmer

from utils import extract_text_from_url, token_is_exact, topic_tokens

logger = logging.getLogger("archiv_tracker.validator")
SOURCES_WITH_ABSTRACT = {"arxiv", "openalex", "europepmc", "minzdrav"}
_RUSSIAN = SnowballStemmer("russian")  # Pure algorithm; no NLTK downloads.


def _token_in_text(token: str, text: str) -> bool:
    exact = token_is_exact(token)
    token = token.casefold().replace("ё", "е")
    text = text.casefold().replace("ё", "е")
    length = sum(char.isalnum() for char in token)
    if exact or length < 4:
        pattern = r"(?<!\w)" + re.escape(token) + r"(?!\w)"
    else:
        if re.fullmatch(r"[а-я]+", token):
            token = _RUSSIAN.stem(token)
        pattern = r"(?<!\w)" + re.escape(token) + r"\w*"
    return re.search(pattern, text) is not None


def _find_topic(text: str, topics: list[str]) -> str | None:
    for topic in topics:
        tokens = topic_tokens(topic)
        if tokens and all(_token_in_text(token, text) for token in tokens):
            return topic
    return None


def validate_item(item: dict, topics: list[str]) -> bool:
    title = str(item.get("title") or "")
    abstract = str(item.get("abstract") or "")
    if _find_topic(f"{title}\n{abstract}", topics):
        return True
    # A metadata-only card must never be validated against navigation/advertising.
    if item.get("content_kind") == "metadata_only" or (
        item.get("source") in SOURCES_WITH_ABSTRACT and abstract
    ):
        return False
    try:
        return _find_topic(extract_text_from_url(item["url"]), topics) is not None
    except Exception:
        logger.warning("Не удалось проверить текст одной записи.")
        return False


def relevance_match(item: dict, topics: list[str]) -> str:
    """Тема, по которой запись прошла проверку в title/abstract, либо '' (совпала только страница)."""
    try:
        text = f"{item.get('title') or ''}\n{item.get('abstract') or ''}"
        return _find_topic(text, topics) or ""
    except Exception:
        return ""

