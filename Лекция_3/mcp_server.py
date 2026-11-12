# mcp_server.py
"""MCP-сервер мультиагентного трекера публикаций. Транспорт по умолчанию — stdio.

Сервер выставляет агентов-источников и пайплайн «скрапер + валидатор» как
MCP-tools, чтобы любой MCP-host (Cursor, Claude Desktop, VS Code, свой
клиент) мог искать публикации без импорта кода проекта. Логика tools лежит
в ``mcp_service.py``; здесь только регистрация tools и перевод ошибок.

Запуск: ``python mcp_server.py`` или ``python -m mcp_server`` из папки
проекта. Сообщения JSON-RPC идут по stdin/stdout, поэтому в stdout нельзя
писать ничего лишнего: журнал проекта идёт в stderr и в ``main.log``.

Tools:
- ``list_sources`` — каталог источников (id, название, описание).
- ``search_publications`` — проверенный поиск по нескольким источникам.
- ``search_archive`` / ``search_arxiv`` / ``search_openalex`` / ``search_habr`` /
  ``search_europepmc`` / ``search_minzdrav`` — сырой поиск по одному источнику.
- ``summarize_text`` — резюме текста (Sumy LSA).
"""

from typing import Annotated, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from mcp_service import (
    list_sources_catalog,
    search_publications as _search_publications,
    search_source as _search_source,
    summarize_text as _summarize_text,
)

# Поиск ходит во внешний мир (API источников), поэтому open_world_hint=True.
# В остальном tools только читают: историю не трогают, ничего не сохраняют.
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer(
    "publication-tracker",
    instructions=(
        "Мультиагентный поиск публикаций по источникам: archive.org, arXiv, "
        "OpenAlex, Habr, Europe PMC и Рубрикатор КР Минздрава РФ. Сначала "
        "вызывайте list_sources, чтобы увидеть каталог, затем "
        "search_publications для проверенного поиска или search_<источник> для "
        "сырого поиска по одному источнику. Даты — строки ГГГГ-ММ-ДД, по "
        "умолчанию последняя неделя. Результат — записи с названием, ссылкой, "
        "аннотацией и датой; это справка по публикациям, а не медицинская "
        "рекомендация."
    ),
    log_level="WARNING",
)


def _run(func, *args, **kwargs):
    """Вызвать сервисную функцию и перевести ValueError в ToolError."""
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    title="Список источников",
    description="Каталог агентов-источников: id, название, описание и подсказка по запросам.",
    annotations=_READ_ONLY,
)
def list_sources() -> dict:
    """Каталог источников для выбора в search_publications."""
    return list_sources_catalog()


@mcp.tool(
    title="Поиск публикаций",
    description=(
        "Проверенный поиск по нескольким источникам: опрашивает агентов и "
        "оставляет только записи, где все слова темы есть в заголовке, "
        "аннотации или тексте страницы. Read-only: история не трогается."
    ),
    annotations=_READ_ONLY,
)
def search_publications(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    sources: Annotated[
        Optional[list[str]],
        Field(description="Id источников (archive, arxiv, openalex, habr, europepmc, minzdrav). Пусто — все."),
    ] = None,
    rows: Annotated[int, Field(description="Сколько записей запрашивать с каждого источника, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Проверенный поиск публикаций по темам и источникам."""
    return _run(_search_publications, topics, sources, rows, date_from, date_to)


def _register_source(source_id: str, title: str, description: str):
    return mcp.tool(title=title, description=description, annotations=_READ_ONLY)


@_register_source(
    "europepmc",
    "Поиск Europe PMC",
    "Сырой поиск по Europe PMC (биомедицинская база EBI): записи с аннотациями, авторами и журналом.",
)
def search_europepmc(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по Europe PMC без валидатора."""
    return _run(_search_source, "europepmc", topics, rows, date_from, date_to)


@_register_source(
    "openalex",
    "Поиск OpenAlex",
    "Сырой поиск по OpenAlex (открытый граф научных знаний): статьи, препринты, книги.",
)
def search_openalex(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по OpenAlex без валидатора."""
    return _run(_search_source, "openalex", topics, rows, date_from, date_to)


@_register_source(
    "arxiv",
    "Поиск arXiv",
    "Сырой поиск по arXiv.org: свежие препринты по всем полям.",
)
def search_arxiv(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по arXiv без валидатора."""
    return _run(_search_source, "arxiv", topics, rows, date_from, date_to)


@_register_source(
    "archive",
    "Поиск archive.org",
    "Сырой поиск по Internet Archive: книги, статьи и документы, поиск фразой целиком.",
)
def search_archive(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по archive.org без валидатора."""
    return _run(_search_source, "archive", topics, rows, date_from, date_to)


@_register_source(
    "habr",
    "Поиск Habr",
    "Сырой поиск по Habr (habr.com): русскоязычные статьи и новости по RSS-ленте.",
)
def search_habr(
    topics: Annotated[list[str], Field(description="Темы, по одной фразе на элемент")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Дата «с», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Дата «по», ГГГГ-ММ-ДД. Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по Habr без валидатора."""
    return _run(_search_source, "habr", topics, rows, date_from, date_to)


@_register_source(
    "minzdrav",
    "Поиск Рубрикатора КР Минздрава",
    "Сырой поиск по Рубрикатору клинических рекомендаций Минздрава РФ: карточки КР по названию, коду МКБ-10 или возрасту. Русскоязычный источник.",
)
def search_minzdrav(
    topics: Annotated[list[str], Field(description="Темы: название заболевания, синоним или код МКБ-10, например «астма» или «J45»")],
    rows: Annotated[int, Field(description="Сколько записей вернуть, 1–100")] = 20,
    date_from: Annotated[Optional[str], Field(description="Год «с» (ГГГГ-ММ-ДД); фильтр идёт по году пересмотра. Пусто — неделя по умолчанию")] = None,
    date_to: Annotated[Optional[str], Field(description="Год «по» (ГГГГ-ММ-ДД). Пусто — неделя по умолчанию")] = None,
) -> dict:
    """Сырой поиск по Рубрикатору КР Минздрава без валидатора."""
    return _run(_search_source, "minzdrav", topics, rows, date_from, date_to)


@mcp.tool(
    title="Резюме текста",
    description="Краткое резюме текста алгоритмом Sumy LSA (английский токенизатор).",
    annotations=_READ_ONLY,
)
def summarize_text(
    text: Annotated[str, Field(description="Текст для резюме (аннотация или текст страницы)")],
    max_sentences: Annotated[int, Field(description="Предложений в резюме, 1–10")] = 3,
) -> dict:
    """Резюме текста тем же движком, что и оркестратор по умолчанию."""
    return _run(_summarize_text, text, max_sentences)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
