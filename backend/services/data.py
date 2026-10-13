"""Market data via free public sources (akshare / East Money)."""
from __future__ import annotations

import datetime as dt
import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, float, Any]] = {}  # key -> (stored_at, ttl, value)
_CACHE_TTL = 60.0  # seconds
_CACHE_MAX_ENTRIES = 512
_cache_lock = threading.Lock()


def cache_fresh(prefix: str) -> bool:
    """True only if a *non-expired* entry with this prefix exists.

    The previous implementation checked key existence only, so an expired
    entry still counted as "warm" and triggered a full 20s snapshot refetch.
    Each entry is now aged against its own TTL rather than a global default.
    """
    now = time.time()
    with _cache_lock:
        for k, (ts, ttl, _v) in _cache.items():
            if k.startswith(prefix) and now - ts < ttl:
                return True
    return False


def _prune_cache() -> None:
    """Drop expired entries; hard-cap size so long runs don't leak memory.

    Expiry uses each entry's own TTL. It previously used `_CACHE_TTL * 30`
    (1800s) for everything, which evicted still-valid long-lived entries such as
    `stock_info` (3600s).
    """
    now = time.time()
    with _cache_lock:
        expired = [k for k, (ts, ttl, _) in _cache.items() if now - ts > ttl]
        for k in expired:
            _cache.pop(k, None)
        if len(_cache) > _CACHE_MAX_ENTRIES:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0]):
                _cache.pop(k, None)
                if len(_cache) <= _CACHE_MAX_ENTRIES:
                    break


def _is_empty(val) -> bool:
    """Empty frames/lists must never be cached: a transient upstream failure
    would otherwise pin the symbol to "no data" for the whole TTL."""
    if val is None:
        return True
    if isinstance(val, pd.DataFrame):
        return len(val) == 0
    if isinstance(val, (list, tuple, dict, str)):
        return len(val) == 0
    return False


def _cached(key: str, ttl: float = _CACHE_TTL, skip_empty: bool = True):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            ck = key + ":" + repr((args, sorted(kwargs.items())))
            now = time.time()
            with _cache_lock:
                hit = _cache.get(ck)
                if hit and now - hit[0] < hit[1]:
                    return hit[2]
            val = fn(*args, **kwargs)
            if not (skip_empty and _is_empty(val)):
                # Timestamp after the fetch: stamping before it made a slow
                # upstream call consume its own TTL.
                with _cache_lock:
                    _cache[ck] = (time.time(), ttl, val)
                if len(_cache) > _CACHE_MAX_ENTRIES:
                    _prune_cache()
            return val
        return wrapper
    return deco


class CircuitBreaker:
    """Skip a known-bad upstream during cooldown instead of paying its timeout.

    East Money rate-limits aggressively; without this every call waits for the
    connection reset before falling back to sina/tencent.
    """

    def __init__(self, name: str, fail_threshold: int = 3, cooldown: float = 90.0):
        self.name = name
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self._fails = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._fails >= self.fail_threshold and time.time() < self._open_until:
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._fails += 1
            if self._fails >= self.fail_threshold:
                self._open_until = time.time() + self.cooldown
                logger.warning(
                    "circuit open: %s skipped for %.0fs after %d failures",
                    self.name, self.cooldown, self._fails,
                )

    def status(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "fails": self._fails,
                "open": self._fails >= self.fail_threshold and time.time() < self._open_until,
                "retry_in_sec": max(0, round(self._open_until - time.time(), 1)),
            }


_em_breaker = CircuitBreaker("eastmoney", fail_threshold=2, cooldown=120.0)


def _full_code(code: str, market: str) -> str:
    code = code.strip().upper()
    if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"):
        return code
    market = (market or "").upper()
    if market in ("SH", "SS"):
        return f"{code}.SH"
    if market == "SZ":
        return f"{code}.SZ"
    if market == "BJ":
        return f"{code}.BJ"
    # heuristic (BJ: 43/83/87/92 prefixes; check before the generic 9=SH rule)
    if code.startswith(("43", "83", "87", "92")):
        return f"{code}.BJ"
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    return f"{code}.SZ"


def parse_symbol(symbol: str) -> tuple[str, str]:
    """Return (code, market) e.g. 159516.SZ -> (159516, SZ)."""
    symbol = symbol.strip().upper()
    if "." in symbol:
        code, market = symbol.rsplit(".", 1)
        return code, market
    code = symbol
    if code.startswith(("43", "83", "87", "92")):
        return code, "BJ"
    if code.startswith(("5", "6", "9")):
        return code, "SH"
    return code, "SZ"


