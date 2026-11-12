"""Офлайн-проверки агентов-источников: сеть не нужна.

Запуск из корня проекта:

    python tests/test_agents_offline.py

Проверяется контракт агентов-источников, разбор ответов Habr (RSS), OpenAlex
и Europe PMC (JSON) на фикстурах, повторяющих форму реальных ответов, работа
валидатора с этими записями, ASCII-безопасность HTTP-заголовков и сервисный
слой MCP (каталог, даты, поиск, резюме).
"""

import inspect
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import utils  # noqa: E402  (импорт настраивает общий логгер проекта)
import europepmc_agent  # noqa: E402
import habr_agent  # noqa: E402
import mcp_service  # noqa: E402
import minzdrav_agent  # noqa: E402
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

# Ответ Europe PMC (resultType=core): HTML-теги в заголовке, аннотация,
# журнал, даты, цитирования, ключевые слова; вторая запись без PMCID (только PMID).
EUROPEPMC_RESPONSE = {
    "version": "6.9",
    "hitCount": 2,
    "resultList": {
        "result": [
            {
                "id": "40724673",
                "source": "MED",
                "pmid": "40724673",
                "pmcid": "PMC12294997",
                "doi": "10.3390/healthcare13141649",
                "title": "<i>MedScrubCrew</i>: A Medical Multi-Agent Framework for Scheduling",
                "authorString": "Ruiz Mejia JM, Rawat DB.",
                "journalInfo": {
                    "journal": {"title": "Healthcare (Basel, Switzerland)"},
                    "printPublicationDate": "2025-07-01",
                },
                "pubYear": "2025",
                "abstractText": "Background: medical multi-agent systems need context. "
                                "Methods: we present a multi-agent framework for scheduling.",
                "keywordList": {"keyword": ["Multi-agent system", "Clinical Decision Support"]},
                "citedByCount": 5,
                "firstPublicationDate": "2025-07-08",
            },
            {
                "id": "42907512",
                "source": "MED",
                "pmid": "42907512",
                "doi": "10.1038/s41586-026-11161-8",
                "title": "The anatomy of a personal health agent",
                "authorString": "Heydari AA, Gu K.",
                "journalInfo": {"journal": {"title": "Nature"}},
                "pubYear": "2026",
                "abstractText": "A personal health agent for medical multi-agent care.",
                "citedByCount": 0,
                "firstPublicationDate": "2026-01-15",
            },
        ]
    },
}

# Страница класса МКБ-10 рубрикатора (зеркало minzdrav.clinirec.ru): компактный
# список КР — ссылки /kr/ с текстом «Название МКБ-10: КОДЫ ВОЗРАСТ ГОД».
# Четвёртая ссылка ведёт не на КР (не /kr/), её агент должен пропустить.
MINZDRAV_CLASS_HTML = """<html><body>
<a href="https://minzdrav.clinirec.ru/kr/bronhialnaya-astma/">Бронхиальная астмаМКБ-10: J45, J45.0, J45.8, J45.9, J46Взрослые, дети2024</a>
<a href="https://minzdrav.clinirec.ru/kr/askaridoz/">АскаридозМКБ-10: B77Взрослые, дети2025</a>
<a href="https://minzdrav.clinirec.ru/kr/vich-infekciya-u-vzroslyh/">ВИЧ-инфекция у взрослыхМКБ-10: B20, B21, B22, B23, B24Взрослые2024</a>
<a href="https://minzdrav.clinirec.ru/mkb-10/kod/a01/">Брюшной тиф (не карточка КР)</a>
</body></html>"""


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        if isinstance(content, dict):
            import json as _json
            content = _json.dumps(content).encode("utf-8")
        self.content = content if isinstance(content, bytes) else str(content).encode("utf-8")
        self.status_code = status_code

    def json(self):
        import json as _json
        return _json.loads(self.content.decode("utf-8"))

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
europepmc_agent.time = type("FakeTime", (), {"sleep": staticmethod(pauses.append)})()


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
      "habr.com" in scraper.SOURCE_NAMES.values() and "openalex.org" in scraper.SOURCE_NAMES.values()
      and "Europe PMC" in scraper.SOURCE_NAMES.values(),
      str(scraper.SOURCE_NAMES))
