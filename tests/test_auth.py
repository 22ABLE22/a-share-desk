"""Token authentication for non-localhost exposure.

The app originally had no auth at all: acceptable while bound to 127.0.0.1, but
any wider exposure would let anyone read the real portfolio (cost basis,
holdings) and mutate it.

Behaviour that must hold:
  * No token configured -> completely open (local use is unchanged).
  * Token configured    -> /api/* requires it; static frontend stays open.
  * Wrong/missing token -> 401, never a 500 and never a silent pass.
  * Comparison is constant-time.

The middleware is exercised by calling it directly with a synthetic ASGI
``receive``/``send`` pair. This avoids Starlette's TestClient, which spins up an
asyncio event loop (and therefore real sockets) — the suite is deliberately
offline, and a socket-based client would also make the no-network guard
meaningless for exactly the code we most want to verify.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys

import pytest
from starlette.requests import Request

from auth import token_matches

TOKEN = "s3cret-token"
ENV = "ASHARE_DESK_TOKEN"


# --------------------------------------------------------------------------
# Constant-time comparison
# --------------------------------------------------------------------------
def test_token_matches_exact():
    assert token_matches("abc123", "abc123") is True


def test_token_matches_rejects_wrong():
    assert token_matches("abc123", "abc124") is False


def test_token_matches_rejects_empty():
    assert token_matches("", "abc123") is False
    assert token_matches(None, "abc123") is False


def test_token_matches_is_case_sensitive():
    assert token_matches("ABC123", "abc123") is False


def test_token_matches_handles_unicode():
    assert token_matches("令牌", "令牌") is True
    assert token_matches("令牌", "令排") is False


# --------------------------------------------------------------------------
# Helpers: drive the middleware without sockets
# --------------------------------------------------------------------------
def _request(path: str, headers: dict[str, str] | None = None, method: str = "GET") -> Request:
    raw = [(b"host", b"testserver")]
    for k, v in (headers or {}).items():
        raw.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": raw,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    return Request(scope)


def _run(coro):
    """Run a coroutine on an explicit Selector event loop.

    On Windows the default is the Proactor loop, whose self-pipe needs a socket
    pair; building one per test is both slow and fragile here. The middleware
    performs no real I/O, so a Selector loop is sufficient and self-contained.
    """
    import asyncio
    import sys

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _call_middleware(app_module, request: Request):
    """Run the auth middleware; return (status, body) or None if it passed through."""
    passed: list[bool] = []

    async def call_next(_req):
        passed.append(True)
        from starlette.responses import Response

        return Response("passthrough")

    response = _run(app_module._require_token_for_api(request, call_next))
    if passed:
        return None  # request reached the app
    body = json.loads(bytes(response.body).decode("utf-8"))
    return response.status_code, body


@pytest.fixture
def app_module(monkeypatch):
    """Import main fresh so the middleware sees the current env var."""
    monkeypatch.delenv(ENV, raising=False)
    sys.modules.pop("main", None)
    import main

    importlib.reload(main)
    yield main
    monkeypatch.delenv(ENV, raising=False)
    sys.modules.pop("main", None)


@pytest.fixture
def secured(app_module, monkeypatch):
    monkeypatch.setenv(ENV, TOKEN)
    return app_module


# --------------------------------------------------------------------------
# Auth disabled (default): behaviour must be unchanged
# --------------------------------------------------------------------------
def test_auth_disabled_by_default(app_module):
    assert app_module.auth.auth_enabled() is False


def test_health_reports_auth_flag(app_module):
    assert app_module.health()["auth_enabled"] is False


def test_health_reports_flag_when_enabled(secured):
    assert secured.health()["auth_enabled"] is True


def test_no_auth_means_no_token_needed(app_module):
    """Every API path must pass through untouched when auth is off."""
    for path in ("/api/health", "/api/market/clock", "/api/portfolio"):
        assert _call_middleware(app_module, _request(path)) is None, path


# --------------------------------------------------------------------------
# Auth enabled
# --------------------------------------------------------------------------
def test_missing_token_rejected(secured):
    result = _call_middleware(secured, _request("/api/health"))
    assert result is not None
    status, body = result
    assert status == 401
    assert "detail" in body


def test_bearer_token_accepted(secured):
    req = _request("/api/health", {"Authorization": f"Bearer {TOKEN}"})
    assert _call_middleware(secured, req) is None


def test_api_token_header_accepted(secured):
    req = _request("/api/health", {"X-API-Token": TOKEN})
    assert _call_middleware(secured, req) is None


def test_wrong_token_rejected(secured):
    result = _call_middleware(secured, _request("/api/health", {"X-API-Token": "wrong"}))
    assert result is not None and result[0] == 401


def test_empty_token_rejected(secured):
    result = _call_middleware(secured, _request("/api/health", {"X-API-Token": ""}))
    assert result is not None and result[0] == 401


def test_bearer_prefix_is_case_insensitive(secured):
    req = _request("/api/health", {"Authorization": f"bearer {TOKEN}"})
    assert _call_middleware(secured, req) is None


def test_401_advertises_bearer(secured):
    from starlette.responses import Response  # noqa: F401

    async def call_next(_req):
        raise AssertionError("should not reach the app")

    response = asyncio.run(secured._require_token_for_api(_request("/api/health"), call_next))
    assert response.headers.get("www-authenticate") == "Bearer"


def test_static_frontend_stays_public(secured):
    """The page must load so it can carry the token."""
    assert _call_middleware(secured, _request("/")) is None


def test_static_assets_stay_public(secured):
    assert _call_middleware(secured, _request("/static/js/app.js")) is None


def test_non_api_paths_not_guarded(secured):
    for path in ("/", "/index.html", "/static/css/app.css"):
        assert _call_middleware(secured, _request(path)) is None, path


@pytest.mark.parametrize("path", [
    "/api/health",
    "/api/quote",
    "/api/history",
    "/api/portfolio",
    "/api/advice",
    "/api/backtest",
    "/api/search",
    "/api/reference",
    "/api/monitor",
    "/api/alerts",
    "/api/fundamentals",
    "/api/intraday",
    "/api/signals",
    "/api/signals/pro",
    "/api/market/clock",
])
def test_every_api_path_guarded(secured, path):
    """Guarding is path-based, so any /api/* route is covered by construction."""
    result = _call_middleware(secured, _request(path))
    assert result is not None, f"{path} was not guarded"
    assert result[0] == 401


def test_write_methods_guarded(secured):
    """Mutations are the most dangerous surface."""
    for method in ("POST", "PATCH", "DELETE"):
        result = _call_middleware(
            secured, _request("/api/portfolio/watch", method=method)
        )
        assert result is not None and result[0] == 401, method


def test_guarded_routes_exist_in_app(secured):
    """Sanity: the app really exposes many /api routes (guard is meaningful)."""
    api_routes = [
        r.path for r in secured.app.routes if getattr(r, "path", "").startswith("/api/")
    ]
    assert len(api_routes) >= 15, f"only saw {len(api_routes)} api routes"


# --------------------------------------------------------------------------
# Frontend wiring
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app_js() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js"
    ).read_text(encoding="utf-8")


def test_frontend_sends_token_header(app_js):
    assert "X-API-Token" in app_js
    assert "function apiFetch(" in app_js


def test_frontend_reads_token_from_fragment(app_js):
    assert "location.hash" in app_js, "token should come from the URL fragment"
    assert "replaceState" in app_js, "fragment should be stripped from the address bar"


def test_frontend_persists_token(app_js):
    assert "localStorage.setItem(TOKEN_KEY" in app_js


def test_all_api_calls_use_the_wrapper(app_js):
    """A bare fetch() would silently omit the token and 401."""
    import re

    bare = []
    for i, ln in enumerate(app_js.splitlines()):
        stripped = ln.strip()
        # Skip comments and the wrapper's own internal call.
        if stripped.startswith(("*", "//", "/*")):
            continue
        if "await fetch(url" in ln:
            continue
        if re.search(r"(?<![\w.])fetch\(", ln):
            bare.append((i + 1, stripped))
    assert not bare, f"unguarded fetch() calls: {bare}"


def test_token_read_before_first_request(app_js):
    boot = app_js[app_js.index("async function boot()"):]
    assert boot.index("readTokenFromUrl") < boot.index("loadPortfolio")