def _is_etf(code: str, market: str) -> bool:
    if market == "SH" and code.startswith("5"):
        return True
    if market == "SZ" and code.startswith("1"):
        return True
    return False


@_cached("etf_spot", 600)
def _etf_spot_df() -> pd.DataFrame:
    if not _em_breaker.allow():
        return pd.DataFrame()
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        if df is None or len(df) == 0:
            _em_breaker.record_failure()
            return pd.DataFrame()
        _em_breaker.record_success()
        return df
    except Exception as e:
        _em_breaker.record_failure()
        logger.warning("EM etf spot unavailable: %s", e)
        return pd.DataFrame()


@_cached("stock_spot", 600)
def _stock_spot_df() -> pd.DataFrame:
    if not _em_breaker.allow():
        return pd.DataFrame()
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            _em_breaker.record_failure()
            return pd.DataFrame()
        _em_breaker.record_success()
        return df
    except Exception as e:
        _em_breaker.record_failure()
        logger.warning("EM stock spot unavailable: %s", e)
        return pd.DataFrame()


def _quote_em_spot(code: str, market: str, full: str) -> dict | None:
    if not _em_breaker.allow():
        return None
    try:
        if _is_etf(code, market):
            df = _etf_spot_df()
        else:
            df = _stock_spot_df()
        if df is None or len(df) == 0 or len(df.columns) == 0:
            return None
        col_code = "代码" if "代码" in df.columns else df.columns[0]
        row = df[df[col_code].astype(str).str.zfill(6) == code.zfill(6)]
        if row is None or len(row) == 0:
            return None
        r = row.iloc[0].to_dict()

        def pick(*names, default=None):
            for n in names:
                if n in r and r[n] is not None and str(r[n]) != "nan":
                    return r[n]
            return default

        price = float(pick("最新价", "当前价", default=0) or 0)
        prev = float(pick("昨收", "昨收价", default=0) or 0)
        chg = float(pick("涨跌额", default=0) or 0)
        chg_pct = float(pick("涨跌幅", default=0) or 0)
        if not chg and price and prev:
            chg = price - prev
        if not chg_pct and price and prev:
            chg_pct = (price - prev) / prev * 100
        if not price:
            return None
        return {
            "code": code,
            "market": market,
            "full": full,
            "name": str(pick("名称", default=full)),
            "price": price,
            "open": float(pick("今开", default=0) or 0),
            "high": float(pick("最高", default=0) or 0),
            "low": float(pick("最低", default=0) or 0),
            "prev_close": prev,
            "change": chg,
            "change_pct": chg_pct,
            "volume": float(pick("成交量", default=0) or 0),
            "amount": float(pick("成交额", default=0) or 0),
            "turnover": float(pick("换手率", default=0) or 0),
            "pe": _to_float(pick("市盈率-动态", "市盈率(动态)", "市盈率")),
            "pb": _to_float(pick("市净率")),
            "total_mv": _to_float(pick("总市值")),
            "circ_mv": _to_float(pick("流通市值")),
            "type": "etf" if _is_etf(code, market) else "stock",
            "source": "spot",
        }
    except Exception as e:
        logger.debug("em spot quote %s: %s", full, e)
        return None


def _prefer_name(a: str | None, b: str | None) -> str | None:
    """Pick the fresher display name.

    XD/XR/DR are exchange-day prefixes (除息/除权/除权除息). On those days some
    feeds (e.g. Tencent) show `XD十年国债ETF国泰` while others (Sina) drop the
    prefix. Prefer the version that carries the status prefix.
    """
    a = (a or "").strip() or None
    b = (b or "").strip() or None
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    a_status = a[:2].upper() in ("XD", "XR", "DR")
    b_status = b[:2].upper() in ("XD", "XR", "DR")
    if b_status and not a_status:
        return b
    if a_status and not b_status:
        return a
    # otherwise keep the longer/more complete live name
    return b if len(b) > len(a) else a


