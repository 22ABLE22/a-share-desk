"""Stricter multi-timeframe resonance + plain-language action card.

Combines daily score with weekly structure and short-term momentum,
then produces a single recommended action with scenarios.

Explanations come from a transparent rule engine only: an optional local LLM
(Ollama) was removed, because it could not improve the underlying signal and
added a dependency that was usually absent.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import data as data_svc
from . import indicators as ind
from . import signal_engine as se

logger = logging.getLogger("advisor")

# Defaults; can be overridden by backtest recommendations
DEFAULT_BUY = 72.0
DEFAULT_SELL = 35.0
# Resonance bonus gates
WEAK = 55.0
STRONG_BUY = 72.0
STRONG_SELL = 35.0


def _weekly_from_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) < 20:
        return df
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    w = d.resample("W-FRI").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(how="any")
    w = w.reset_index()
    w["date"] = w["date"].dt.strftime("%Y-%m-%d")
    return w


def _tf_votes(df_daily: pd.DataFrame) -> dict[str, Any]:
    """Weekly + daily structure votes (causal indicators)."""
    votes = {"weekly": 0, "daily": 0, "notes": []}

    weekly = ind.enrich(_weekly_from_daily(df_daily.tail(400)))
    if len(weekly) >= 12:
        last = weekly.iloc[-1]
        ma5, ma20 = last.get("ma5"), last.get("ma20")
        close = float(last["close"])
        if pd.notna(ma5) and pd.notna(ma20):
            if ma5 > ma20 and close > ma5:
                votes["weekly"] = 1
                votes["notes"].append("周线：短期均线上方，中期偏强")
            elif ma5 < ma20 and close < ma5:
                votes["weekly"] = -1
                votes["notes"].append("周线：短期均线下方，中期偏弱")
            else:
                votes["notes"].append("周线：结构中性")
        dif, dea = last.get("macd_dif"), last.get("macd_dea")
        if pd.notna(dif) and pd.notna(dea):
            if dif > dea:
                votes["weekly"] += 0.5
            else:
                votes["weekly"] -= 0.5

    daily = ind.enrich(df_daily)
    if len(daily) >= 30:
        last = daily.iloc[-1]
        close = float(last["close"])
        ma5, ma20, ma60 = last.get("ma5"), last.get("ma20"), last.get("ma60")
        if all(pd.notna(x) for x in (ma5, ma20, ma60)):
            if ma5 > ma20 and close > ma60:
                votes["daily"] = 1
                votes["notes"].append("日线：短中期多头且站上60日线")
            elif ma5 < ma20 and close < ma60:
                votes["daily"] = -1
                votes["notes"].append("日线：短中期空头且在60日线下")
            else:
                votes["notes"].append("日线：多空交织")
        rsi14 = last.get("rsi14")
        if pd.notna(rsi14):
            if rsi14 >= 70:
                votes["daily"] -= 0.5
                votes["notes"].append(f"日线 RSI14={rsi14:.0f} 偏超买")
            elif rsi14 <= 35:
                votes["daily"] += 0.5
                votes["notes"].append(f"日线 RSI14={rsi14:.0f} 偏超卖")
    return votes


def _resonate(score: float, votes: dict, buy_th: float, sell_th: float) -> dict[str, Any]:
    """Apply multi-timeframe gate. Returns action/tone/confidence/resonance."""
    weekly = float(votes.get("weekly") or 0)
    daily_v = float(votes.get("daily") or 0)
    both_up = weekly > 0 and daily_v > 0
    both_down = weekly < 0 and daily_v < 0
    mixed = (weekly > 0 > daily_v) or (daily_v > 0 > weekly)

    action = "观望"
    tone = "neutral"
    confidence = 50
    resonance = "无共振"

    if score >= buy_th:
        if both_up:
            action, tone = "买入关注（多周期共振）", "buy"
            confidence = 82
            resonance = "日周同向偏多"
        elif weekly > 0 or daily_v > 0:
            action, tone = "偏多，仅小仓/已有仓持有", "buy"
            confidence = 68
            resonance = "单周期偏多"
        else:
            action, tone = "分高但未共振，建议观望", "neutral"
            confidence = 55
            resonance = "高分无共振"
    elif score <= sell_th:
        if both_down:
            action, tone = "减仓/止损关注（多周期共振）", "sell"
            confidence = 82
            resonance = "日周同向偏空"
        elif weekly < 0 or daily_v < 0:
            action, tone = "偏空，不加仓，评估减仓", "sell"
            confidence = 68
            resonance = "单周期偏空"
        else:
            action, tone = "分低但未完全共振，谨慎观望", "neutral"
            confidence = 55
            resonance = "低分未完全共振"
    else:
        # Middle band: structure decides; never force a buy on a bearish score
        if both_up and score >= WEAK:
            action, tone = "可小仓试错，等更强确认", "buy"
            confidence = 62
            resonance = "结构偏多但分数中性"
        elif both_down or (score < 50 and (weekly < 0 or daily_v < 0)):
            action, tone = "结构或分数偏空，回避加仓", "sell"
            confidence = 62
            resonance = "偏空结构" if both_down else "分数偏弱"
        elif mixed:
            action, tone = "日周不一致，观望", "neutral"
            confidence = 50
            resonance = "日周方向不一致"
        else:
            action, tone = "观望为主", "neutral"
            confidence = 52
            resonance = "无明确共振"

    return {
        "action": action,
        "tone": tone,
        "confidence": confidence,
        "resonance": resonance,
        "weekly_vote": weekly,
        "daily_vote": daily_v,
    }


def _scenarios(price: float, stop: float, target: float, cost: float | None) -> list[dict]:
    scen = []
    if price and stop:
        scen.append({
            "if": f"跌破 {stop:.4g}",
            "then": "按纪律减仓或止损，不要幻想回本加仓",
            "kind": "sell",
        })
    if price and target:
        scen.append({
            "if": f"站稳/触及 {target:.4g}",
            "then": "可部分止盈，或上移止损保护利润",
            "kind": "buy",
        })
    if cost and price:
        pnl = (price / cost - 1) * 100
        scen.append({
            "if": f"相对成本 {pnl:+.1f}%",
            "then": "亏损扩大时先控仓，盈利时想清楚是兑现还是持有",
            "kind": "neutral",
        })
    scen.append({
        "if": "横盘无量",
        "then": "不动也是一种操作，避免频繁进出",
        "kind": "neutral",
    })
    return scen


def advise_symbol(
    code: str,
    market: str = "",
    cost: float | None = None,
    shares: float | None = None,
    buy_th: float = DEFAULT_BUY,
    sell_th: float = DEFAULT_SELL,
) -> dict[str, Any]:
    base = se.score_symbol(code, market, cost=cost, shares=shares)
    if not base.get("ok"):
        return base

    df = data_svc.get_history(code, market, days=250)
    votes = _tf_votes(df) if df is not None and len(df) else {"weekly": 0, "daily": 0, "notes": []}
    gate = _resonate(float(base.get("score") or 50), votes, buy_th, sell_th)

    price = float(base.get("price") or 0)
    stop = float(base.get("stop_hint") or 0)
    target = float(base.get("target_hint") or 0)
    scenarios = _scenarios(price, stop, target, cost)

    # Position hint (very rough, educational)
    conf = gate["confidence"]
    if gate["tone"] == "buy" and conf >= 75:
        pos_hint = "若要参与，建议小仓（例如不超过权益资产的 10–20%），并预设止损"
    elif gate["tone"] == "buy":
        pos_hint = "更适合已有仓位持有观察，新仓宜极小或等待"
    elif gate["tone"] == "sell":
        pos_hint = "优先控制风险：不加仓；已有仓按成本与止损纪律处理"
    else:
        pos_hint = "保持现有仓位或空仓观望，等待共振"

    summary = (
        f"{base.get('name')} 现价 {price:.4g}，综合分 {base.get('score')}。"
        f"{gate['action']}（置信 {conf}%，{gate['resonance']}）。"
    )

    detail_lines = list(votes.get("notes") or [])
    for f in (base.get("factors") or [])[:5]:
        detail_lines.append(f"{f.get('name')}（{f.get('delta'):+}）：{f.get('detail')}")

    out = {
        **base,
        "buy_th": buy_th,
        "sell_th": sell_th,
        "resonance": gate,
        "action": gate["action"],
        "tone": gate["tone"],
        "confidence": gate["confidence"],
        "pos_hint": pos_hint,
        "scenarios": scenarios,
        "summary": summary,
        "explain_points": detail_lines,
        "engine": "rules+resonance",
    }

    return out


def advise_universe(
    symbols: list[tuple[str, str, float | None, float | None]],
    buy_th: float = DEFAULT_BUY,
    sell_th: float = DEFAULT_SELL,
) -> list[dict]:
    rows = []
    for code, market, cost, shares in symbols:
        try:
            rows.append(advise_symbol(code, market, cost, shares, buy_th, sell_th))
        except Exception as e:
            rows.append({"ok": False, "code": code, "market": market, "error": str(e)})
    # Sort: strong buy first, then by confidence, avoid dumping fails on top
    def key(x):
        if not x.get("ok"):
            return (2, 0, x.get("code") or "")
        tone = x.get("tone")
        pri = 0 if tone == "buy" else 1 if tone == "neutral" else 2
        return (pri, -(x.get("confidence") or 0), x.get("code") or "")

    rows.sort(key=key)
    return rows
