"""Офлайн-тесты онлайн-переводчика: сеть подменяется фейком, наружу ничего не уходит."""

import pytest

import translator


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def gtx_payload(text):
    return [[[text, "original", None, None, 10]], None, "en"]


def test_mostly_cyrillic_detection():
    assert translator.mostly_cyrillic("Резюме на русском языке про диабет")
    assert not translator.mostly_cyrillic("A short abstract about diabetes care")
    assert not translator.mostly_cyrillic("")
    assert translator.mostly_cyrillic("Диабет 2 типа: management guideline")


def test_translate_text_sends_gtx_request_and_joins_segments(monkeypatch):
    calls = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["params"] = kwargs.get("params")
        return FakeResponse([[["Привет, ", "Hello, "], ["мир", "world"]], None, "en"])

    monkeypatch.setattr(translator.requests, "get", fake_get)
    assert translator.translate_text("Hello, world", "ru") == "Привет, мир"
    assert calls["params"]["tl"] == "ru" and calls["params"]["sl"] == "auto"
    assert calls["params"]["client"] == "gtx" and calls["params"]["q"] == "Hello, world"


def test_translate_text_truncates_long_input(monkeypatch):
    calls = {}

    def fake_get(url, **kwargs):
        calls["params"] = kwargs.get("params")
        return FakeResponse(gtx_payload("x"))

    monkeypatch.setattr(translator.requests, "get", fake_get)
    translator.translate_text("a" * 5000, "ru")
    assert len(calls["params"]["q"]) == translator.MAX_INPUT_CHARS


def test_translate_text_failures_raise_translator_unavailable(monkeypatch):
    monkeypatch.setattr(translator.requests, "get", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(translator.TranslatorUnavailable):
        translator.translate_text("text", "ru")
    monkeypatch.setattr(translator.requests, "get", lambda *a, **k: FakeResponse(ValueError("not json")))
    with pytest.raises(translator.TranslatorUnavailable):
        translator.translate_text("text", "ru")
    monkeypatch.setattr(translator.requests, "get", lambda *a, **k: FakeResponse([[]]))
    with pytest.raises(translator.TranslatorUnavailable):
        translator.translate_text("text", "ru")


def test_translate_text_rejects_bad_target_and_empty_input():
    with pytest.raises(ValueError):
        translator.translate_text("text", "de")
    with pytest.raises(translator.TranslatorUnavailable):
        translator.translate_text("   ", "ru")