def get_quote(code: str, market: str = "") -> dict[str, Any]:
    """Latest quote for stock or ETF. Prefer fast sina, enrich with EM when warm."""
    if market:
        code, market = parse_symbol(_full_code(code, market))
    else:
        code, market = parse_symbol(code)
    full = _full_code(code, market)

    # Fast path: sina + tencent in parallel (tencent carries PE/PB/市值/换手).
    sina_box: dict[str, Any] = {"q": None}
    tencent_box: dict[str, Any] = {"q": None}

    def _pull_sina():
        sina_box["q"] = _quote_sina_hq(code, market, full)

    def _pull_tx():
        tencent_box["q"] = _quote_tencent(code, market, full)

    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(_pull_sina)
        ex.submit(_pull_tx)

    fast = sina_box["q"]
    if fast:
        tx = tencent_box["q"]
        if tx:
            for k in ("pe", "pb", "total_mv", "circ_mv", "turnover"):
                if tx.get(k) is not None and fast.get(k) is None:
                    fast[k] = tx[k]
            # 名称：保留含 XD/XR/DR 等除息除权前缀的实时名
            preferred = _prefer_name(fast.get("name"), tx.get("name"))
            if preferred:
                fast["name"] = preferred
            if tx.get("name") and fast.get("name") in (None, "", full):
                fast["name"] = tx["name"]
        # Optionally enrich from EM only when a *fresh* full snapshot exists.
        cache_key_prefix = "etf_spot:" if _is_etf(code, market) else "stock_spot:"
        if cache_fresh(cache_key_prefix) and _em_breaker.allow():
            extra = _quote_em_spot(code, market, full)
            if extra:
                for k in ("pe", "pb", "total_mv", "circ_mv", "turnover"):
                    if extra.get(k) is not None and fast.get(k) is None:
                        fast[k] = extra[k]
        return fast

    # sina failed — try tencent as primary, then EM
    if tencent_box["q"]:
        return tencent_box["q"]

    em = _quote_em_spot(code, market, full)
    if em:
        return em
    return _quote_fallback_hist(code, market, full)


# Bond / money ETFs sometimes missing from EM stock-style ETF spot list
# 仅当实时源完全没有名称时兜底，不覆盖含 XD/XR 的实时名
_NAME_HINTS = {
    "159516": "半导体设备ETF",
    "511260": "十年国债ETF国泰",
    "511010": "国债ETF",
    "511030": "公司债ETF",
    "511220": "城投债ETF",
    "511380": "可转债ETF",
    "511880": "银华日利",
    "511990": "华宝添益",
    "511660": "建信添益",
}


def _name_from_spot(code: str, market: str) -> str | None:
    try:
        if _is_etf(code, market):
            df = _etf_spot_df()
        else:
            df = _stock_spot_df()
        if df is None or len(df) == 0 or len(df.columns) == 0:
            return _NAME_HINTS.get(code)
        col_code = "代码" if "代码" in df.columns else df.columns[0]
        col_name = "名称" if "名称" in df.columns else df.columns[1]
        row = df[df[col_code].astype(str).str.zfill(6) == code.zfill(6)]
        if len(row):
            return str(row.iloc[0][col_name])
    except Exception:
        pass
    return _NAME_HINTS.get(code)


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        if s in ("", "-", "nan", "None"):
            return None
        return float(s)
    except Exception:
        return None


_SINA_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def _exch_prefix(market: str) -> str:
    return _SINA_PREFIX.get(market, "sz")


def _quote_sina_hq(code: str, market: str, full: str) -> dict | None:
    """Fast sina realtime quote; good for bond ETFs missing from EM spot."""
    try:
        import requests
        prefix = _exch_prefix(market)
        url = f"https://hq.sinajs.cn/list={prefix}{code}"
        headers = {
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0",
        }
        r = requests.get(url, headers=headers, timeout=6)
        r.raise_for_status()
        text = r.text
        # gbk encoded typically
        try:
            text = r.content.decode("gbk", errors="replace")
        except Exception:
            pass
        payload = text.split('="', 1)[-1].rsplit('"', 1)[0]
        parts = payload.split(",")
        if len(parts) < 32:
            return None
        name = parts[0].strip()
        # 本地 hints 只在实时名为空时使用，避免盖掉 XD 等当日状态名
        if (not name or len(name) < 3) and code in _NAME_HINTS:
            name = _NAME_HINTS[code]
        open_ = float(parts[1] or 0)
        prev = float(parts[2] or 0)
        price = float(parts[3] or 0)
        high = float(parts[4] or 0)
        low = float(parts[5] or 0)
        volume = float(parts[8] or 0)
        amount = float(parts[9] or 0)
        if not price:
            return None
        chg = price - prev if prev else 0
        chg_pct = (chg / prev * 100) if prev else 0
        return {
            "code": code,
            "market": market,
            "full": full,
            "name": name or full,
            "price": price,
            "open": open_,
            "high": high,
            "low": low,
            "prev_close": prev,
            "change": chg,
            "change_pct": chg_pct,
            "volume": volume,
            "amount": amount,
            "type": "etf" if _is_etf(code, market) else "stock",
            "source": "sina",
        }
    except Exception as e:
        logger.debug("sina hq %s: %s", full, e)
        return None


