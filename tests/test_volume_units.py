"""Volume-unit normalization (regression: 100x discrepancy between sources).

East Money history reports 成交量 in 手 (lots), sina reports 股 (shares). Before
the fix the same symbol's `volume` silently changed unit depending on which
source won the fallback chain, which corrupted the volume factor.
"""
from __future__ import annotations

import pandas as pd
import pytest

from services import data


def _em_frame(volume: float = 31556.0) -> pd.DataFrame:
    """Minimal East Money-shaped frame (Chinese columns, 手)."""
    return pd.DataFrame({
        "日期": ["2026-10-12"],
        "开盘": [1280.0],
        "收盘": [1288.0],
        "最高": [1290.0],
        "最低": [1275.0],
        "成交量": [volume],
        "成交额": [4042857559.0],
    })


def _sina_frame(volume: float = 3155604.0) -> pd.DataFrame:
    """Minimal sina-shaped frame (English columns, already 股)."""
    return pd.DataFrame({
        "date": ["2026-10-12"],
        "open": [1280.0],
        "close": [1288.0],
        "high": [1290.0],
        "low": [1275.0],
        "volume": [volume],
    })


def test_lot_size_is_100():
    assert data._LOT_SIZE == 100


def test_em_volume_converted_lots_to_shares():
    """volume_in_lots=True must multiply by 100."""
    out = data._standardize_ohlcv(_em_frame(31556.0), volume_in_lots=True)
    assert out["volume"].iloc[0] == pytest.approx(3_155_600.0)


def test_sina_volume_left_untouched():
    """sina already reports 股, so no conversion must be applied."""
    out = data._standardize_ohlcv(_sina_frame(3_155_604.0), volume_in_lots=False)
    assert out["volume"].iloc[0] == pytest.approx(3_155_604.0)


def test_sources_agree_after_normalization():
    """The whole point: both sources must land on the same unit."""
    em = data._standardize_ohlcv(_em_frame(31556.0), volume_in_lots=True)
    sina = data._standardize_ohlcv(_sina_frame(3155604.0), volume_in_lots=False)
    ratio = em["volume"].iloc[0] / sina["volume"].iloc[0]
    assert ratio == pytest.approx(1.0, abs=1e-3)


def test_tencent_fetch_returns_raw_lots():
    """_fetch_hist_tencent must NOT pre-scale; the caller declares the unit.

    Pre-scaling inside the fetch and again in _standardize_ohlcv would apply the
    100x factor twice.
    """
    import inspect

    src = inspect.getsource(data._fetch_hist_tencent)
    assert "float(b[5])" in src
    assert "* 100" not in src, "tencent volume must not be pre-scaled"


def test_fallback_chain_declares_units():
    """EM and tencent pass volume_in_lots=True; sina does not."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(data._history_normalized))
    tree = ast.parse(src)

    # Map each source call to whether it was given volume_in_lots=True.
    seen: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # The source fetch is the first positional arg of _standardize_ohlcv.
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        inner = node.args[0].func
        name = inner.attr if isinstance(inner, ast.Attribute) else getattr(inner, "id", "")
        if name.startswith("_fetch_hist_"):
            kwargs = {k.arg: k.value for k in node.keywords}
            lots = kwargs.get("volume_in_lots")
            seen[name] = isinstance(lots, ast.Constant) and lots.value is True

    assert seen.get("_fetch_hist_em") is True, "EM volume is in 手 -> needs conversion"
    assert seen.get("_fetch_hist_tencent") is True, "tencent volume is in 手 -> needs conversion"
    assert seen.get("_fetch_hist_sina") is False, "sina volume is already 股"


def test_amount_not_fabricated_from_volume():
    """Tencent returns no 成交额; amount must stay 0, never alias volume."""
    import inspect

    src = inspect.getsource(data._fetch_hist_tencent)
    assert '"amount": 0.0' in src
