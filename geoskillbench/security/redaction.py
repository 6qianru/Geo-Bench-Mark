from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "passwd",
    "client_secret",
    "db_url",
    "database_url",
}
_SECRET_QUERY_KEYS = {"key", "api_key", "apikey", "token", "access_token", "password", "secret"}
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    host = hostname
    if parsed.port:
        host += f":{parsed.port}"
    query = urlencode(
        [(key, "[REDACTED]" if key.lower() in _SECRET_QUERY_KEYS else item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)]
    )
    return urlunsplit((parsed.scheme, host, parsed.path, query, parsed.fragment))


def redact_text(value: str) -> str:
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    return _redact_url(value)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a recursively redacted copy suitable for reports, events and logs."""
    if key and key.lower().replace("-", "_") in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
