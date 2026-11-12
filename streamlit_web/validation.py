"""Shared limits for UI, CLI, agents and MCP. Validation never performs I/O."""

import re
from datetime import date

MAX_TOPICS = 10
MAX_TOPIC_LENGTH = 200
MAX_TEXT_LENGTH = 10000


def bounded_int(value, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Поле «{field}» должно быть целым числом.")
    if not minimum <= value <= maximum:
        raise ValueError(f"Поле «{field}» должно быть от {minimum} до {maximum}.")
    return value


def clean_topics(topics) -> list[str]:
    if not isinstance(topics, (list, tuple)):
        raise ValueError("Поле «topics» должно быть списком строк.")
    if len(topics) > MAX_TOPICS:
        raise ValueError(f"Укажите не больше {MAX_TOPICS} тем.")
    result = []
    for topic in topics:
        if not isinstance(topic, str):
            raise ValueError("Каждая тема должна быть строкой.")
        topic = topic.strip()
        if not topic:
            continue
        if len(topic) > MAX_TOPIC_LENGTH or any(ord(ch) < 32 for ch in topic):
            raise ValueError(f"Тема должна содержать не больше {MAX_TOPIC_LENGTH} символов без переносов.")
        if topic not in result:
            result.append(topic)
    if not result:
        raise ValueError("Укажите хотя бы одну тему (topics).")
    return result


def parse_iso_date(value, field: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(f"Поле «{field}» должно быть датой ГГГГ-ММ-ДД.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"Поле «{field}» содержит несуществующую дату.") from exc
