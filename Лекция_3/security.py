"""URL policies and secret redaction. No network requests or configuration writes."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


class UnsafeURL(ValueError):
    """An outbound URL violates the server's network policy."""


_SECRET_KEY = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization", re.I)
_QUERY_SECRET = re.compile(r"([?&](?:api[_-]?key|key|access_token|token|password|secret)=)[^&\s\"'<>]+", re.I)
_BEARER = re.compile(r"(\bBearer\s+)[^\s\"'<>]+", re.I)
_USERINFO = re.compile(r"(https?://)[^/\s@]+@", re.I)


def redact_text(value: object) -> str:
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", str(value))
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _USERINFO.sub(r"\1[REDACTED]@", text)


def redact_secrets(value):
    """Recursively redact secrets, including nested provider/source settings."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    # Block transition mechanisms too: they can encapsulate a private IPv4 address.
    if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo):
        return False
    return ip.is_global and not (ip.is_multicast or ip.is_unspecified or ip.is_reserved)


def normalize_url(url: str, *, allow_private: bool = False) -> str:
    """Syntax/literal-IP validation; DNS is checked and pinned at connection time."""
    if not isinstance(url, str) or len(url) > 4096:
        raise UnsafeURL("Недопустимый URL.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url) or "\\" in url:
        raise UnsafeURL("Управляющие символы в URL запрещены.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("Некорректный адрес или порт.") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise UnsafeURL("Разрешены только абсолютные HTTP/HTTPS URL.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("Учётные данные внутри URL запрещены.")
    host = host.rstrip(".").lower()
    if not host or "%" in host:
        raise UnsafeURL("Некорректное имя узла.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURL("Некорректное имя узла.") from exc
    if not allow_private:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeURL("Локальные адреса для источников запрещены.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass  # Alternate numeric forms and DNS answers are checked at the socket.
        else:
            if not is_public_address(host):
                raise UnsafeURL("Непубличные IP-адреса запрещены.")
        if port not in (None, 80, 443):
            raise UnsafeURL("Для внешних источников разрешены только порты 80 и 443.")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))


def normalize_model_base(url: str) -> str:
    normalized = normalize_url(url, allow_private=True)
    parsed = urlsplit(normalized)
    if parsed.query or parsed.fragment or urlsplit(url).fragment:
        raise UnsafeURL("API URL не должен содержать параметры или фрагмент.")
    # Remote credentials must not travel over plaintext HTTP.
    if parsed.scheme == "http":
        host = parsed.hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise UnsafeURL("Для удалённого LLM API требуется HTTPS.")
    return normalized.rstrip("/")


def same_origin(first: str, second: str) -> bool:
    a, b = urlsplit(first), urlsplit(second)
    return (a.scheme, a.hostname, a.port or (443 if a.scheme == "https" else 80)) == (
        b.scheme,
        b.hostname,
        b.port or (443 if b.scheme == "https" else 80),
    )
