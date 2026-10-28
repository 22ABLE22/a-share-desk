"""Optional token authentication for exposing the desk beyond localhost.

Why this exists
---------------
The app was built as a single-user local tool with **no authentication at all**.
That is fine while it only listens on 127.0.0.1, but the moment it becomes
reachable from anywhere else (LAN via 0.0.0.0, Cloudflare Tunnel, a server),
anyone who knows the address can read the real portfolio (cost basis, holdings)
and mutate it via the write endpoints.

Design
------
* **Off by default.** With no token configured the app behaves exactly as
  before, so local use needs no extra step and cannot be locked out by accident.
* Enabling it is a single environment variable: ``ASHARE_DESK_TOKEN``.
* Accepted via ``Authorization: Bearer <token>`` or ``X-API-Token: <token>``.
* The browser UI gets the token from the URL fragment once
  (``/#token=...``), stores it in ``localStorage`` and sends it as a header —
  fragments never reach the server or appear in access logs.
* Comparison is constant-time to avoid leaking the token through timing.

Scope: only ``/api/*`` is protected. The static frontend must stay public,
otherwise the browser could not even load the page that prompts for the token.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger("a-share-desk.auth")

ENV_VAR = "ASHARE_DESK_TOKEN"
# Header the browser UI uses; Bearer is also accepted for curl/scripts.
HEADER_TOKEN = "x-api-token"


def configured_token() -> str | None:
    """The active token, or None when auth is disabled."""
    raw = os.environ.get(ENV_VAR, "").strip()
    return raw or None


def auth_enabled() -> bool:
    return configured_token() is not None


def _extract(request: Request) -> str | None:
    header = request.headers.get(HEADER_TOKEN)
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def token_matches(supplied: str | None, expected: str) -> bool:
    """Constant-time comparison; never raises on odd input."""
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def require_token(request: Request) -> None:
    """FastAPI dependency guarding the API surface."""
    expected = configured_token()
    if expected is None:
        return  # auth disabled: behave like the original local-only app
    if token_matches(_extract(request), expected):
        return
    logger.warning("rejected unauthenticated request: %s %s", request.method, request.url.path)
    raise HTTPException(
        status_code=401,
        detail="需要访问令牌（Authorization: Bearer <token> 或 X-API-Token）",
        headers={"WWW-Authenticate": "Bearer"},
    )