def _quote_tencent(code: str, market: str, full: str) -> dict | None:
    """Tencent realtime quote — supplies PE/PB/市值/换手率 when EM is limited.

    Market caps are reported in 亿元 here, so they are scaled to 元 to match
    the EM field convention consumed by _fmt_mv.
    """
    try:
        import requests
        prefix = _exch_prefix(market)
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
            timeout=6,
        )
        r.raise_for_status()
        text = r.content.decode("gbk", errors="replace")
        parts = text.split("~")
        if len(parts) < 47:
            return None
        price = _to_float(parts[3])
        if not price:
            return None
        prev = _to_float(parts[4]) or 0
        chg = price - prev if prev else 0
        chg_pct = (chg / prev * 100) if prev else 0
        mv_yi = _to_float(parts[45])
        circ_yi = _to_float(parts[44])
        name = parts[1].strip() or _NAME_HINTS.get(code) or full
        pb = _to_float(parts[46])
        return {
            "code": code,
            "market": market,
            "full": full,
            "name": name,
            "price": price,
            "open": _to_float(parts[5]) or 0,
            "high": _to_float(parts[33]) or 0,
            "low": _to_float(parts[34]) or 0,
            "prev_close": prev,
            "change": chg,
            "change_pct": chg_pct,
            "volume": (_to_float(parts[6]) or 0) * 100,   # 手 -> 股
            "amount": (_to_float(parts[37]) or 0) * 1e4,   # 万元 -> 元
            "turnover": _to_float(parts[38]),
            "pe": _to_float(parts[39]),
            "pb": pb if pb else None,
            "total_mv": mv_yi * 1e8 if mv_yi else None,
            "circ_mv": circ_yi * 1e8 if circ_yi else None,
            "type": "etf" if _is_etf(code, market) else "stock",
            "source": "tencent",
        }
    except Exception as e:
        logger.debug("tencent quote %s: %s", full, e)
        return None


def _quote_fallback_hist(code: str, market: str, full: str) -> dict:
    try:
        hist = get_history(code, market, days=120)
        if hist is None or len(hist) == 0:
            return {"code": code, "market": market, "full": full, "name": full, "price": 0, "source": "empty"}
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last
        price = float(last["close"])
        prev_close = float(prev["close"])
        chg = price - prev_close
        chg_pct = (chg / prev_close * 100) if prev_close else 0
        name = _name_from_spot(code, market) or full
        return {
            "code": code,
            "market": market,
            "full": full,
            "name": name,
            "price": price,
            "open": float(last.get("open", 0) or 0),
            "high": float(last.get("high", 0) or 0),
            "low": float(last.get("low", 0) or 0),
            "prev_close": prev_close,
            "change": chg,
            "change_pct": chg_pct,
            "volume": float(last.get("volume", 0) or 0),
            "amount": float(last.get("amount", 0) or 0),
            "type": "etf" if _is_etf(code, market) else "stock",
            "source": "hist",
        }
    except Exception as e:
        logger.error("hist fallback failed %s: %s", full, e)
        return {"code": code, "market": market, "full": full, "name": full, "price": 0, "error": str(e)}


# A-share lot size: both SH and SZ trade in 手 (lots) of 100 shares. akshare's
# East Money history endpoints report 成交量 in 手, while the sina endpoints
# already return 股. Without this conversion the same symbol's `volume` silently
# changes unit by 100x depending on which source won the fallback chain, which
# corrupts the volume factor and any volume-based statistic.
_LOT_SIZE = 100


