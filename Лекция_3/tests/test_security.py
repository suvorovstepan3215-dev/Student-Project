import logging
import socket
from types import SimpleNamespace

import pytest
import requests

import network
import security
import summarizer
import utils
from tests.helpers import FakeResponse


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "http://localhost/",
        "http://foo.local/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
        "http://10.1.2.3/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fc00::1]/",
        "http://[2002:7f00:1::]/",
        "http://user:pass@example.com/",
        "https://example.com:8080/",
        "https://example.com/\r\nFoo:bar",
        "https://example.com\\@127.0.0.1/",
        "http://[fe80::1%25eth0]/",
        "https:///missing-host",
    ],
)
def test_unsafe_urls_rejected_before_request(url):
    with pytest.raises(ValueError):
        security.normalize_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path?q=1",
        "https://8.8.8.8/",
        "http://example.com/",
        "https://[2606:4700:4700::1111]/",
    ],
)
def test_public_url_syntax(url):
    assert security.normalize_url(url) == url


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["169.254.169.254"], ["::1"]])
def test_dns_private_or_mixed_answer_never_connects(monkeypatch, addresses):
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in addresses
        ],
    )
    monkeypatch.setattr(
        network, "create_connection", lambda *a, **k: pytest.fail("private address reached the socket")
    )
    with pytest.raises(ValueError):
        network._PublicHTTPSConnection("public.example", port=443)._new_conn()


def test_dns_public_address_is_pinned_and_tls_hostname_retained(monkeypatch):
    calls = []
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(
        network, "create_connection", lambda address, *a, **k: calls.append(address) or "socket"
    )
    conn = network._PublicHTTPSConnection("public.example", port=443)
    assert conn._new_conn() == "socket"
    assert calls == [("8.8.8.8", 443)] and conn.host == "public.example"


def fake_client(monkeypatch, responses, model=False):
    calls = []
    client = network._HTTPClient(model=model)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(
        client,
        "_session",
        lambda _: SimpleNamespace(request=request, cookies=SimpleNamespace(clear=lambda: None)),
    )
    return client, calls


