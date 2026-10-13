"""Historical walk-forward evaluation of the composite score.

Teaching tool: measures how past score-threshold rules would have behaved.
Not a promise of future returns.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import data as data_svc
from . import indicators as ind
from . import signal_engine as se


def _score_series(df: pd.DataFrame) -> pd.Series:
    """Daily composite score for every bar, using the live rule set.

    Delegates to `signal_engine._bar_factors` so the calibration measures the
    rules the app actually applies. It previously re-implemented a subset of the
    factors (no volume, no short-term momentum), so `/api/backtest` reported the
    accuracy of a different model than the one shown in the UI.

    Each bar is scored from that bar's own close and from indicators computed
    only up to that bar, so the walk-forward result cannot leak future data.
    """
    out = ind.enrich(df)
    n = len(out)
    scores = np.full(n, 50.0)
    for i in range(n):
        # price=bar close: this is a historical replay, not a live quote.
        scores[i] = se._score_from_factors(se._bar_factors(out, i))
    return pd.Series(scores, index=out.index)


def _next_day_return(close: pd.Series, horizon: int = 1) -> pd.Series:
    return close.shift(-horizon) / close - 1.0


def evaluate_thresholds(
    code: str,
    market: str = "",
    days: int = 500,
    buy_levels: tuple[float, ...] = (60, 65, 70, 72, 75),
    sell_levels: tuple[float, ...] = (40, 35, 30, 28, 25),
    hold_days: int = 5,
) -> dict[str, Any]:
    df = data_svc.get_history(code, market, days=days)
    if df is None or len(df) < 80:
        return {"ok": False, "error": "历史数据不足，无法回测"}

    scores = _score_series(df)
    close = df["close"].astype(float)
    fwd = _next_day_return(close, hold_days)

    rows = []
    for b in buy_levels:
        mask = (scores >= b) & fwd.notna()
        if mask.sum() == 0:
            rows.append({"side": "buy", "level": b, "n": 0})
            continue
        ret = fwd[mask]
        rows.append({
            "side": "buy",
            "level": b,
            "n": int(mask.sum()),
            "win_rate": float((ret > 0).mean()),
            "avg_ret": float(ret.mean()),
            "med_ret": float(ret.median()),
            "best": float(ret.max()),
            "worst": float(ret.min()),
        })
    for s in sell_levels:
        mask = (scores <= s) & fwd.notna()
        if mask.sum() == 0:
            rows.append({"side": "sell", "level": s, "n": 0})
            continue
        # "sell side" success = price fell later
        ret = fwd[mask]
        rows.append({
            "side": "sell",
            "level": s,
            "n": int(mask.sum()),
            "win_rate": float((ret < 0).mean()),
            "avg_ret": float(ret.mean()),
            "med_ret": float(ret.median()),
            "best": float(ret.min()),  # best for seller = most negative move
            "worst": float(ret.max()),
        })

    # Recommended stricter thresholds: pick buy with n>=8 and best win_rate
    buys = [r for r in rows if r["side"] == "buy" and r.get("n", 0) >= 8]
    sells = [r for r in rows if r["side"] == "sell" and r.get("n", 0) >= 8]
    rec_buy = max(buys, key=lambda r: (r.get("win_rate") or 0, -r["level"])) if buys else None
    rec_sell = min(sells, key=lambda r: (r.get("win_rate") or 0, r["level"])) if sells else None

    # Buy-and-hold baseline over same window
    valid = fwd.notna()
    baseline = {
        "avg_fwd_ret": float(fwd[valid].mean()) if valid.any() else None,
        "n": int(valid.sum()),
    }

    return {
        "ok": True,
        "code": code,
        "market": market,
        "days": days,
        "hold_days": hold_days,
        "bars": len(df),
        "baseline": baseline,
        "rows": rows,
        "recommended": {
            "buy_score": rec_buy["level"] if rec_buy else 72,
            "buy_win_rate": rec_buy.get("win_rate") if rec_buy else None,
            "buy_n": rec_buy.get("n") if rec_buy else 0,
            "sell_score": rec_sell["level"] if rec_sell else 35,
            "sell_win_rate": rec_sell.get("win_rate") if rec_sell else None,
            "sell_n": rec_sell.get("n") if rec_sell else 0,
            "note": "按历史样本自动选出的更严阈值；样本少时会退回默认 72/35",
        },
        "disclaimer": "教学向回测，样本外表现可能完全不同；不构成投资建议。",
    }
