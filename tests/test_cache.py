"""Cache behaviour: key normalization, TTL handling, pruning.

Covers three regressions:
  P1-4 alternate symbol spellings created duplicate cache entries
  P1-5 pruning evicted still-valid long-TTL entries
  plus the original "expired entry counts as warm" bug.
"""
from __future__ import annotations

import time

import pandas as pd

from services import data


def _seed(key: str, ttl: float, age: float, value=None):
    """Insert a cache entry that looks `age` seconds old."""
    with data._cache_lock:
        data._cache[key] = (time.time() - age, ttl, value if value is not None else {"v": 1})


def test_cache_entry_stores_ttl():
    """Entries carry their own TTL as the middle tuple element."""
    _seed("x:y", 60.0, 0.0)
    with data._cache_lock:
        ts, ttl, val = data._cache["x:y"]
    assert ttl == 60.0
    assert val == {"v": 1}


def test_cache_fresh_respects_per_entry_ttl():
    """A 3600s entry aged 2000s is still fresh; a 60s entry aged 100s is not."""
    _seed("stock_info:a", 3600.0, 2000.0)
    _seed("hist:b", 60.0, 100.0)
    assert data.cache_fresh("stock_info:") is True
    assert data.cache_fresh("hist:") is False


def test_prune_keeps_valid_long_ttl_entry():
    """Regression P1-5: prune used a global 1800s threshold and killed this."""
    _seed("stock_info:a", 3600.0, 2000.0)
    data._prune_cache()
    with data._cache_lock:
        assert "stock_info:a" in data._cache


def test_prune_drops_expired_entries():
    _seed("hist:old", 60.0, 120.0)
    data._prune_cache()
    with data._cache_lock:
        assert "hist:old" not in data._cache


def test_prune_enforces_max_entries():
    """Hard cap must hold even when nothing is individually expired."""
    with data._cache_lock:
        data._cache.clear()
        for i in range(data._CACHE_MAX_ENTRIES + 50):
            data._cache[f"k:{i}"] = (time.time(), 9999.0, i)
    data._prune_cache()
    with data._cache_lock:
        assert len(data._cache) <= data._CACHE_MAX_ENTRIES


def _cache_key(prefix: str, args: tuple, kwargs: dict | None = None) -> str:
    """Mirror the key format built by the `_cached` decorator."""
    return prefix + ":" + repr((args, sorted((kwargs or {}).items())))


def test_intraday_cache_key_is_normalized(monkeypatch):
    """Regression P1-4: '159516'+'SZ' and '159516.SZ'+'' must normalize identically."""
    calls = []

    def fake_fetch(code, market, scale, datalen):
        calls.append((code, market, scale, datalen))
        return [{"time": "2026-10-12 10:00:00", "close": 1.0}]

    monkeypatch.setattr(data, "_intraday_normalized", fake_fetch)

    data.get_intraday("159516", "SZ", scale=1, datalen=240)
    data.get_intraday("159516.SZ", "", scale=1, datalen=240)

    # The decorated fetch is stubbed out here, so both calls reach it; what
    # matters is that they receive byte-identical normalized arguments.
    assert calls[0] == calls[1] == ("159516", "SZ", 1, 240)


def test_intraday_second_spelling_hits_cache():
    """A pre-warmed normalized entry must serve an alternate spelling.

    Note: the real (decorated) function is called here on purpose. Patching
    `_intraday_normalized` would replace the cache wrapper itself and prove
    nothing about caching.
    """
    sentinel = [{"time": "2026-10-12 10:00:00", "close": 9.99}]
    _seed(_cache_key("intraday", ("159516", "SZ", 1, 240)), 60.0, 0.0, sentinel)

    # Different spelling, same normalized key -> cache hit, no network.
    out = data.get_intraday("159516.SZ", "", scale=1, datalen=240)
    assert out == sentinel


def test_search_key_is_normalized(monkeypatch):
    """Whitespace/case variants must normalize to one query string."""
    calls = []

    def fake_fetch(q):
        calls.append(q)
        return []

    monkeypatch.setattr(data, "_search_sina_normalized", fake_fetch)

    data._search_sina("茅台 ")
    data._search_sina("茅台")
    assert calls == ["茅台", "茅台"], "both spellings must normalize to '茅台'"


def test_search_second_spelling_hits_cache():
    """A pre-warmed normalized entry must serve a padded query."""
    sentinel = [{"code": "600519", "market": "SH"}]
    _seed(_cache_key("search", ("茅台",)), 300.0, 0.0, sentinel)

    # Real decorated function: cache hit means no upstream call happens.
    assert data._search_sina("  茅台  ") == sentinel


def test_search_does_not_fetch_cold_tables(monkeypatch):
    """Regression P1-3: a search must never trigger a full-market download."""
    attempts = {"etf": 0, "stock": 0}

    def fake_etf():
        attempts["etf"] += 1
        return pd.DataFrame()

    def fake_stock():
        attempts["stock"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(data, "_etf_spot_df", fake_etf)
    monkeypatch.setattr(data, "_stock_spot_df", fake_stock)

    # Nothing warm at all -> neither table may be fetched.
    data._search_spot_tables("茅台", 8)
    assert attempts == {"etf": 0, "stock": 0}

    # Only the ETF table warm -> only it may be fetched.
    _seed("etf_spot:((), [])", 600.0, 0.0, pd.DataFrame({"代码": ["159516"], "名称": ["半导体设备ETF"]}))
    data._search_spot_tables("半导体", 8)
    assert attempts["etf"] == 1
    assert attempts["stock"] == 0, "cold stock table must not be downloaded"