def test_redirect_to_metadata_ip_blocked(monkeypatch):
    response = FakeResponse(status=302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    client, calls = fake_client(monkeypatch, [response])
    with pytest.raises(ValueError):
        client.get("https://example.com/")
    assert len(calls) == 1 and response.closed


def test_redirect_cross_origin_secret_query_not_forwarded(monkeypatch):
    response = FakeResponse(status=302, headers={"Location": "https://attacker.example/?api_key=synthetic"})
    client, calls = fake_client(monkeypatch, [response])
    with pytest.raises(ValueError):
        client.get("https://api.openalex.org/works", params={"api_key": "synthetic"})
    assert len(calls) == 1


def test_https_downgrade_is_blocked(monkeypatch):
    client, calls = fake_client(
        monkeypatch, [FakeResponse(status=302, headers={"Location": "http://example.com/"})]
    )
    with pytest.raises(ValueError):
        client.get("https://example.com/")
    assert len(calls) == 1


def test_model_redirect_never_forwards_bearer(monkeypatch):
    client, calls = fake_client(
        monkeypatch, [FakeResponse(status=307, headers={"Location": "https://evil.example/"})], model=True
    )
    with pytest.raises(ValueError):
        client.post(
            "https://api.example/v1/chat/completions",
            headers={"Authorization": "Bearer synthetic"},
            allowed_base_url="https://api.example/v1",
        )
    assert len(calls) == 1


@pytest.mark.parametrize("headers,body", [({"Content-Length": "1000"}, b"tiny"), ({}, b"X" * 20)])
def test_response_size_cap_including_decompressed_body(monkeypatch, headers, body):
    response = FakeResponse(content=body, headers=headers)
    client, _ = fake_client(monkeypatch, [response])
    with pytest.raises(requests.RequestException):
        client.get("https://example.com/", max_bytes=10)
    assert response.closed


def test_manual_redirect_does_not_buffer_redirect_body():
    session = network._NoRedirectSession()
    assert list(session.resolve_redirects(None, None)) == []


def test_sessions_ignore_proxies_netrc_and_never_disable_tls():
    session = network._HTTPClient()._session(False)
    assert session.trust_env is False
    assert session.verify is not False
    assert isinstance(session.get_adapter("https://example.com"), network._PublicAdapter)


@pytest.mark.parametrize(
    "kwargs", [{"verify": False}, {"proxies": {}}, {"auth": ("a", "b")}, {"allow_private": True}]
)
def test_source_policy_cannot_be_overridden(kwargs):
    with pytest.raises(ValueError):
        network._HTTPClient().get("https://example.com/", **kwargs)


def test_model_base_forbids_url_secrets_and_plaintext_remote():
    for url in [
        "https://user:pass@example.com/v1",
        "https://example.com/v1?key=synthetic",
        "http://example.com/v1",
        "https://example.com/v1#token",
    ]:
        with pytest.raises(ValueError):
            security.normalize_model_base(url)
    assert security.normalize_model_base("http://localhost:11434/v1/") == "http://localhost:11434/v1"


def test_nested_config_and_logs_redact_secrets():
    result = security.redact_secrets(
        {
            "providers": {
                "openai": {
                    "api_key": "SYNTHETIC1",
                    "api_key_env": "KEY",
                    "nested": [{"password": "SYNTHETIC2"}],
                }
            }
        }
    )
    assert "SYNTHETIC" not in str(result)
    message = "GET https://user:pass@example.com/?api_key=SYNTHETIC1&token=SYNTHETIC2 Authorization Bearer SYNTHETIC3"
    record = logging.LogRecord("test", logging.ERROR, "", 1, message, (), None)
    assert "SYNTHETIC" not in utils.RedactingFormatter("%(message)s").format(record)
    assert "user:pass" not in security.redact_text(message)


def test_llm_cannot_exfiltrate_server_environment_secret(monkeypatch):
    reads, calls = [], []
    monkeypatch.setattr(
        summarizer,
        "os",
        SimpleNamespace(
            environ=SimpleNamespace(
                get=lambda key, default="": reads.append(key) or "SYNTHETIC_SERVER_SECRET"
            )
        ),
    )
    monkeypatch.setattr(
        summarizer,
        "requests",
        SimpleNamespace(
            post=lambda *a, **k: calls.append(k),
            RequestException=requests.RequestException,
            Timeout=requests.Timeout,
        ),
    )
    supplied = {
        "base_url": "https://attacker.example/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    }
    with pytest.raises(summarizer.LlmUnavailable):
        summarizer.summarize("Diabetes research.", engine="openai", llm=supplied)
    assert not reads and not calls


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_llm_health_checks_http_status_and_auth(monkeypatch, status):
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")

    def get(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse({"error": "SYNTHETIC_MUST_NOT_LEAK"}, status=status)

    monkeypatch.setattr(
        summarizer,
        "requests",
        SimpleNamespace(get=get, RequestException=requests.RequestException, Timeout=requests.Timeout),
    )
    notice = summarizer.llm_unavailable_message(engine="openai")
    assert f"HTTP {status}" in notice and "MUST_NOT_LEAK" not in notice
    assert calls[0]["headers"] == {"Authorization": "Bearer synthetic-key"}


def test_llm_health_verifies_selected_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(
        summarizer,
        "requests",
        SimpleNamespace(
            get=lambda *a, **k: FakeResponse({"data": [{"id": "other"}]}),
            RequestException=requests.RequestException,
            Timeout=requests.Timeout,
        ),
    )
    assert "модель не найдена" in summarizer.llm_unavailable_message(engine="openai")
    monkeypatch.setattr(
        summarizer.requests, "get", lambda *a, **k: FakeResponse({"data": [{"id": "gpt-4o-mini"}]})
    )
    assert summarizer.llm_unavailable_message(engine="openai") is None


def test_text_extractor_strips_navigation_and_refuses_pdf(monkeypatch):
    response = FakeResponse(
        content=b"<body><nav>Menu</nav><article>Real abstract.</article><footer>Footer</footer></body>",
        headers={"Content-Type": "text/html"},
    )
    monkeypatch.setattr(utils, "requests", SimpleNamespace(get=lambda *a, **k: response))
    assert utils.extract_text_from_url("https://example.com/") == "Real abstract."
    response.headers["Content-Type"] = "application/pdf"
    assert utils.extract_text_from_url("https://example.com/") == ""


def test_slow_drip_response_cannot_postpone_deadline_until_full_chunk(monkeypatch):
    clock, reads = [0.0], []
    response = FakeResponse()

    def read1(size, **kwargs):
        reads.append(size)
        clock[0] += 1
        return b"x"

    response.raw.read1 = read1
    client, _ = fake_client(monkeypatch, [response])
    monkeypatch.setattr(network.time, "monotonic", lambda: clock[0])
    with pytest.raises(requests.Timeout):
        client.get("https://example.com/", timeout=2)
    assert len(reads) == 2 and response.closed


def test_public_redirect_count_is_bounded(monkeypatch):
    responses = [FakeResponse(status=302, headers={"Location": "/again"}) for _ in range(6)]
    client, calls = fake_client(monkeypatch, responses)
    with pytest.raises(requests.TooManyRedirects):
        client.get("https://example.com/")
    assert len(calls) == network.MAX_REDIRECTS + 1
