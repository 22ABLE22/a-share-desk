"""Price-convention (复权) handling.

Regression P1-8. Two independent defects were found here:

1. `get_history` applied `adjust or "qfq"`, so a request for 不复权 ("") was
   silently rewritten to qfq — the UI's 不复权 button never worked at all,
   regardless of which source served the data.

2. Even with (1) fixed, two upstreams cannot honour the request: sina's ETF
   endpoint takes no adjust argument (always 不复权) and tencent hardcodes qfq
   in its URL. Whichever one the fallback chain lands on decides the actual
   convention, so it must be reported rather than assumed.

Measured against East Money on 510050: sina matched EM's *raw* close (mean
|diff| 3e-6) while EM's qfq close differed by up to 0.30; tencent matched EM's
*qfq* close (mean |diff| 0.031).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import data


def _frame(n: int = 30) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "open": np.linspace(10, 12, n),
        "high": np.linspace(10.5, 12.5, n),
        "low": np.linspace(9.5, 11.5, n),
        "close": np.linspace(10, 12, n),
        "volume": np.full(n, 1000.0),
    })


# --------------------------------------------------------------------------
# Defect 1: adjust must not be silently rewritten
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("", ""), ("qfq", "qfq"), (None, ""), ("bfq", ""), ("raw", ""),
    ("QFQ", "qfq"), ("  ", ""), ("none", ""),
])
def test_normalize_adjust(value, expected):
    assert data._normalize_adjust(value) == expected


def test_normalize_adjust_does_not_force_qfq():
    """The old `adjust or "qfq"` is exactly what broke 不复权."""
    assert data._normalize_adjust("") == ""
    assert data._normalize_adjust(None) == ""


def test_get_history_requests_raw(monkeypatch):
    """A 不复权 request must reach the fetch layer as raw, not qfq."""
    seen = {}

    def fake_normalized(code, market, days, adjust):
        seen["adjust"] = adjust
        return _frame()

    monkeypatch.setattr(data, "_history_normalized", fake_normalized)
    data.get_history("600519", "SH", days=60, adjust="")
    assert seen["adjust"] == ""


def test_get_history_requests_qfq(monkeypatch):
    seen = {}

    def fake_normalized(code, market, days, adjust):
        seen["adjust"] = adjust
        return _frame()

    monkeypatch.setattr(data, "_history_normalized", fake_normalized)
    data.get_history("600519", "SH", days=60, adjust="qfq")
    assert seen["adjust"] == "qfq"


# --------------------------------------------------------------------------
# Defect 2: the applied convention must be reported
# --------------------------------------------------------------------------
def test_applied_adjust_reads_attr():
    df = _frame()
    df.attrs[data._APPLIED_ADJUST_ATTR] = "qfq"
    assert data._applied_adjust(df) == "qfq"


def test_applied_adjust_none_for_empty():
    assert data._applied_adjust(pd.DataFrame()) is None
    assert data._applied_adjust(None) is None


def test_applied_adjust_absent_attr_is_none():
    assert data._applied_adjust(_frame()) is None


def test_attrs_survive_tail_and_reset_index():
    """get_history slices with tail().reset_index(); the marker must survive."""
    df = _frame(100)
    df.attrs[data._APPLIED_ADJUST_ATTR] = "qfq"
    out = df.tail(50).reset_index(drop=True)
    assert data._applied_adjust(out) == "qfq"


# --------------------------------------------------------------------------
# Fallback chain: which convention each source actually yields
# --------------------------------------------------------------------------
def _force_chain(monkeypatch, *, em=True, sina=True, tencent=True):
    """Enable/disable each source by making it raise."""
    def down(*a, **k):
        raise RuntimeError("source down")

    if not em:
        monkeypatch.setattr(data, "_fetch_hist_em", down)
    if not sina:
        monkeypatch.setattr(data, "_fetch_hist_sina", down)
    if not tencent:
        monkeypatch.setattr(data, "_fetch_hist_tencent", down)


def test_em_honours_requested_qfq(monkeypatch):
    monkeypatch.setattr(data, "_fetch_hist_em", lambda *a, **k: _frame())
    _force_chain(monkeypatch, sina=False, tencent=False)
    df = data.get_history("600519", "SH", days=60, adjust="qfq")
    assert data._applied_adjust(df) == "qfq"


def test_em_honours_requested_raw(monkeypatch):
    monkeypatch.setattr(data, "_fetch_hist_em", lambda *a, **k: _frame())
    _force_chain(monkeypatch, sina=False, tencent=False)
    df = data.get_history("600519", "SH", days=60, adjust="")
    assert data._applied_adjust(df) == ""


def test_sina_etf_reports_raw_when_qfq_requested(monkeypatch):
    """sina's ETF endpoint cannot adjust -> must be reported as raw."""
    monkeypatch.setattr(data, "_fetch_hist_sina", lambda *a, **k: _frame())
    _force_chain(monkeypatch, em=False, tencent=False)
    df = data.get_history("510050", "SH", days=60, adjust="qfq")
    assert data._applied_adjust(df) == ""


def test_sina_stock_honours_qfq(monkeypatch):
    """The stock endpoint does accept adjust, so qfq stays qfq."""
    monkeypatch.setattr(data, "_fetch_hist_sina", lambda *a, **k: _frame())
    _force_chain(monkeypatch, em=False, tencent=False)
    df = data.get_history("600519", "SH", days=60, adjust="qfq")
    assert data._applied_adjust(df) == "qfq"


def test_sina_stock_honours_raw(monkeypatch):
    monkeypatch.setattr(data, "_fetch_hist_sina", lambda *a, **k: _frame())
    _force_chain(monkeypatch, em=False, tencent=False)
    df = data.get_history("600519", "SH", days=60, adjust="")
    assert data._applied_adjust(df) == ""


def test_tencent_reports_qfq_when_raw_requested(monkeypatch):
    """tencent hardcodes qfq, so a raw request must be reported as qfq."""
    monkeypatch.setattr(data, "_fetch_hist_tencent", lambda *a, **k: _frame())
    _force_chain(monkeypatch, em=False, sina=False)
    df = data.get_history("600519", "SH", days=60, adjust="")
    assert data._applied_adjust(df) == "qfq"


def test_tencent_qfq_request_is_consistent(monkeypatch):
    monkeypatch.setattr(data, "_fetch_hist_tencent", lambda *a, **k: _frame())
    _force_chain(monkeypatch, em=False, sina=False)
    df = data.get_history("600519", "SH", days=60, adjust="qfq")
    assert data._applied_adjust(df) == "qfq"


def test_tencent_url_hardcodes_qfq():
    """Documents *why* tencent always returns qfq."""
    import inspect

    src = inspect.getsource(data._fetch_hist_tencent)
    assert ",320,qfq" in src


def test_sina_etf_call_passes_no_adjust():
    """Documents *why* sina ETFs are always raw.

    Checks the executable code only: the docstring legitimately mentions
    "adjust", so scanning the whole source would match the explanation.
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(data._fetch_hist_sina))
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "fund_etf_hist_sina"
    ]
    assert calls, "fund_etf_hist_sina call not found"
    for call in calls:
        kwarg_names = {k.arg for k in call.keywords}
        assert "adjust" not in kwarg_names, "ETF endpoint must not be passed adjust"
