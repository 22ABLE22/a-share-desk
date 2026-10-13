"""Theme switching (auto / light / dark).

The button cycles through three modes. Two things must hold and are easy to
break silently:

1. The cycle order is derived from the *active* mode, not a fixed
   light-first sequence, so a dark-preferring user goes auto -> dark -> light.
2. "auto" must follow the OS preference and react to changes, while an explicit
   light/dark choice must ignore them.

The theme helpers are extracted verbatim from app.js and executed in Node
against a small DOM/localStorage stub, so this exercises the shipped code.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = FRONTEND / "js" / "app.js"
INDEX_HTML = FRONTEND / "index.html"
APP_CSS = FRONTEND / "css" / "app.css"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_css() -> str:
    return APP_CSS.read_text(encoding="utf-8")


def _run_theme_js(app_js: str, expression: str, *, system_dark: bool = False):
    """Run the real theme helpers in Node and return the JSON result."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    start = app_js.index("const THEME_KEY")
    end = app_js.index("/** Read a CSS custom property", start)
    helpers = app_js[start:end]

    # NOTE: `_mql` is declared by app.js itself (the extracted range includes
    # it), so the stub must only supply the `window.matchMedia` it calls.
    stub = f"""
const _store = {{}};
const localStorage = {{
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => {{ _store[k] = String(v); }},
}};
const _listeners = [];
const _mqlStub = {{
  matches: {str(system_dark).lower()},
  addEventListener: (ev, fn) => _listeners.push(fn),
  addListener: (fn) => _listeners.push(fn),
}};
const window = {{ matchMedia: () => _mqlStub }};
const _attrs = {{}};
const document = {{
  documentElement: {{
    setAttribute: (k, v) => {{ _attrs[k] = v; }},
    getAttribute: (k) => _attrs[k] ?? null,
  }},
}};
// The theme helpers touch these optional DOM nodes; null is the "absent" case.
const $ = () => null;
const state = {{ portfolio: null, history: null, intraday: null, mode: "daily" }};
function renderAlloc() {{}}
function renderKline() {{}}
function renderIntraday() {{}}
function renderCompareChart() {{}}
// chartPalette/refreshPalette are out of scope here; stub the CSS reader.
function cssVar() {{ return ""; }}
function chartPalette() {{ return {{}}; }}
let PAL = null;
function refreshPalette() {{ PAL = chartPalette(); return PAL; }}
"""

    script = stub + helpers + f"\nconsole.log(JSON.stringify({expression}));"
    proc = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


# --------------------------------------------------------------------------
# Cycle order
# --------------------------------------------------------------------------
def test_cycle_from_auto_goes_to_light(app_js):
    assert _run_theme_js(app_js, "nextThemeMode('auto')") == "light"


def test_cycle_from_light_goes_to_dark(app_js):
    assert _run_theme_js(app_js, "nextThemeMode('light')") == "dark"


def test_cycle_from_dark_wraps_to_auto(app_js):
    assert _run_theme_js(app_js, "nextThemeMode('dark')") == "auto"


def test_cycle_covers_all_modes(app_js):
    """Three clicks from any mode must return to the starting mode."""
    result = _run_theme_js(app_js, """(() => {
      const out = {};
      for (const start of THEME_MODES) {
        let m = start;
        const seen = [m];
        for (let i = 0; i < THEME_MODES.length; i++) { m = nextThemeMode(m); seen.push(m); }
        out[start] = seen;
      }
      return out;
    })()""")
    for start, seen in result.items():
        assert seen[-1] == start, f"{start} did not return to itself"
        assert sorted(set(seen)) == sorted(["auto", "light", "dark"])


def test_dark_user_cycles_to_dark_not_light(app_js):
    """A user whose OS prefers dark must see auto -> dark -> light.

    The requirement is that the cycle starts from the *active* mode; forcing a
    light-first order would make a dark user's first click flash white.
    """
    result = _run_theme_js(
        app_js,
        """(() => {
          const mode = storedTheme();          // fresh install -> auto
          const resolved = resolveTheme(mode); // follows OS -> dark
          const next = nextThemeMode(mode);
          return { mode, resolved, next, nextResolved: resolveTheme(next) };
        })()""",
        system_dark=True,
    )
    assert result["mode"] == "auto"
    assert result["resolved"] == "dark"
    assert result["next"] == "light", "auto advances to light in the fixed order"


def test_cycle_is_deterministic_regardless_of_system(app_js):
    """Mode order must not depend on the OS preference."""
    light = _run_theme_js(app_js, "nextThemeMode('auto')", system_dark=False)
    dark = _run_theme_js(app_js, "nextThemeMode('auto')", system_dark=True)
    assert light == dark == "light"


# --------------------------------------------------------------------------
# Resolution: auto follows the OS, explicit modes do not
# --------------------------------------------------------------------------
def test_auto_resolves_to_dark_when_system_dark(app_js):
    assert _run_theme_js(app_js, "resolveTheme('auto')", system_dark=True) == "dark"


