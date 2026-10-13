"""Scoring rules must be shared by live scoring and backtest calibration.

Regression P1-1: `backtest._score_series` re-implemented a *subset* of the
factors (no volume, no short-term momentum, no MACD histogram), so
`/api/backtest` measured the accuracy of a different model than the one the UI
shows. Both now call `signal_engine._bar_factors`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import backtest, indicators as ind, signal_engine as se


def _series(n: int = 120, trend: float = 0.0, seed: int = 7) -> pd.DataFrame:
    """Deterministic OHLCV frame; `trend` adds drift per bar."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(trend, 1.0, n))
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    })


def test_score_from_factors_base_is_neutral():
    assert se._score_from_factors([]) == 50.0


def test_score_from_factors_sums_and_clamps():
    assert se._score_from_factors([{"delta": 10}, {"delta": -4}]) == 56.0
    assert se._score_from_factors([{"delta": 999}]) == 100.0
    assert se._score_from_factors([{"delta": -999}]) == 0.0


def test_bar_factors_include_volume_and_momentum():
    """The factors the old backtest omitted must be present in the shared rules.

    Conditions are forced: a volume surge (>=1.3x) plus a 5-bar drop of >=8%
    for the volume and momentum factors respectively. A neutral volume ratio
    deliberately emits nothing, so this test must not rely on random data.
    """
    df = _series(120, trend=-0.5, seed=11)
    # Force a volume surge in the most recent 5 bars.
    df.loc[df.index[-5:], "volume"] = df["volume"].iloc[-20:].mean() * 3.0
    # Force a >=8% 5-bar decline so the momentum factor fires.
    df.loc[df.index[-1], "close"] = float(df["close"].iloc[-6]) * 0.90

    out = ind.enrich(df)
    names = {f["name"] for f in se._bar_factors(out, len(out) - 1)}
    assert names & {"放量上行", "放量下跌", "缩量"}, "volume factor missing"
    assert names & {"短线超跌", "短线过热"}, "momentum factor missing"


def test_bar_factors_are_causal_within_a_frame():
    """Factors for bar i must ignore bars after i (no lookahead)."""
    df = _series(120, seed=5)
    out_full = ind.enrich(df)
    out_trunc = ind.enrich(df.iloc[:100].reset_index(drop=True))

    full_at_99 = se._bar_factors(out_full, 99)
    trunc_at_99 = se._bar_factors(out_trunc, 99)
    assert full_at_99 == trunc_at_99


def test_volume_factor_absent_when_ratio_neutral():
    """A neutral volume ratio must not emit a factor (guards over-reporting)."""
    df = _series(120, seed=21)
    df["volume"] = 1_000_000.0  # perfectly flat -> ratio exactly 1.0
    out = ind.enrich(df)
    names = {f["name"] for f in se._bar_factors(out, len(out) - 1)}
    assert not (names & {"放量上行", "放量下跌", "缩量"})


def test_backtest_series_matches_shared_rules():
    """A backtest bar score must equal scoring that same bar via the live rules."""
    df = _series(150)
    series = backtest._score_series(df)
    out = ind.enrich(df)
    expected = se._score_from_factors(se._bar_factors(out, len(out) - 1))
    assert float(series.iloc[-1]) == pytest.approx(expected)


def test_backtest_series_is_causal():
    """Scoring bar i must not depend on bars after i."""
    df = _series(200)
    full = backtest._score_series(df)
    truncated = backtest._score_series(df.iloc[:150].reset_index(drop=True))
    assert float(truncated.iloc[-1]) == pytest.approx(float(full.iloc[149]))


def test_backtest_does_not_reimplement_factors():
    """Guard against the rules being copied back into backtest.py."""
    import inspect

    src = inspect.getsource(backtest)
    assert "_bar_factors" in src, "backtest must delegate to the shared rule set"
    # The old duplicate hardcoded these deltas inline.
    assert "s += 12" not in src
    assert "s -= 12" not in src


def test_cost_overlay_only_affects_scoring_via_shared_rules():
    """Position overlay must come from _bar_factors, not a second code path."""
    df = _series(120)
    out = ind.enrich(df)
    price = float(out["close"].iloc[-1])

    no_cost = se._bar_factors(out, len(out) - 1, price=price, cost=None)
    big_loss = se._bar_factors(out, len(out) - 1, price=price, cost=price * 2.0)

    assert "持仓亏损扩大" not in {f["name"] for f in no_cost}
    assert "持仓亏损扩大" in {f["name"] for f in big_loss}
    assert se._score_from_factors(big_loss) < se._score_from_factors(no_cost)


def test_uptrend_scores_above_downtrend():
    """Sanity: the rule set must be directionally sensible."""
    up = _series(150, trend=0.5, seed=1)
    down = _series(150, trend=-0.5, seed=1)
    up_score = float(backtest._score_series(up).iloc[-1])
    down_score = float(backtest._score_series(down).iloc[-1])
    assert up_score > down_score
