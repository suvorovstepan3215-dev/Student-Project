import io
import json
from datetime import date
from types import SimpleNamespace

import requests

START, END = date(2026, 1, 1), date(2026, 12, 31)


class FakeResponse:
    def __init__(self, payload=None, *, status=200, content=None, headers=None, url="https://example.com/"):
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()
        self.text = self.content.decode("utf-8", errors="replace")
        self.is_redirect = status in {301, 302, 303, 307, 308} and "Location" in self.headers
        self.is_permanent_redirect = status in {301, 308} and "Location" in self.headers
        self.closed = False
        self._stream = io.BytesIO(self.content)
        self.raw = SimpleNamespace(read1=lambda size, **kwargs: self._stream.read1(size), connection=None)

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("Synthetic HTTP error", response=self)

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        self.closed = True


def item(identifier="europepmc:1", **overrides):
    return dict(
        {
            "source": "europepmc",
            "identifier": identifier,
            "title": "Diabetes research",
            "url": "https://europepmc.org/article/MED/1",
            "abstract": "Diabetes needs careful study. More evidence is required.",
            "published": "2026-02-01",
        },
        **overrides,
    )


def ep_record(identifier="1", **overrides):
    return dict(
        {
            "id": identifier,
            "source": "MED",
            "pmid": identifier,
            "title": "<i>Diabetes</i>: a study",
            "authorString": "Example A.",
            "abstractText": "Diabetes needs careful study. More evidence is required.",
            "firstPublicationDate": "2026-02-01",
            "citedByCount": 2,
            "journalInfo": {"journal": {"title": "Synthetic journal"}},
            "keywordList": {"keyword": ["diabetes", "review"]},
        },
        **overrides,
    )


def ep_payload(records, **overrides):
    return dict({"hitCount": len(records), "resultList": {"result": records}}, **overrides)


def mirror_entry(slug="asthma", year=2024, **overrides):
    return dict(
        {
            "slug": slug,
            "title": "Бронхиальная астма",
            "codes": "J45, J46",
            "age": "Взрослые, дети",
            "year": year,
            "url": "https://minzdrav.clinirec.ru/kr/" + slug + "/",
            "backend": "mirror",
        },
        **overrides,
    )


def mirror_html(slug="asthma", title="Бронхиальная астма", year=2024, extra=""):
    return f'<html><body><a href="/kr/{slug}/">{title}МКБ-10: J45, J46Взрослые, дети{year}</a>{extra}</body></html>'


# Synthetic examples of the publicly referenced FilterV2 field schema, NOT
# evidence of live API availability or the authenticity of any guideline.
def official_record(code="123_1", **overrides):
    return dict(
        {
            "CodeVersion": code,
            "Name": "Сахарный диабет (тестовая карточка)",
            "PublishDateStr": "15.02.2026",
            "Status": 0,
            "Version": 1,
            "AgeCategoryStr": "Взрослые",
            "Mkbs": [{"MkbCode": "E11"}],
        },
        **overrides,
    )


HABR_XML = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
<title>Diabetes research</title><link>https://habr.com/ru/articles/123456/</link>
<description><![CDATA[<p>Diabetes research and evidence.</p><a>Read more</a>]]></description>
<pubDate>Sun, 15 Feb 2026 12:00:00 GMT</pubDate><dc:creator>example</dc:creator>
<category>medicine</category></item></channel></rss>"""
