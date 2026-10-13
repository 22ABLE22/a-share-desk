"""Frontend guards checked statically.

The frontend is a plain browser script with no module system, so these assert on
source structure rather than executing the DOM. They are deliberately narrow:
each one pins a specific regression so it cannot silently come back.

P1-9  the holding form offered only SZ/SH, so BJ holdings could not be entered
      even though the backend supports them.
P1-10 no request sequencing existed, so a slow earlier response could overwrite
      a newer one after rapid symbol/period switching.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND / "js" / "app.js"
INDEX_HTML = FRONTEND / "index.html"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# P1-9: Beijing Stock Exchange must be selectable
# --------------------------------------------------------------------------
def test_market_select_offers_bj(index_html):
    select = re.search(r'<select name="market">(.*?)</select>', index_html, re.S)
    assert select, "market <select> not found"
    body = select.group(1)
    assert 'value="BJ"' in body, "BJ option missing from the holding form"
    for market in ("SH", "SZ", "BJ"):
        assert f'value="{market}"' in body


# --------------------------------------------------------------------------
# P1-10: request sequencing guard
# --------------------------------------------------------------------------
def test_guard_helpers_defined(app_js):
    assert "function beginRequest(" in app_js
    assert "function isCurrent(" in app_js


def _run_guard_js(app_js: str, expression: str):
    """Execute the real guard helpers in Node and return the result.

    The helpers are extracted verbatim from app.js so this tests the shipped
    code, not a copy of it. Node is required; the test skips if it is absent.
    """
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    start = app_js.index("const _reqTokens")
    end = app_js.index("function fmtNum")
    helpers = app_js[start:end]

    script = helpers + "\nconsole.log(JSON.stringify(" + expression + "));"
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def test_guard_returns_increasing_tokens(app_js):
    """A later request must invalidate the earlier one, per view."""
    result = _run_guard_js(app_js, """(() => {
      const t1 = beginRequest("chart");
      const currentAfterT1 = isCurrent("chart", t1);
      const t2 = beginRequest("chart");
      return {
        currentAfterT1,
        distinct: t2 !== t1,
        staleIsCurrent: isCurrent("chart", t1),
        newIsCurrent: isCurrent("chart", t2),
      };
    })()""")

    assert result["currentAfterT1"] is True
    assert result["distinct"] is True
    assert result["staleIsCurrent"] is False, "older token must become stale"
    assert result["newIsCurrent"] is True


def test_guard_is_per_view(app_js):
    """Starting a request in one view must not invalidate another view."""
    result = _run_guard_js(app_js, """(() => {
      const chart = beginRequest("chart");
      beginRequest("fund");
      return { chartStillCurrent: isCurrent("chart", chart) };
    })()""")
    assert result["chartStillCurrent"] is True


@pytest.mark.parametrize("view", ["chart", "fund"])
def test_loaders_use_the_guard(app_js, view):
    """Both racing loaders must take a token and check it after awaiting."""
    assert f'beginRequest("{view}")' in app_js, f"{view} loader takes no token"
    assert f'isCurrent("{view}", token)' in app_js, f"{view} loader never checks its token"


def test_chart_checks_token_before_rendering(app_js):
    """The token check must precede the render calls, or it guards nothing."""
    body = re.search(r"async function loadChart\(\) \{(.*?)\n\}", app_js, re.S).group(1)
    check = body.find('isCurrent("chart", token)')
    render = body.find("renderKline(data)")
    assert check != -1 and render != -1
    assert check < render, "token must be checked before rendering"


def test_fund_checks_token_before_rendering(app_js):
    body = re.search(r"async function loadFundamentals\(\) \{(.*?)\n\}", app_js, re.S).group(1)
    check = body.find('isCurrent("fund", token)')
    render = body.find("setActiveSymbol(data.code")
    assert check != -1 and render != -1
    assert check < render, "token must be checked before rendering"