def _standardize_ohlcv(
    df: pd.DataFrame,
    days: int | None = None,
    volume_in_lots: bool = False,
) -> pd.DataFrame:
    """Normalize an OHLCV frame to a common schema.

    `volume_in_lots=True` converts 成交量 from 手 to 股 so that frames from
    every source agree on unit. Callers must state the source's convention.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    rename = {
        "日期": "date",
        "date": "date",
        "开盘": "open",
        "开盘价": "open",
        "open": "open",
        "收盘": "close",
        "收盘价": "close",
        "close": "close",
        "最高": "high",
        "最高价": "high",
        "high": "high",
        "最低": "low",
        "最低价": "low",
        "low": "low",
        "成交量": "volume",
        "volume": "volume",
        "成交额": "amount",
        "amount": "amount",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "pct_chg": "change_pct",
        "涨跌额": "change",
        "换手率": "turnover",
        "turnover": "turnover",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if volume_in_lots and "volume" in df.columns:
        df["volume"] = df["volume"] * _LOT_SIZE
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    if days and len(df) > days:
        df = df.tail(days).reset_index(drop=True)
    return df


def _fetch_hist_em(code: str, market: str, start_s: str, end_s: str, adjust: str) -> pd.DataFrame:
    if not _em_breaker.allow():
        return pd.DataFrame()
    import akshare as ak
    try:
        if _is_etf(code, market):
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily", start_date=start_s, end_date=end_s, adjust=adjust
            )
        else:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_s, end_date=end_s, adjust=adjust
            )
    except Exception:
        _em_breaker.record_failure()
        raise
    if df is None or len(df) == 0:
        _em_breaker.record_failure()
        return pd.DataFrame()
    _em_breaker.record_success()
    return df


def _fetch_hist_sina(code: str, market: str, adjust: str = "qfq") -> pd.DataFrame:
    """Sina daily history.

    `ak.stock_zh_a_daily` honours `adjust` for stocks, but the ETF endpoint
    (`ak.fund_etf_hist_sina`) takes no adjust argument at all and always returns
    unadjusted prices. Verified against East Money on 510050: sina matched EM's
    raw close to 3e-6, and EM's qfq close differed by up to 0.30.

    The caller records the actual convention via _APPLIED_ADJUST_ATTR.
    """
    import akshare as ak
    prefix = market.lower()
    if _is_etf(code, market):
        return ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
    return ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust=adjust or "")


def _fetch_hist_tencent(code: str, market: str) -> pd.DataFrame:
    """Tencent daily K as last-resort fallback.

    The endpoint URL hardcodes `qfq`, so this source ALWAYS returns forward-
    adjusted (前复权) prices regardless of what the caller asked for. Verified
    against East Money on 510050: tencent matched EM's qfq close (mean |diff|
    0.031) and diverged from EM's raw close.

    Returns raw upstream values: Tencent reports volume in 手 (lots), so the
    caller must pass volume_in_lots=True to _standardize_ohlcv. Tencent does not
    return 成交额, so amount is left as 0 rather than being aliased to volume.
    """
    import requests
    prefix = _exch_prefix(market)
    symbol = f"{prefix}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,320,qfq"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    r = requests.get(url, headers=headers, timeout=12)
    r.raise_for_status()
    js = r.json()
    node = js.get("data", {}).get(symbol, {})
    bars = node.get("qfqday") or node.get("day") or []
    rows = []
    for b in bars:
        if len(b) < 6:
            continue
        try:
            rows.append({
                "date": b[0],
                "open": float(b[1]),
                "close": float(b[2]),
                "high": float(b[3]),
                "low": float(b[4]),
                "volume": float(b[5]),  # 手; converted in _standardize_ohlcv
                "amount": 0.0,          # not provided by this endpoint
            })
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)


@_cached("hist", 180)
def _history_normalized(code: str, market: str, days: int, adjust: str) -> pd.DataFrame:
    """Fetch history for an already-normalized (code, market) pair."""
    end = dt.date.today()
    start = end - dt.timedelta(days=int(days * 1.6) + 30)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    def try_sources():
        # Each source's REAL price convention, which is not always the requested
        # one:
        #   EM      -> honours `adjust` (qfq or raw)
        #   sina    -> stocks honour `adjust`; ETFs are always raw (no such param)
        #   tencent -> always qfq (URL hardcodes it)
        # The convention actually returned is stamped onto the frame so callers
        # can tell the user instead of silently showing the wrong prices.
        # Volume unit per source: EM -> 手, sina -> 股, tencent -> 手.
        try:
            df = _standardize_ohlcv(
                _fetch_hist_em(code, market, start_s, end_s, adjust),
                days,
                volume_in_lots=True,
            )
            if len(df):
                df.attrs[_APPLIED_ADJUST_ATTR] = adjust
                return df
        except Exception as e:
            logger.warning("EM hist %s.%s: %s", code, market, e)
        # Sina
        try:
            df = _standardize_ohlcv(_fetch_hist_sina(code, market, adjust), days)
            if len(df):
                # ETF endpoint cannot adjust, so it is always unadjusted.
                sina_applied = "" if _is_etf(code, market) else adjust
                if sina_applied != adjust:
                    logger.info(
                        "sina hist %s.%s: requested adjust=%r but source is raw",
                        code, market, adjust,
                    )
                df.attrs[_APPLIED_ADJUST_ATTR] = sina_applied
                return df
        except Exception as e:
            logger.warning("Sina hist %s.%s: %s", code, market, e)
        # Tencent
        try:
            df = _standardize_ohlcv(
                _fetch_hist_tencent(code, market), days, volume_in_lots=True
            )
            if len(df):
                if adjust != "qfq":
                    logger.info(
                        "tencent hist %s.%s: requested adjust=%r but source is qfq",
                        code, market, adjust,
                    )
                df.attrs[_APPLIED_ADJUST_ATTR] = "qfq"
                return df
        except Exception as e:
            logger.warning("Tencent hist %s.%s: %s", code, market, e)
        return pd.DataFrame()

    df = try_sources()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df


# Frontend asks for 120/250/500; snap to tiers so one cached frame serves all.
_HISTORY_TIERS = (60, 120, 250, 500)


def _normalize_adjust(adjust: str | None) -> str:
    """Map a user-supplied adjust value to a canonical one.

    Only "qfq" and "" (unadjusted) are supported; anything else falls back to
    qfq. Note this deliberately does NOT turn "" into "qfq" — that previously
    made the UI's 不复权 button silently return forward-adjusted data.
    """
    a = (adjust or "").strip().lower()
    if a in ("", "none", "raw", "bfq"):
        return ""
    return "qfq"


def _snap_days(days: int) -> int:
    for tier in _HISTORY_TIERS:
        if days <= tier:
            return tier
    return max(_HISTORY_TIERS[-1], int(days))


# Records which price convention a returned frame actually carries.
# "qfq" = 前复权, "" = 不复权. Set by the fallback chain because two upstreams
# cannot honour the requested adjust (see _fetch_hist_sina / _fetch_hist_tencent).
_APPLIED_ADJUST_ATTR = "applied_adjust"


def _applied_adjust(df: pd.DataFrame) -> str | None:
    """Price convention actually present in `df`, or None if unknown."""
    if df is None or len(df) == 0:
        return None
    return getattr(df, "attrs", {}).get(_APPLIED_ADJUST_ATTR)


def get_history(code: str, market: str = "", days: int = 250, adjust: str = "qfq") -> pd.DataFrame:
    """Daily OHLCV history with multi-source fallback.

    Symbols are normalized before caching so '159516'+'SZ' and '159516.SZ'
    share one cache entry instead of fetching twice.

    `adjust`: "qfq" for 前复权, "" for 不复权. NOTE: the requested convention is
    not always available — see `_fetch_hist_sina` / `_fetch_hist_tencent`. The
    frame's `attrs["applied_adjust"]` reports what was really returned, so
    callers must not assume it matches the request.
    """
    code, market = parse_symbol(_full_code(code, market))
    tier = _snap_days(int(days))
    df = _history_normalized(code, market, tier, _normalize_adjust(adjust))
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if len(df) > int(days):
        df = df.tail(int(days)).reset_index(drop=True)
    return df


def get_quotes_batch(pairs, max_workers: int = 8) -> dict:
    """Fetch quotes concurrently; returns {(code, MARKET): quote_dict}.

    Serial get_quote costs ~200-700ms per symbol; a 15-symbol reference page
    measured 2.8s serial vs 0.86s with 8 workers. Cache is lock-guarded, and
    sina/tencent endpoints are per-symbol, so concurrency is safe upstream.
    """
    uniq = list(dict.fromkeys(
        (str(c).strip().upper(), str(m or "").strip().upper()) for c, m in pairs
    ))
    if not uniq:
        return {}
    if len(uniq) == 1:
        return {uniq[0]: get_quote(uniq[0][0], uniq[0][1])}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(uniq))) as ex:
        vals = list(ex.map(lambda cm: get_quote(cm[0], cm[1]), uniq))
    return dict(zip(uniq, vals))


def get_name(code: str, market: str = "") -> str:
    q = get_quote(code, market)
    return q.get("name") or _full_code(*parse_symbol(code if market else code))


@_cached("intraday", 60)
def _intraday_normalized(code: str, market: str, scale: int, datalen: int) -> list[dict]:
    """Intraday fetch for an already-normalized (code, MARKET) pair.

    Split out of `get_intraday` so the cache key is built from normalized
    arguments: '159516'+'SZ' and '159516.SZ'+'' previously produced two entries
    for identical data.
    """
    prefix = _exch_prefix(market)
    try:
        import requests
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
            f"?symbol={prefix}{code}&scale={int(scale)}&ma=no&datalen={int(datalen)}"
        )
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        out = []
        for row in data or []:
            try:
                out.append({
                    "time": str(row.get("day", "")),
                    "open": float(row.get("open") or 0),
                    "high": float(row.get("high") or 0),
                    "low": float(row.get("low") or 0),
                    "close": float(row.get("close") or 0),
                    "volume": float(row.get("volume") or 0),
                    "amount": float(row.get("amount") or 0),
                })
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        logger.warning("intraday %s.%s: %s", code, market, e)
        return []


def get_intraday(code: str, market: str = "", scale: int = 1, datalen: int = 240) -> list[dict]:
    """Intraday minute bars from sina (1/5/15/30/60 min).

    Returns [{time, open, high, low, close, volume}], oldest->newest.
    scale=1 with datalen=240 covers a full trading session.

    Symbols are normalized before the cache lookup so alternate spellings of the
    same instrument share one entry.
    """
    code, market = parse_symbol(_full_code(code, market))
    return _intraday_normalized(code, market, int(scale), int(datalen))


def _market_from_prefix(symbol: str) -> str:
    s = symbol.strip().lower()
    if s.startswith("sh"):
        return "SH"
    if s.startswith("sz"):
        return "SZ"
    if s.startswith("bj"):
        return "BJ"
    return ""


# sina suggest types worth surfacing: A-shares and exchange-traded funds.
_SINA_TYPE_MAP = {"11": "stock", "12": "stock", "203": "etf"}


@_cached("search", 300)
def _search_sina_normalized(q: str) -> list[dict]:
    """Sina suggest for an already-normalized query (stripped + lowercased).

    Caching on the normalized form means '茅台 ' and '茅台' share one entry
    instead of issuing two upstream requests.
    """
    import requests
    url = f"https://suggest3.sinajs.cn/suggest/key={q}"
    r = requests.get(
        url,
        headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=8,
    )
    r.raise_for_status()
    text = r.content.decode("gbk", errors="replace")
    inner = text.split('="', 1)[-1].rsplit('"', 1)[0]
    out: list[dict] = []
    seen = set()
    for row in inner.split(";"):
        row = row.strip()
        if not row:
            continue
        f = row.split(",")
        if len(f) < 5:
            continue
        kind = _SINA_TYPE_MAP.get(f[1].strip())
        if not kind:
            continue
        code = f[2].strip()
        market = _market_from_prefix(f[3])
        if not code or not market:
            continue
        key = (code, market)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "code": code,
            "market": market,
            "full": _full_code(code, market),
            "name": (f[4] or f[0] or code).strip(),
            "type": kind,
        })
    return out


def _search_sina(q: str) -> list[dict]:
    """Sina suggest — one light request, works while EM is rate-limited."""
    return _search_sina_normalized(q.strip().lower())


def _search_spot_tables(q: str, limit: int) -> list[dict]:
    """Fallback: filter the cached EM snapshots.

    Only *already-warm* tables are consulted. Calling `_etf_spot_df()` and
    `_stock_spot_df()` unconditionally used to trigger a full A-share snapshot
    download (~18s, 16 paged requests) on an ordinary search, because `search()`
    gates on "either table is warm".
    """
    results: list[dict] = []
    tables = []
    if cache_fresh("etf_spot:"):
        tables.append((_etf_spot_df(), "etf"))
    if cache_fresh("stock_spot:"):
        tables.append((_stock_spot_df(), "stock"))
    for df, kind in tables:
        if df is None or len(df) == 0 or len(df.columns) < 2:
            continue
        try:
            col_code = "代码" if "代码" in df.columns else df.columns[0]
            col_name = "名称" if "名称" in df.columns else df.columns[1]
            mask = (
                df[col_code].astype(str).str.contains(q, case=False, na=False)
                | df[col_name].astype(str).str.contains(q, case=False, na=False)
            )
            for _, r in df[mask].head(limit).iterrows():
                code = str(r[col_code]).zfill(6)
                _, market = parse_symbol(_full_code(code, ""))
                if any(x["code"] == code and x["market"] == market for x in results):
                    continue
                results.append({
                    "code": code,
                    "market": market,
                    "full": _full_code(code, market),
                    "name": str(r[col_name]),
                    "type": kind,
                })
        except Exception as e:
            logger.warning("%s search: %s", kind, e)
        if len(results) >= limit:
            break
    return results[:limit]


def search(query: str, limit: int = 8) -> list[dict]:
    """Symbol search. Sina suggest first (fast + survives EM throttling)."""
    q = query.strip()
    if not q:
        return []

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def merge(items: list[dict]) -> None:
        for it in items:
            key = (it["code"], it["market"])
            if key in seen:
                continue
            seen.add(key)
            results.append(it)

    try:
        merge(_search_sina(q))
    except Exception as e:
        logger.warning("sina suggest search: %s", e)

    # A bare 6-digit code may be missing from suggest; resolve it directly.
    if not results and q.isdigit() and len(q) == 6:
        for market in ("SH", "SZ"):
            quote = _quote_sina_hq(q, market, _full_code(q, market))
            if quote and quote.get("price"):
                merge([{
                    "code": q,
                    "market": market,
                    "full": quote["full"],
                    "name": quote.get("name") or q,
                    "type": quote.get("type", "stock"),
                }])
                break

    if len(results) < limit and (cache_fresh("etf_spot:") or cache_fresh("stock_spot:")):
        merge(_search_spot_tables(q, limit - len(results)))

    return results[:limit]


@_cached("stock_info", 3600, skip_empty=True)
def _stock_info_cached(code: str) -> dict:
    """Industry / listing metadata. Cached 1h; respects EM circuit breaker."""
    if not _em_breaker.allow():
        return {}
    try:
        import akshare as ak
        info = ak.stock_individual_info_em(symbol=code)
        if info is None or not len(info):
            return {}
        kv = {}
        for _, row in info.iterrows():
            kv[str(row.iloc[0])] = row.iloc[1]
        _em_breaker.record_success()
        return kv
    except Exception as e:
        _em_breaker.record_failure()
        logger.debug("stock info %s: %s", code, e)
        return {}


def get_fundamentals(code: str, market: str = "") -> dict:
    """Best-effort fundamentals from free sources."""
    code, market = parse_symbol(code if not market else _full_code(code, market))
    full = _full_code(code, market)
    quote = get_quote(code, market)
    # EM spot full-table is expensive and often rate-limited; only try when core fields missing.
    needs_em = quote.get("pe") is None and quote.get("pb") is None
    if needs_em and _em_breaker.allow():
        em = _quote_em_spot(code, market, full)
        if em:
            for k, v in em.items():
                if quote.get(k) in (None, "", 0) and v not in (None, ""):
                    quote[k] = v
            if em.get("name") and (not quote.get("name") or quote.get("name") == full):
                quote["name"] = em["name"]
    out = {
        "code": code,
        "market": market,
        "full": full,
        "name": quote.get("name", full),
        "type": quote.get("type", "stock"),
        "price": quote.get("price"),
        "pe": quote.get("pe"),
        "pb": quote.get("pb"),
        "total_mv": quote.get("total_mv"),
        "circ_mv": quote.get("circ_mv"),
        "turnover": quote.get("turnover"),
        "items": [],
    }

    def item(label, value, hint=""):
        if value is None or value == "":
            return
        out["items"].append({"label": label, "value": value, "hint": hint})

    item("最新价", quote.get("price"), "元")
    item("涨跌幅", quote.get("change_pct"), "%")
    item("市盈率(动态)", quote.get("pe"), "倍")
    item("市净率", quote.get("pb"), "倍")
    item("总市值", _fmt_mv(quote.get("total_mv")), "")
    item("流通市值", _fmt_mv(quote.get("circ_mv")), "")
    item("换手率", quote.get("turnover"), "%")

    # Extra stock fundamentals via spot already covers most; try individual info for stocks
    # Cached per code to avoid a 2-6s EM round-trip on every fundamentals open.
    if out["type"] == "stock":
        info_kv = _stock_info_cached(code)
        if info_kv:
            item("行业", info_kv.get("行业", info_kv.get("所属行业")))
            item("上市时间", info_kv.get("上市时间"))
            item("总股本", info_kv.get("总股本"))
            item("流通股", info_kv.get("流通股"))

    # 52w range from history
    try:
        hist = get_history(code, market, days=250)
        if hist is not None and len(hist):
            item("区间最高(约1年)", float(hist["high"].max()))
            item("区间最低(约1年)", float(hist["low"].min()))
            item("近20日均价", float(hist["close"].tail(20).mean()))
    except Exception:
        pass

    return out


def _fmt_mv(v) -> str | None:
    if v is None:
        return None
    try:
        x = float(v)
    except Exception:
        return str(v)
    if x >= 1e12:
        return f"{x / 1e12:.2f} 万亿"
    if x >= 1e8:
        return f"{x / 1e8:.2f} 亿"
    if x >= 1e4:
        return f"{x / 1e4:.2f} 万"
    return f"{x:.0f}"
