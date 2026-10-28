"""Shared pytest fixtures.

Tests must never touch the network: the data layer talks to three unofficial
upstreams, and the whole point of this suite is to catch regressions offline.
Anything that would fetch is either a pure function or monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# backend/ is the import root used by uvicorn ("main:app" with cwd=backend).
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def _clear_data_cache():
    """Keep the module-level TTL cache from leaking between tests."""
    from services import data

    with data._cache_lock:
        data._cache.clear()
    yield
    with data._cache_lock:
        data._cache.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Fail loudly if a test accidentally reaches for the network.

    Only outbound connects to a *remote* address are blocked. Local loopback and
    ``socket.socketpair`` are allowed: on Windows the asyncio event loop builds
    its internal self-pipe from a socket pair, which shows up as a loopback
    ``connect`` and would otherwise make every ASGI test fail for a reason that
    has nothing to do with network access.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    import socket

    real_connect = socket.socket.connect

    def _guarded_connect(self, address, *args, **kwargs):
        host = None
        if isinstance(address, tuple) and address:
            host = address[0]
        elif isinstance(address, (str, bytes)):
            host = address
        if isinstance(host, bytes):
            host = host.decode("latin-1", "replace")
        # Loopback / unix-style local addresses are infrastructure, not egress.
        if host is None or host in ("127.0.0.1", "::1", "localhost", "") or str(host).startswith(
            ("127.", "\\\\.\\pipe", "/")
        ):
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError(f"test attempted a network connection to {host!r}")

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect, raising=False)

    def _blocked_create_connection(*args, **kwargs):
        raise AssertionError("test attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection, raising=False)
