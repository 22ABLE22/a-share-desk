"""Pure-function modules: indicators and the market clock.

These need no network and no fixtures, which is why they are the highest-value
first tests for this project.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from services import indicators as ind
from services import market_clock as mc
from services.market_clock import CN_TZ


# --------------------------------------------------------------------------
# RSI warmup (regression A1: fillna(100) reported "fully overbought")
# --------------------------------------------------------------------------
def test_rsi_warmup_is_nan_not_100():
    close = pd.Series(np.linspace(10, 20, 40))
    rsi = ind.rsi(close, 14)
    assert rsi.iloc[:13].isna().all(), "warmup rows must stay NaN"
    assert not (rsi.iloc[:13] == 100.0).any(), "warmup must never read as overbought"


def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(close, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    close = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(close, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_series_is_neutral_50():
    close = pd.Series([10.0] * 40)
    assert ind.rsi(close, 14).iloc[-1] == pytest.approx(50.0)


# --------------------------------------------------------------------------
# Indicator plumbing
# --------------------------------------------------------------------------
def _ohlcv(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 50 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": rng.integers(1000, 5000, n).astype(float),
    })


def test_enrich_adds_expected_columns():
    out = ind.enrich(_ohlcv())
    for col in ("ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_dea",
                "macd_hist", "rsi6", "rsi14", "boll_mid", "boll_upper", "boll_lower"):
        assert col in out.columns


def test_enrich_does_not_mutate_input():
    df = _ohlcv()
    before = df.copy()
    ind.enrich(df)
    pd.testing.assert_frame_equal(df, before)


def test_ma_requires_full_window():
    """min_periods=window: no partial averages that would distort early signals."""
    out = ind.enrich(_ohlcv(60))
    assert out["ma20"].iloc[:19].isna().all()
    assert pd.notna(out["ma20"].iloc[19])


def test_bollinger_bands_ordered():
    out = ind.enrich(_ohlcv())
    tail = out.dropna(subset=["boll_upper", "boll_lower"])
    assert (tail["boll_upper"] >= tail["boll_mid"]).all()
    assert (tail["boll_mid"] >= tail["boll_lower"]).all()


def test_detect_signals_empty_on_short_input():
    assert ind.detect_signals(_ohlcv(10)) == []


def test_detect_signals_returns_wellformed_items():
    for s in ind.detect_signals(_ohlcv(120)):
        assert set(s) >= {"date", "close", "kind", "name", "detail", "strength"}
        assert s["kind"] in {"buy", "sell", "neutral"}
        assert isinstance(s["close"], float)


def test_summarize_trend_handles_short_input():
    assert ind.summarize_trend(_ohlcv(5))["label"] == "数据不足"


def test_summarize_trend_labels_are_known():
    t = ind.summarize_trend(_ohlcv(120))
    assert t["label"] in {"偏多", "偏空", "震荡", "数据不足"}
    assert t["tone"] in {"up", "down", "neutral"}


# --------------------------------------------------------------------------
# Market clock (regression A3: no holiday calendar)
# --------------------------------------------------------------------------
def _phase(y, m, d, hh=10, mm=0):
    return mc.market_phase(dt.datetime(y, m, d, hh, mm, tzinfo=CN_TZ))


@pytest.mark.parametrize("day", [dt.date(2026, 1, 1), dt.date(2026, 10, 1),
                                 dt.date(2026, 2, 17), dt.date(2026, 5, 1)])
def test_known_2026_holidays(day):
    assert mc.is_holiday(day) is True
    assert mc.is_trading_day(day) is False


def test_weekend_is_not_a_trading_day():
    assert mc.is_trading_day(dt.date(2026, 2, 14)) is False  # Saturday


def test_ordinary_weekday_is_a_trading_day():
    assert mc.is_trading_day(dt.date(2026, 9, 14)) is True


def test_holiday_phase_is_closed():
    ph = _phase(2026, 10, 1)
    assert ph["phase"] == "closed"
    assert ph["is_holiday"] is True
    assert ph["is_trading"] is False


@pytest.mark.parametrize("hh,mm,expected", [
    (9, 20, "pre_open"),
    (10, 0, "trading"),
    (12, 0, "lunch"),
    (14, 0, "trading"),
    (16, 0, "after"),
])
def test_session_phases(hh, mm, expected):
    assert _phase(2026, 9, 14, hh, mm)["phase"] == expected


def test_should_monitor_matches_is_trading():
    moment = dt.datetime(2026, 9, 14, 10, 0, tzinfo=CN_TZ)
    assert mc.should_monitor(moment) is mc.market_phase(moment)["is_trading"]


def test_phase_payload_shape():
    ph = _phase(2026, 9, 14)
    assert set(ph) == {"now", "phase", "detail", "is_trading", "is_holiday", "weekday"}