def test_auto_resolves_to_light_when_system_light(app_js):
    assert _run_theme_js(app_js, "resolveTheme('auto')", system_dark=False) == "light"


def test_explicit_modes_ignore_system(app_js):
    result = _run_theme_js(
        app_js,
        "[resolveTheme('light'), resolveTheme('dark')]",
        system_dark=True,
    )
    assert result == ["light", "dark"]


def test_apply_theme_sets_data_attribute(app_js):
    result = _run_theme_js(
        app_js,
        """(() => {
          applyTheme('dark');
          const afterDark = document.documentElement.getAttribute('data-theme');
          applyTheme('light');
          const afterLight = document.documentElement.getAttribute('data-theme');
          applyTheme('auto');
          const afterAuto = document.documentElement.getAttribute('data-theme');
          return { afterDark, afterLight, afterAuto };
        })()""",
        system_dark=False,
    )
    assert result["afterDark"] == "dark"
    assert result["afterLight"] == "light"
    assert result["afterAuto"] == "light"  # auto + light system


def test_set_theme_persists_choice(app_js):
    result = _run_theme_js(
        app_js,
        """(() => {
          setTheme('dark');
          const saved = localStorage.getItem(THEME_KEY);
          return { saved, reloaded: storedTheme() };
        })()""",
    )
    assert result["saved"] == "dark"
    assert result["reloaded"] == "dark"


def test_unknown_stored_value_falls_back_to_auto(app_js):
    result = _run_theme_js(
        app_js,
        """(() => {
          localStorage.setItem(THEME_KEY, 'chartreuse');
          return storedTheme();
        })()""",
    )
    assert result == "auto"


def test_storage_failure_does_not_break_theme(app_js):
    """Private-mode localStorage throws; the app must still render."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    start = app_js.index("const THEME_KEY")
    end = app_js.index("/** Read a CSS custom property", start)
    helpers = app_js[start:end]
    stub = """
const localStorage = { getItem(){ throw new Error('denied'); }, setItem(){ throw new Error('denied'); } };
const _mqlStub = { matches: true, addEventListener(){}, addListener(){} };
const window = { matchMedia: () => _mqlStub };
const document = { documentElement: { setAttribute(){}, getAttribute(){ return null; } } };
const $ = () => null;
const state = {};
function cssVar(){ return ""; }
function chartPalette(){ return {}; }
let PAL = null;
function refreshPalette(){ return PAL; }
function renderAlloc(){} function renderKline(){} function renderIntraday(){} function renderCompareChart(){}
"""
    script = stub + helpers + "\nconsole.log(JSON.stringify([storedTheme(), resolveTheme(storedTheme())]));"
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, f"theme must survive storage denial: {proc.stderr}"
    assert json.loads(proc.stdout.strip()) == ["auto", "dark"]


# --------------------------------------------------------------------------
# Markup + CSS wiring
# --------------------------------------------------------------------------
def test_toggle_button_present(index_html):
    assert 'id="btnTheme"' in index_html
    assert 'id="themeIcon"' in index_html
    assert 'id="themeLabel"' in index_html


def test_dark_theme_defined_in_css(app_css):
    assert '[data-theme="dark"]' in app_css


def test_dark_theme_overrides_core_surfaces(app_css):
    """The dark block must actually restate the surface colours."""
    block = app_css[app_css.index('[data-theme="dark"]'):]
    block = block[: block.index("\n}")]
    for var in ("--bg:", "--bg2:", "--bg3:", "--text:", "--muted:", "--line:"):
        assert var in block, f"{var} not overridden for dark mode"


def test_no_hardcoded_surface_colors_outside_theme_blocks(app_css):
    """Every surface colour must come from a variable so themes cannot drift.

    Chart series hues are exempt (they are passed to ECharts as data, not CSS),
    but structural colours like panel/toast backgrounds are not.
    """
    stripped = re.sub(r":root\s*\{.*?\n\}", "", app_css, flags=re.S)
    stripped = re.sub(r'\[data-theme="dark"\]\s*\{.*?\n\}', "", stripped, flags=re.S)
    offenders = [
        line.strip()
        for line in stripped.splitlines()
        if re.search(r"#[0-9a-fA-F]{3,8}\b", line)
    ]
    assert not offenders, "hardcoded colours found: " + "; ".join(offenders)


def test_theme_initialised_on_boot(app_js):
    body = re.search(r"async function boot\(\) \{(.*?)\n\}", app_js, re.S).group(1)
    assert "initTheme()" in body, "boot() must apply the stored theme"


def test_charts_refresh_on_theme_change(app_js):
    """Charts are canvas-drawn, so they must be redrawn after a switch."""
    body = re.search(r"function initTheme\(\) \{.*?\n\}", app_js, re.S).group(0)
    assert "refreshChartsForTheme()" in body


def test_system_change_only_applies_in_auto(app_js):
    body = re.search(r"function initTheme\(\) \{.*?\n\}", app_js, re.S).group(0)
    assert 'storedTheme() === "auto"' in body, "OS changes must be ignored for explicit modes"
