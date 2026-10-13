"""沪深股票分析 — FastAPI entry."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from services import advisor as advisor_svc
from services import alerts as alerts_svc
from services import backtest as bt_svc
from services import data as data_svc
from services import indicators as ind
from services import market_clock as clock_svc
from services import monitor as monitor_svc
from services import portfolio as pf_svc
from services import reference as ref_svc
from services import signal_engine as se_svc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("a-share-desk")

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        monitor_svc.start(45)
        logger.info("market monitor auto-started")
    except Exception:
        logger.exception("failed to start monitor")
    yield
    try:
        monitor_svc.shutdown()
    except Exception:
        logger.exception("failed to stop monitor cleanly")


app = FastAPI(title="沪深股票分析", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HoldingIn(BaseModel):
    code: str
    market: str = ""
    shares: float = Field(gt=0)
    cost: float = Field(gt=0)
    note: str = ""
    stop_line: float | None = Field(default=None, gt=0)
    take_line: float | None = Field(default=None, gt=0)


class HoldingPatch(BaseModel):
    shares: float | None = Field(default=None, gt=0)
    cost: float | None = Field(default=None, gt=0)
    note: str | None = None
    stop_line: float | None = Field(default=None, gt=0)
    take_line: float | None = Field(default=None, gt=0)


class WatchIn(BaseModel):
    code: str
    market: str = ""


@app.get("/api/health")
def health():
    return {"ok": True, "app": "a-share-desk"}


@app.get("/api/quote")
def quote(symbol: str, market: str = ""):
    return data_svc.get_quote(symbol, market)


@app.get("/api/intraday")
def intraday(symbol: str, market: str = "", scale: int = 1):
    """Minute bars for the current (or most recent) trading session."""
    scale = scale if scale in (1, 5, 15, 30, 60) else 1
    datalen = 240 if scale == 1 else int(240 / scale) + 8
    code, mkt = data_svc.parse_symbol(symbol if not market else data_svc._full_code(symbol, market))
    bars = data_svc.get_intraday(code, mkt, scale=scale, datalen=datalen)
    if not bars:
        raise HTTPException(status_code=404, detail="无分时数据")
    q = data_svc.get_quote(code, mkt)
    # 均价线：累计成交额 / 累计成交量（amount 缺失时回退 close*volume 近似）
    cum_amt = 0.0
    cum_vol = 0.0
    for b in bars:
        amt = b.get("amount") or 0.0
        if not amt:
            amt = b["close"] * b["volume"]
        cum_amt += amt
        cum_vol += b["volume"]
        b["avg_price"] = round(cum_amt / cum_vol, 4) if cum_vol else b["close"]
    return {
        "code": code,
        "market": mkt,
        "full": q.get("full"),
        "name": q.get("name"),
        "scale": scale,
        "prev_close": q.get("prev_close"),
        "bars": bars,
    }


def _frames_to_records(df) -> list[dict]:
    """NaN/NaT -> None in one vectorized pass.

    Whole-frame astype(object) + where(pd.notna) is the only form that
    actually leaves plain None behind here (per-column .map re-promoted
    floats to NaN, which broke JSON encoding of /api/history).
    """
    import pandas as pd
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


@app.get("/api/history")
def history(symbol: str, market: str = "", days: int = 250, adjust: str = "qfq"):
    df = data_svc.get_history(symbol, market, days=days, adjust=adjust)
    if df is None or len(df) == 0:
        raise HTTPException(status_code=404, detail="无历史数据")
    enriched = ind.enrich(df)
    records = _frames_to_records(enriched)
    code, mkt = data_svc.parse_symbol(symbol if not market else data_svc._full_code(symbol, market))
    # Name comes from the history cache's last bar context; one cheap quote.
    q = data_svc.get_quote(code, mkt)
    return {
        "code": code,
        "market": mkt,
        "full": q.get("full"),
        "name": q.get("name"),
        "type": q.get("type"),
        "bars": records,
    }


@app.get("/api/signals")
def signals(symbol: str, market: str = "", days: int = 250):
    df = data_svc.get_history(symbol, market, days=days)
    if df is None or len(df) == 0:
        raise HTTPException(status_code=404, detail="无历史数据")
    code, mkt = data_svc.parse_symbol(symbol if not market else data_svc._full_code(symbol, market))
    q = data_svc.get_quote(code, mkt)
    return {
        "code": code,
        "market": mkt,
        "full": q.get("full"),
        "name": q.get("name"),
        "trend": ind.summarize_trend(df),
        "signals": ind.detect_signals(df),
    }


@app.get("/api/fundamentals")
def fundamentals(symbol: str, market: str = ""):
    return data_svc.get_fundamentals(symbol, market)


@app.get("/api/search")
def search(q: str):
    return data_svc.search(q)


@app.get("/api/reference")
def reference():
    """Educational same-style ETF reference list for conservative learners."""
    groups = ref_svc.get_reference_groups()
    # Enrich with live quotes concurrently (per-symbol serial cost was ~3s)
    pairs = [(it["code"], it["market"]) for g in groups for it in g["items"]]
    quotes = data_svc.get_quotes_batch(pairs)
    out = []
    for g in groups:
        items = []
        for it in g["items"]:
            q = quotes.get((str(it["code"]).upper(), str(it["market"]).upper())) or {}
            items.append({
                **it,
                "full": f"{it['code']}.{it['market']}",
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "live_name": q.get("name") or it["name"],
                "type": q.get("type", "etf"),
            })
        out.append({**g, "items": items})
    return {"groups": out, "disclaimer": "仅为同类品种科普与观察清单，不构成投资建议。"}


@app.get("/api/portfolio")
def get_portfolio():
    try:
        return pf_svc.portfolio_overview()
    except Exception as e:
        logger.exception("portfolio")
        raise HTTPException(status_code=502, detail=f"行情获取失败: {e}")


@app.post("/api/portfolio/holdings")
def add_holding(body: HoldingIn):
    try:
        pf_svc.add_holding(body.model_dump())
        return pf_svc.portfolio_overview()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/portfolio/holdings/{index}")
def patch_holding(index: int, body: HoldingPatch):
    try:
        # 保留显式 null（用于清除提醒线），忽略未传字段
        payload = {k: getattr(body, k) for k in body.model_fields_set}
        pf_svc.update_holding(index, payload)
        return pf_svc.portfolio_overview()
    except IndexError:
        raise HTTPException(status_code=404, detail="持仓不存在")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/portfolio/holdings/{index}")
def delete_holding(index: int):
    try:
        pf_svc.remove_holding(index)
        return pf_svc.portfolio_overview()
    except IndexError:
        raise HTTPException(status_code=404, detail="持仓不存在")


@app.post("/api/portfolio/watch")
def add_watch(body: WatchIn):
    pf_svc.add_watch(body.model_dump())
    return pf_svc.portfolio_overview()


@app.delete("/api/portfolio/watch")
def remove_watch(code: str, market: str = ""):
    pf_svc.remove_watch(code, market)
    return pf_svc.portfolio_overview()


@app.get("/api/market/clock")
def market_clock():
    return clock_svc.market_phase()


@app.get("/api/signals/pro")
def signals_pro(symbol: str = "", market: str = ""):
    """Single-symbol professional score, or full universe if symbol omitted."""
    if symbol:
        # attach cost if in holdings
        cost = shares = None
        for h in pf_svc.load().get("holdings", []):
            if h["code"] == symbol or data_svc._full_code(h["code"], h.get("market", "")).upper() == data_svc._full_code(symbol, market).upper():
                cost, shares = float(h.get("cost") or 0) or None, float(h.get("shares") or 0) or None
                break
        return se_svc.score_symbol(symbol, market, cost=cost, shares=shares)
    pf = pf_svc.load()
    rows = []
    seen = set()
    for h in pf.get("holdings", []):
        key = (h["code"], h.get("market", ""))
        seen.add(key)
        rows.append((key[0], key[1], float(h.get("cost") or 0) or None, float(h.get("shares") or 0) or None))
    for w in pf.get("watchlist", []):
        key = (w["code"], w.get("market", ""))
        if key in seen:
            continue
        rows.append((key[0], key[1], None, None))
    items = se_svc.score_universe(rows)
    return {"items": items, "disclaimer": "规则因子综合分，仅供学习观察，不构成投资建议。"}


@app.get("/api/advice")
def advice(symbol: str = "", market: str = "", use_llm: bool = False):
    """Stricter multi-TF action card (plain language)."""
    pf = pf_svc.load()
    if symbol:
        cost = shares = None
        for h in pf.get("holdings", []):
            full_h = data_svc._full_code(h["code"], h.get("market", "")).upper()
            full_s = data_svc._full_code(symbol, market).upper()
            if h["code"] == symbol or full_h == full_s:
                cost, shares = float(h.get("cost") or 0) or None, float(h.get("shares") or 0) or None
                break
        return advisor_svc.advise_symbol(symbol, market, cost=cost, shares=shares, use_llm=use_llm)

    rows = []
    seen = set()
    for h in pf.get("holdings", []):
        key = (h["code"], h.get("market", ""))
        seen.add(key)
        rows.append((key[0], key[1], float(h.get("cost") or 0) or None, float(h.get("shares") or 0) or None))
    for w in pf.get("watchlist", []):
        key = (w["code"], w.get("market", ""))
        if key in seen:
            continue
        rows.append((key[0], key[1], None, None))
    items = advisor_svc.advise_universe(rows, use_llm=use_llm)
    return {
        "items": items,
        "disclaimer": "多周期共振建议，仅供学习观察，不构成投资建议。",
    }


@app.get("/api/backtest")
def backtest(symbol: str, market: str = "", days: int = 500, hold_days: int = 5):
    """Historical threshold calibration for one symbol."""
    return bt_svc.evaluate_thresholds(symbol, market, days=days, hold_days=hold_days)


@app.get("/api/monitor")
def monitor_status():
    return monitor_svc.get_status()


@app.post("/api/monitor/start")
def monitor_start(interval_sec: int = 45):
    return monitor_svc.start(interval_sec)


@app.post("/api/monitor/stop")
def monitor_stop():
    return monitor_svc.stop()


@app.post("/api/monitor/scan")
def monitor_scan():
    items = monitor_svc.scan_once()
    return {"items": items, "status": monitor_svc.get_status()}


@app.get("/api/alerts")
def get_alerts(limit: int = 50):
    return {
        "alerts": alerts_svc.list_alerts(limit),
        "clock": clock_svc.market_phase(),
        "monitor": monitor_svc.get_status(),
    }


@app.delete("/api/alerts")
def clear_alerts():
    n = alerts_svc.clear_alerts()
    return {"cleared": n}


# Static frontend
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(FRONTEND / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8765, reload=False)
