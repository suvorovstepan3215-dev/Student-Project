"""Bounded HTTP clients; external sources cannot reach the local/private network.

Every DNS answer is checked immediately before connecting. The connection uses
that numeric address while retaining the original hostname for TLS/SNI. This
also prevents DNS rebinding between URL validation and the actual connection.
Redirects are handled explicitly; environment proxies and .netrc are not used.
"""

import os
import socket
import ssl
import threading
import time
from urllib.parse import urljoin

import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util.connection import create_connection

from security import UnsafeURL, is_public_address, normalize_model_base, normalize_url, same_origin

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5


class ResponseTooLarge(_requests.RequestException):
    pass


class _PublicConnection:
    def _new_conn(self):
        connect_timeout = self.timeout if isinstance(self.timeout, (int, float)) else 5.0
        deadline = time.monotonic() + connect_timeout
        try:
            answers = socket.getaddrinfo(self._dns_host, self.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise UnsafeURL("DNS источника указывает на непубличный IP-адрес.")
        last_error = None
        for address in addresses[:4]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConnectTimeoutError(self, "Превышено время установления соединения.")
            try:
                return create_connection(
                    (address, self.port),
                    remaining,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            except socket.timeout as exc:
                last_error = ConnectTimeoutError(self, "Источник не ответил вовремя.")
                last_error.__cause__ = exc
            except OSError as exc:
                last_error = NewConnectionError(self, "Не удалось подключиться к источнику.")
                last_error.__cause__ = exc
        raise last_error


class _PublicHTTPConnection(_PublicConnection, HTTPConnection):
    pass


class _PublicHTTPSConnection(_PublicConnection, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _PublicHTTPConnection


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # PoolManager's default mapping is shared globally: never mutate it.
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


class _NoRedirectSession(_requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # requests otherwise buffers redirect response bodies EVEN when
        # allow_redirects=False, bypassing our decompressed-size limit.
        return iter(())


class _HTTPClient:
    # Match the requests attributes used by the adapters and their test fixtures.
    Response = _requests.Response
    RequestException = _requests.RequestException
    ConnectionError = _requests.ConnectionError
    Timeout = _requests.Timeout
    HTTPError = _requests.HTTPError

    def __init__(self, *, model=False):
        self.model = model
        self._local = threading.local()

    def _session(self, allow_private):
        key = "private" if allow_private else "public"
        sessions = getattr(self._local, "sessions", None)
        if sessions is None:
            sessions = self._local.sessions = {}
        if key not in sessions:
            session = _NoRedirectSession()
            session.trust_env = False
            # Keep TLS verification enabled. Use the host OS trust store when
            # available (including administrator-installed enterprise CAs).
            session.verify = (
                os.environ.get("TRACKER_CA_BUNDLE") or ssl.get_default_verify_paths().cafile or True
            )
            if not allow_private:
                session.mount("http://", _PublicAdapter())
                session.mount("https://", _PublicAdapter())
            sessions[key] = session
        return sessions[key]

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def _request(
        self,
        method,
        url,
        *,
        allowed_base_url=None,
        allow_private=False,
        max_bytes=MAX_RESPONSE_BYTES,
        timeout=20,
        **kwargs,
    ):
        if any(key in kwargs for key in ("verify", "proxies", "auth", "cert", "stream", "allow_redirects")):
            raise UnsafeURL("Изменение сетевой политики запроса запрещено.")
        if not self.model and allow_private:
            raise UnsafeURL("Источники не могут обращаться к частной сети.")
        if self.model:
            if not allowed_base_url:
                raise UnsafeURL("LLM endpoint должен быть задан администратором сервера.")
            base = normalize_model_base(allowed_base_url)
            url = normalize_url(url, allow_private=allow_private)
            if not same_origin(base, url) or url not in {base + "/models", base + "/chat/completions"}:
                raise UnsafeURL("Запрос выходит за пределы настроенного LLM API.")
        else:
            url = normalize_url(url)
        max_bytes = min(max(1, int(max_bytes)), MAX_RESPONSE_BYTES)
        seconds = max(0.1, min(float(timeout), 120))
        deadline = time.monotonic() + seconds
        session = self._session(allow_private)
        session.cookies.clear()
        headers = dict(kwargs.pop("headers", {}))
        sensitive = any(key.lower() in {"authorization", "cookie"} for key in headers)
        sensitive = sensitive or any(
            str(key).lower() in {"api_key", "key", "access_token", "token", "password", "secret"}
            for key in (kwargs.get("params") or {})
        )
        for redirect_count in range(MAX_REDIRECTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _requests.Timeout("Превышено время запроса к источнику.")
            response = session.request(
                method,
                url,
                headers=headers,
                timeout=(min(5, remaining), remaining),
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    if self.model:
                        raise UnsafeURL("Перенаправления LLM API запрещены во избежание утечки ключа.")
                    if redirect_count == MAX_REDIRECTS:
                        raise _requests.TooManyRedirects("Слишком много перенаправлений.")
                    target = normalize_url(urljoin(url, response.headers.get("Location", "")))
                    if url.startswith("https:") and target.startswith("http:"):
                        raise UnsafeURL("Переход с HTTPS на незащищённый HTTP запрещён.")
                    if not same_origin(url, target) and sensitive:
                        raise UnsafeURL("Перенаправление авторизованного запроса на другой узел запрещено.")
                    if not same_origin(url, target):
                        headers = {
                            k: v for k, v in headers.items() if k.lower() not in {"authorization", "cookie"}
                        }
                    session.cookies.clear()
                    url = target
                    kwargs.pop("params", None)
                    continue
                if self.model and response.status_code >= 400:
                    # Don't consume a remote error body that could echo a key.
                    response.raise_for_status()
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise ResponseTooLarge("Ответ источника превышает допустимый размер.")
                chunks, size = [], 0
                for chunk in _read_body(response, deadline):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ResponseTooLarge("Распакованный ответ источника слишком велик.")
                    if time.monotonic() > deadline:
                        raise _requests.Timeout("Превышено время чтения ответа.")
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                response._content_consumed = True
                return response
            finally:
                response.close()
        raise _requests.TooManyRedirects("Слишком много перенаправлений.")


def _read_body(response, deadline):
    # read1 returns available data instead of waiting for a full 64 KiB block.
    # With iter_content/read a slow-drip chunked response could postpone our
    # deadline check indefinitely, despite a socket idle timeout.
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _requests.Timeout("Превышено время чтения ответа.")
        connection = getattr(response.raw, "connection", None)
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        try:
            chunk = response.raw.read1(64 * 1024, decode_content=True)
        except Urllib3HTTPError as exc:
            raise _requests.RequestException("Ошибка чтения ответа источника.") from exc
        if not chunk:
            break
        yield chunk


public_requests = _HTTPClient()
# Only summarizer.py uses this client, after matching the server-owned preset.
model_requests = _HTTPClient(model=True)
