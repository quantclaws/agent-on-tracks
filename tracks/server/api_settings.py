"""System settings HTTP face (settings window, network panel).

Reads/writes the service-plane ``settings`` store (interfaces §1c) through
``ServiceDB``. The network panel owns the single key ``network.proxy`` whose
JSON value is ``{type, host, port, github_fallback}``; the GitHub effect
layer reads the same row read-only as its last-resort transport fallback.

Request seam (mirrors api_query/api_command):
    request.app.state.home            -> service home (service.db)
    request.cookies / request.headers -> session + X-Trac-CSRF on the POST
    await request.json()              -> structured JSON body

Response envelope: ``QueryResponse(status_code, payload)`` — starlette-free
so the package stays importable at the unit layer; the app factory maps it
onto a JSONResponse. The POST rides the pre-existing auth boundary: a missing
session is 401 and a missing/mismatching ``X-Trac-CSRF`` is the same audited
403 the command faces return.
"""

from __future__ import annotations

from typing import Any

from tracks.server import api_command, auth
from tracks.server.api_query import QueryResponse
from tracks.supervisor.db import ServiceDB

_PROXY_KEY = "network.proxy"
_PROXY_TYPES = frozenset({"http", "https", "socks5"})


class _InvalidProxy(ValueError):
    """A proxy payload refused by the network-panel validation rules."""


def _default_proxy() -> dict:
    return {"type": "http", "host": "", "port": 0, "github_fallback": False}


def _home(request: Any) -> Any:
    return request.app.state.home


def _validated_proxy(raw: object) -> dict:
    """Validate + normalize one proxy object; raises ``_InvalidProxy``."""
    if not isinstance(raw, dict):
        raise _InvalidProxy("proxy must be an object")
    proxy_type = raw.get("type")
    if proxy_type not in _PROXY_TYPES:
        raise _InvalidProxy("type must be one of http, https, socks5")
    host = raw.get("host")
    if not isinstance(host, str):
        raise _InvalidProxy("host must be a string")
    if len(host) > 255:
        raise _InvalidProxy("host must be at most 255 characters")
    if "://" in host:
        raise _InvalidProxy("host must not contain a URL scheme")
    if any(char.isspace() for char in host):
        raise _InvalidProxy("host must not contain whitespace")
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        raise _InvalidProxy("port must be an integer")
    if not 0 <= port <= 65535:
        raise _InvalidProxy("port must be within 0..65535")
    if host and port == 0:
        raise _InvalidProxy("port must be within 1..65535 when host is set")
    fallback = raw.get("github_fallback")
    if not isinstance(fallback, bool):
        raise _InvalidProxy("github_fallback must be a boolean")
    return {
        "type": proxy_type,
        "host": host,
        "port": port,
        "github_fallback": fallback if host else False,
    }


def _stored_proxy(home: Any) -> dict:
    """The persisted proxy, normalized; defaults on absence/corrupt data."""
    stored = ServiceDB(home).get_setting(_PROXY_KEY)
    if stored is None:
        return _default_proxy()
    try:
        return _validated_proxy(stored)
    except _InvalidProxy:
        return _default_proxy()


async def get_settings(request: Any) -> QueryResponse:
    """GET /api/settings: the network panel's current configuration."""
    return QueryResponse(200, {"network": {"proxy": _stored_proxy(_home(request))}})


async def update_network(request: Any) -> QueryResponse:
    """POST /api/settings/network: validate, persist and echo the proxy."""
    session = api_command._authenticated(request)
    if session is None:
        return api_command._error(
            401, "unauthenticated", "an authenticated session is required"
        )
    if not auth.check_csrf(session, request.headers.get("X-Trac-CSRF")):
        return api_command._csrf_rejection(
            request, session, "settings.network", None
        )
    try:
        body = await request.json()
    except (AttributeError, TypeError, ValueError):
        body = {}
    try:
        proxy = _validated_proxy(body.get("proxy") if isinstance(body, dict) else None)
    except _InvalidProxy as exc:
        return QueryResponse(
            422, {"error": "validation_failed", "detail": str(exc)}
        )
    ServiceDB(_home(request)).set_setting(_PROXY_KEY, proxy)
    return QueryResponse(200, {"ok": True, "proxy": proxy})
