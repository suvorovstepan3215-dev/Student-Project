"""Офлайн-проверки агентов-источников: сеть не нужна.

Запуск из корня проекта:

    python tests/test_agents_offline.py

Проверяется контракт агентов-источников, разбор ответов Habr (RSS) и OpenAlex
(JSON) на фикстурах, повторяющих форму реальных ответов, работа валидатора с
этими записями и ASCII-безопасность HTTP-заголовков.
"""

import inspect
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import utils  # noqa: E402  (импорт настраивает общий логгер проекта)
import habr_agent  # noqa: E402
import openalex_agent  # noqa: E402
import scraper  # noqa: E402
import validator  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="agents-offline-"))
utils.HISTORY_PATH = TMP / "history.json"
utils.CONFIG_PATH = TMP / "config.yaml"
utils.CONFIG_PATH.write_text("topics:\n  - multi-agent\nsources: [habr]\n", encoding="utf-8")

FAILURES = []


def check(name, condition, extra=""):
    print(("  ✅ " if condition else "  ❌ ") + name + (f" — {extra}" if extra else ""))
    if not condition:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------
# Лента Habr: CDATA в заголовках, HTML в лиде, хвост «Читать далее», utm-метки
# в guid, автор в dc:creator и в author, теги в categories, запись без pubDate.
HABR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title><![CDATA[Результаты поиска по запросу «multi-agent»]]></title>
    <link>https://habr.com/ru/</link>
    <item>
      <guid isPermaLink="false">https://habr.com/ru/articles/1037770/?utm_source=habrahabr&amp;utm_medium=rss&amp;utm_campaign=1037770</guid>
      <title><![CDATA[Как мы проектировали multi-agent feedback для обучения рисованию]]></title>
      <link>https://habr.com/ru/articles/1037770/</link>
      <description><![CDATA[<p>Инженерный разбор про multi-agent feedback:
три LLM-персоны и судья.</p>
<a href="https://habr.com/ru/articles/1037770/#habracut">Читать далее</a>]]></description>
      <pubDate>Thu, 21 May 2026 11:51:41 GMT</pubDate>
      <dc:creator>artem_dev</dc:creator>
      <categories><category>ai agent</category><category>multi-agent</category></categories>
    </item>
    <item>
      <title><![CDATA[Kimi выпустили открытую модель K2.6]]></title>
      <link>https://habr.com/ru/news/1025736/</link>
      <guid isPermaLink="false">https://habr.com/ru/news/1025736/?utm_source=habrahabr&amp;utm_medium=rss</guid>
      <description><![CDATA[<p>Открытая модель для долгих задач и агентных сценариев.</p>
<a href="https://habr.com/ru/news/1025736/#habracut">Read more</a>]]></description>
      <pubDate>Mon, 20 Apr 2026 15:49:39 GMT</pubDate>
      <author>rueditor (rueditor@habr.com)</author>
      <categories><category>llm</category></categories>
    </item>
    <item>
      <title><![CDATA[LLM/AI Firewall и Agent Runtime Security]]></title>
      <link>https://habr.com/ru/companies/infera_security/articles/1053878/</link>
      <description><![CDATA[<p>Защита мультиагентных систем в 2026 году.</p>]]></description>
      <pubDate>Tue, 30 Jun 2026 10:11:32 GMT</pubDate>
    </item>
    <item>
      <title><![CDATA[Публикация без даты]]></title>
      <link>https://habr.com/ru/articles/900001/</link>
      <description><![CDATA[<p>multi-agent без pubDate</p>]]></description>
    </item>
  </channel>
