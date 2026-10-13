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
def _no_network(monkeypatch):
    """Fail loudly if a test accidentally reaches for the network."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=False)
    monkeypatch.setattr(socket, "create_connection", _blocked, raising=False)