check("источники подключены в config.yaml проекта",
      {"openalex", "habr", "europepmc"} <= set(__import__("yaml").safe_load(
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

print("\n6. Валидатор на записях Habr, OpenAlex и Europe PMC")
check("Habr не входит в источники с полной аннотацией",
      validator.SOURCES_WITH_ABSTRACT == {"arxiv", "openalex", "europepmc", "minzdrav"},
      str(validator.SOURCES_WITH_ABSTRACT))
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

print("\n7.5 Минздрав: разбор страницы класса МКБ-10 и каталог")
entries = minzdrav_agent._parse_class_page(MINZDRAV_CLASS_HTML)
check("из 4 ссылок распознано 3 карточки КР (не /kr/ пропущена)", len(entries) == 3, str([e["slug"] for e in entries]))
asthma = next(e for e in entries if e["slug"] == "bronhialnaya-astma")
check("название без «МКБ-10:»", asthma["title"] == "Бронхиальная астма", asthma["title"])
check("коды МКБ-10 выделены", asthma["codes"] == "J45, J45.0, J45.8, J45.9, J46", asthma["codes"])
check("возраст — составная категория", asthma["age"] == "Взрослые, дети", asthma["age"])
check("год пересмотра выделен", asthma["year"] == 2024, str(asthma["year"]))
check("ссылка абсолютная из href", asthma["url"].endswith("/kr/bronhialnaya-astma/"), asthma["url"])
catalog = minzdrav_agent.build_catalog(pages={"https://minzdrav.clinirec.ru/mkb-10/i/": MINZDRAV_CLASS_HTML})
check("каталог ключом по slug, 3 записи", set(catalog) == {"bronhialnaya-astma", "askaridoz", "vich-infekciya-u-vzroslyh"}, str(set(catalog)))
check("запись без «МКБ-10:» не разбирается", minzdrav_agent._parse_entry("Просто текст без кодов", "/kr/x/", "x") is None)

print("\n7.6 Минздрав: fetch по теме и фильтр по году пересмотра")
_real_get = minzdrav_agent._http_get
minzdrav_agent._http_get = lambda url: MINZDRAV_CLASS_HTML
try:
    found_asthma = minzdrav_agent.fetch(["астма"], rows=10, date_from=date(2015, 1, 1), date_to=date(2026, 12, 31))
    check("тема «астма» находит Бронхиальную астму (2024)",
          [i["identifier"] for i in found_asthma] == ["minzdrav:bronhialnaya-astma"],
          str([i["identifier"] for i in found_asthma]))
    check("аннотация содержит коды МКБ-10", "МКБ-10:" in found_asthma[0]["abstract"], found_asthma[0]["abstract"][:60])
    check("дата публикации — 1 января года пересмотра", found_asthma[0]["published"] == "2024-01-01", found_asthma[0]["published"])
    found_by_code = minzdrav_agent.fetch(["J45"], rows=10, date_from=date(2015, 1, 1), date_to=date(2026, 12, 31))
    check("поиск по коду МКБ-10 J45 тоже находит астму", found_by_code and found_by_code[0]["identifier"] == "minzdrav:bronhialnaya-astma")
    found_hiv = minzdrav_agent.fetch(["ВИЧ"], rows=10, date_from=date(2015, 1, 1), date_to=date(2026, 12, 31))
    check("тема «ВИЧ» находит соответствующую КР", found_hiv and found_hiv[0]["identifier"] == "minzdrav:vich-infekciya-u-vzroslyh")
    # Фильтр по году пересмотра: астма (2024) вне окна 2025–2026, аскаридоз (2025) — внутри.
    none = minzdrav_agent.fetch(["астма"], rows=10, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    check("окно 2025–2026 исключает астму (пересмотр 2024)", none == [], str([i["identifier"] for i in none]))
    ask = minzdrav_agent.fetch(["аскаридоз"], rows=10, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    check("окно 2025–2026 включает аскаридоз (пересмотр 2025)", ask and ask[0]["identifier"] == "minzdrav:askaridoz", str([i["identifier"] for i in ask]))
    empty = minzdrav_agent.fetch(["  ", ""], rows=10, date_from=date(2015, 1, 1), date_to=date(2026, 12, 31))
    check("пустые темы — без результата", empty == [])
finally:
    minzdrav_agent._http_get = _real_get

print("\n8. Europe PMC: разбор JSON")
ep_record = europepmc_agent._to_item(EUROPEPMC_RESPONSE["resultList"]["result"][0])
check("идентификатор по PMCID", ep_record["identifier"] == "europepmc:PMC12294997", ep_record["identifier"])
check("заголовок очищен от HTML", ep_record["title"] == "MedScrubCrew: A Medical Multi-Agent Framework for Scheduling",
      ep_record["title"])
check("ссылка — страница статьи Europe PMC", ep_record["url"] == "https://europepmc.org/articles/PMC12294997",
      ep_record["url"])
check("авторы, журнал, цитирования, ключевые слова",
      ep_record["authors"] == "Ruiz Mejia JM, Rawat DB." and ep_record["venue"] == "Healthcare (Basel, Switzerland)"
      and ep_record["cited_by_count"] == 5 and ep_record["keywords"] == "Multi-agent system, Clinical Decision Support")
check("дата первой публикации", ep_record["published"] == "2025-07-08", ep_record["published"])
ep_no_pmcid = europepmc_agent._to_item(EUROPEPMC_RESPONSE["resultList"]["result"][1])
check("без PMCID — идентификатор по PMID и ссылка на PubMed",
      ep_no_pmcid["identifier"] == "europepmc:42907512"
      and ep_no_pmcid["url"] == "https://europepmc.org/article/MED/42907512",
      f"{ep_no_pmcid['identifier']} {ep_no_pmcid['url']}")
check("запрос: темы фразами через OR и FIRST_PDATE",
      europepmc_agent.build_query(["medical multi-agent", "clinical AI"], date(2026, 1, 1), date(2026, 1, 31))
      == '("medical multi-agent" OR "clinical AI") AND FIRST_PDATE:[2026-01-01 TO 2026-01-31]',
      europepmc_agent.build_query(["medical multi-agent", "clinical AI"], date(2026, 1, 1), date(2026, 1, 31)))

print("\n9. Europe PMC: fetch с подменой сети")
module, calls = fake_requests([FakeResponse(EUROPEPMC_RESPONSE)])
europepmc_agent.requests = module
found_ep = europepmc_agent.fetch(["medical multi-agent"], rows=5,
                                 date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
check("один запрос к API", len(calls) == 1, f"запросов {len(calls)}")
check("адрес API Europe PMC", calls[0]["url"] == europepmc_agent.EUROPEPMC_API_URL, calls[0]["url"])
check("параметры: core, json, лимит", calls[0]["params"].get("resultType") == "core"
      and calls[0]["params"].get("format") == "json" and calls[0]["params"].get("pageSize") == 5,
      str(calls[0]["params"]))
check("сортировка по убыванию даты", [i["published"] for i in found_ep] == ["2026-01-15", "2025-07-08"],
      str([i["published"] for i in found_ep]))
check("обе записи отданы", len(found_ep) == 2, str(len(found_ep)))
module, calls = fake_requests([lambda: FakeResponse(b"", 500)])
europepmc_agent.requests = module
try:
    europepmc_agent.fetch(["medical multi-agent"], rows=5)
    check("500 после трёх попыток — ошибка", False, "исключение не брошено")
except RuntimeError:
    check("500 после трёх попыток — ошибка", len(calls) == 3, f"запросов {len(calls)}")
check("пустые темы — без запросов к сайту",
      europepmc_agent.fetch(["  ", ""], rows=5) == [])

print("\n10. MCP: сервисный слой без транспорта")
catalog = mcp_service.list_sources_catalog()
catalog_ids = [source["id"] for source in catalog["sources"]]
check("каталог содержит все источники", set(catalog_ids) == set(scraper.SOURCES), str(catalog_ids))
check("каталог: у каждого источника название и описание",
      all(source["name"] and source["description"] for source in catalog["sources"]))
check("parse_date: валидная дата", mcp_service.parse_date("2026-01-15", "date_from") == date(2026, 1, 15))
check("parse_date: пусто — None", mcp_service.parse_date("", "date_from") is None
      and mcp_service.parse_date(None, "date_from") is None)
try:
    mcp_service.parse_date("15.01.2026", "date_from")
    check("parse_date: битая дата — ошибка", False, "исключение не брошено")
except ValueError:
    check("parse_date: битая дата — ошибка", True)
try:
    mcp_service.resolve_dates("2026-02-01", "2026-01-01")
    check("resolve_dates: from позже to — ошибка", False, "исключение не брошено")
except ValueError:
    check("resolve_dates: from позже to — ошибка", True)
try:
    mcp_service.search_source("unknown", ["multi-agent"])
    check("search_source: неизвестный источник — ошибка", False, "исключение не брошено")
except ValueError:
    check("search_source: неизвестный источник — ошибка", True)
module, calls = fake_requests([FakeResponse(EUROPEPMC_RESPONSE)])
europepmc_agent.requests = module
raw = mcp_service.search_source("europepmc", ["medical multi-agent"], rows=5,
                                date_from="2025-01-01", date_to="2026-12-31")
check("search_source: сырой поиск Europe PMC", raw["count"] == 2 and raw["source"] == "europepmc",
      str(raw["count"]))
check("search_source: даты в ответе", raw["date_from"] == "2025-01-01" and raw["date_to"] == "2026-12-31")
module, calls = fake_requests([FakeResponse(EUROPEPMC_RESPONSE)])
europepmc_agent.requests = module
validator.extract_text_from_url = lambda url: "совсем другой текст"
checked = mcp_service.search_publications(["medical multi-agent"], sources=["europepmc"], rows=5,
                                          date_from="2025-01-01", date_to="2026-12-31")
check("search_publications: валидатор принял обе записи", checked["count"] == 2, str(checked["count"]))
check("search_publications: история не тронута", utils.get_history() == ids, str(utils.get_history()))
# Sumy требует данные NLTK (punkt_tab), которых может не быть на чистой машине,
# поэтому движок подменяем: проверяем логику сервиса, а не сам LSA.
_real_summarize = mcp_service.summarize
mcp_service.summarize = lambda text, max_sentences=3, engine="sumy": "Резюме по тексту."
summary = mcp_service.summarize_text(
    "Medical multi-agent systems coordinate several agents. "
    "Each agent handles a narrow task. Together they improve scheduling workflows.", 2)
mcp_service.summarize = _real_summarize
check("summarize_text: резюме непустое", bool(summary["summary"]), summary["summary"][:80])
try:
    mcp_service.summarize_text("   ", 3)
    check("summarize_text: пустой текст — ошибка", False, "исключение не брошено")
except ValueError:
    check("summarize_text: пустой текст — ошибка", True)

print("\n11. MCP: регистрация tools на сервере")
try:
    import asyncio as _asyncio
    import mcp_server as _mcp_server
    _tools = _asyncio.run(_mcp_server.mcp.list_tools())
    _names = {tool.name for tool in _tools}
    _expected = {"list_sources", "search_publications", "search_europepmc", "search_openalex",
                 "search_arxiv", "search_archive", "search_habr", "search_minzdrav", "summarize_text"}
    check("сервер регистрирует все tools", _names == _expected, str(sorted(_names)))
    _result = _asyncio.run(_mcp_server.mcp.call_tool("list_sources", {}))
    import json as _json
    _payload = _result.structured_content
    if _payload is None:
        _payload = _json.loads(_result.content[0].text)
    check("tool list_sources отвечает каталогом",
          {s["id"] for s in _payload["sources"]} == set(scraper.SOURCES))
except ImportError as _exc:
    check(f"MCP-сервер импортируется (пропуск без пакета mcp: {_exc})", True)

print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILURES else f"ПРОВАЛЕНО {len(FAILURES)}: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