</rss>"""

OPENALEX_WORK = {
    "id": "https://openalex.org/W4417100132",
    "doi": "https://doi.org/10.48550/arxiv.2510.10185",
    "title": "Auditing medical multi-agent AI reveals risks of false consensus",
    "publication_date": "2025-10-11",
    "publication_year": 2025,
    "cited_by_count": 3,
    "authorships": [{"author": {"display_name": "Zhu, Yinghao"}}, {"author": {"display_name": "Gu, Lei"}}],
    "primary_location": {"landing_page_url": "https://arxiv.org/abs/2510.10185",
                         "source": {"display_name": "arXiv (Cornell University)"}},
    "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/2510.10185"},
    "abstract_inverted_index": {"Large": [0], "language": [1], "models": [2], "assembled": [3],
                                "into": [4], "medical": [5], "multi-agent": [6], "systems": [7]},
}


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content if isinstance(content, bytes) else str(content).encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_requests(responses):
    """Подмена ``requests`` в модуле агента: список ответов по порядку вызовов."""
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "headers": headers})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        return item() if callable(item) else item

    module = type("FakeRequests", (), {"get": staticmethod(get)})()
    return module, calls


pauses = []
habr_agent.time = type("FakeTime", (), {"sleep": staticmethod(pauses.append)})()


# ---------------------------------------------------------------------------
print("\n1. Контракт агентов-источников")
for sid, module in scraper.SOURCES.items():
    check(f"{sid}: SOURCE_ID совпадает с ключом реестра", module.SOURCE_ID == sid, module.SOURCE_ID)
    check(f"{sid}: SOURCE_NAME задан", bool(getattr(module, "SOURCE_NAME", "")), module.SOURCE_NAME)
    signature = inspect.signature(module.fetch)
    check(f"{sid}: fetch(topics, rows, date_from, date_to)",
          list(signature.parameters) == ["topics", "rows", "date_from", "date_to"],
          str(list(signature.parameters)))
check("новый источник виден в интерфейсе",
      "habr.com" in scraper.SOURCE_NAMES.values() and "openalex.org" in scraper.SOURCE_NAMES.values(),
      str(scraper.SOURCE_NAMES))
check("источники подключены в config.yaml проекта",
      {"openalex", "habr"} <= set(__import__("yaml").safe_load(
          (PROJECT_DIR / "config.yaml").read_text(encoding="utf-8")).get("sources", [])))

print("\n2. HTTP-заголовки ASCII-безопасны (регрессия на latin-1)")
for sid, module in scraper.SOURCES.items():
    values = []
    user_agent = getattr(module, "USER_AGENT", None)
    if user_agent:
        values.append(("USER_AGENT", user_agent))
    headers = getattr(module, "HEADERS", None)
    if isinstance(headers, dict):
        values += [(f"HEADERS[{key}]", value) for key, value in headers.items()]
    for name, value in values:
        try:
            str(value).encode("latin-1")
            ok, detail = True, str(value)[:60]
        except UnicodeEncodeError as exc:
            ok, detail = False, f"{exc} в {value!r}"
        check(f"{sid}: {name} кодируется в latin-1", ok, detail)
    if not values:
        check(f"{sid}: своих заголовков нет — проверять нечего", True)

print("\n3. Habr: разбор RSS")
pairs = habr_agent.parse_feed(HABR_RSS.encode("utf-8"), date(2026, 4, 1), date(2026, 6, 30))
check("в диапазон попали 3 записи из 4", len(pairs) == 3, str([item["identifier"] for _, item in pairs]))
items = [item for _, item in sorted(pairs, key=lambda pair: pair[0], reverse=True)]
check("сортировка по убыванию даты",
      [i["published"] for i in items] == ["2026-06-30", "2026-05-21", "2026-04-20"],
      str([i["published"] for i in items]))
first, second, third = items[1], items[2], items[0]
check("идентификатор статьи", first["identifier"] == "habr:1037770", first["identifier"])
check("ссылка без utm-меток", first["url"] == "https://habr.com/ru/articles/1037770/", first["url"])
check("заголовок из CDATA", first["title"].startswith("Как мы проектировали multi-agent"), first["title"])
check("лид очищен от HTML и от «Читать далее»",
      first["abstract"] == "Инженерный разбор про multi-agent feedback: три LLM-персоны и судья.",
      first["abstract"])
check("автор из dc:creator", first["authors"] == "artem_dev", first["authors"])
check("теги через запятую", first["tags"] == "ai agent, multi-agent", first["tags"])
check("автор из author без почты", second["authors"] == "rueditor", second["authors"])
check("английский хвост «Read more» тоже отброшен",
      not second["abstract"].endswith("Read more"), second["abstract"])
check("новость: идентификатор из /news/", second["identifier"] == "habr:1025736", second["identifier"])
check("статья компании: идентификатор из длинной ссылки",
      third["identifier"] == "habr:1053878", third["identifier"])
check("нет автора в ленте — поле пустое", third["authors"] == "")
check("крайняя дата диапазона входит в выборку", third["published"] == "2026-06-30")
check("запись без pubDate отброшена", all(i["identifier"] != "habr:900001" for i in items))
check("битый XML не роняет агента", habr_agent.parse_feed(b"<rss><channel>", date(2026, 1, 1), date(2026, 2, 1)) == [])
check("параметры запроса к Habr",
      habr_agent.build_params("multi-agent") == {"q": "multi-agent", "target_type": "posts", "order": "date"})

print("\n4. Habr: fetch по нескольким темам")
module, calls = fake_requests([FakeResponse(HABR_RSS.encode("utf-8"))])
habr_agent.requests = module
found = habr_agent.fetch(["multi-agent", "ai agent"], rows=2,
                         date_from=date(2026, 4, 1), date_to=date(2026, 6, 30))
check("по одному запросу на тему", len(calls) == 2, f"запросов {len(calls)}")
check("пауза между запросами", pauses == [1.0], str(pauses))
check("темы пришли в параметре q", [c["params"]["q"] for c in calls] == ["multi-agent", "ai agent"])
check("одинаковые публикации склеены по идентификатору", len(found) == 2, str([i["identifier"] for i in found]))
check("лимит rows соблюдён", len(found) == 2 and found[0]["published"] == "2026-06-30")
check("в запрос ушёл ASCII User-Agent",
      calls[0]["headers"]["User-Agent"] == habr_agent.USER_AGENT
      and calls[0]["headers"]["User-Agent"].encode("latin-1").decode("latin-1") == habr_agent.USER_AGENT,
      calls[0]["headers"]["User-Agent"])
module, calls = fake_requests([lambda: FakeResponse(b"", 403)])
habr_agent.requests = module
check("403 не роняет цикл, записей нет",
      habr_agent.fetch(["multi-agent"], rows=5) == [] and len(calls) == 1, f"запросов {len(calls)}")
requests_before = len(calls)
check("пустые темы — без запросов к сайту",
      habr_agent.fetch(["  ", ""], rows=5) == [] and len(calls) == requests_before)

print("\n5. OpenAlex: разбор JSON")
record = openalex_agent._to_item(OPENALEX_WORK)
check("идентификатор с префиксом", record["identifier"] == "openalex:W4417100132", record["identifier"])
check("аннотация из перевёрнутого индекса",
      record["abstract"] == "Large language models assembled into medical multi-agent systems",
      record["abstract"])
check("ссылка — landing page", record["url"] == "https://arxiv.org/abs/2510.10185")
check("DOI без префикса", record["doi"] == "10.48550/arxiv.2510.10185")
check("авторы, площадка, цитирования",
      record["authors"] == "Zhu, Yinghao, Gu, Lei" and record["venue"] == "arXiv (Cornell University)"
      and record["cited_by_count"] == 3)
check("запрос: слова темы через пробел, темы через |",
      openalex_agent.build_text_query(["medical multi-agent", "clinical decision support"])
      == "medical multi-agent|clinical decision support")

print("\n6. Валидатор на записях Habr и OpenAlex")
check("Habr не входит в источники с полной аннотацией",
      validator.SOURCES_WITH_ABSTRACT == {"arxiv", "openalex"}, str(validator.SOURCES_WITH_ABSTRACT))
downloads = []
validator.extract_text_from_url = lambda url: downloads.append(url) or "текст страницы про multi-agent системы"
habr_item = dict(first, source="habr")
check("тема найдена в лиде — страница не нужна",
      validator.validate_item(habr_item, ["multi-agent"]) is True and downloads == [])
leadless = dict(habr_item, identifier="habr:1053878", title="LLM/AI Firewall", abstract="Защита систем в 2026 году")
check("лид без темы — валидатор дочитал страницу",
      validator.validate_item(leadless, ["multi-agent"]) is True and downloads == [leadless["url"]],
      str(downloads))
downloads.clear()
validator.extract_text_from_url = lambda url: downloads.append(url) or "совсем другой текст"
check("ни в лиде, ни на странице темы нет — отклонено",
      validator.validate_item(leadless, ["multi-agent"]) is False and downloads == [leadless["url"]])
downloads.clear()
check("OpenAlex с аннотацией — страница не скачивается",
      validator.validate_item(dict(record, source="openalex"), ["medical multi-agent"]) is True
      and downloads == [])

print("\n7. Скрапер: Habr в общем цикле")
module, calls = fake_requests([FakeResponse(HABR_RSS.encode("utf-8"))])
habr_agent.requests = module
# Страница публикации не содержит слов темы: принимаются только записи с темой в лиде.
validator.extract_text_from_url = lambda url: "текст страницы про защиту информационных систем"
accepted = scraper.get_new_items(topics=["multi-agent"], sources=["habr"], rows=5,
                                 skip_seen=True, save_history=True,
                                 date_from=date(2026, 4, 1), date_to=date(2026, 6, 30))
ids = [item["identifier"] for item in accepted]
check("принята запись с темой в лиде", ids == ["habr:1037770"], str(ids))
check("записи без темы отклонены",
      "habr:1053878" not in ids and "habr:1025736" not in ids, str(ids))
check("история пополнена префиксами habr:", utils.get_history() == ids, str(utils.get_history()))

print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILURES else f"ПРОВАЛЕНО {len(FAILURES)}: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
