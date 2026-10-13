/* 沪深股票分析前端 */
const API = "";
const state = {
  symbol: "159516",
  market: "SZ",
  name: "159516.SZ",
  days: 250,
  mode: "daily", // daily | intraday
  adjust: "qfq",
  indicators: { ma: true, boll: false, vol: true, macd: true },
  portfolio: null,
  history: null,
  intraday: null,
  signals: null,
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

/**
 * Request sequencing guard.
 *
 * Rapidly switching symbol/period/tab starts several overlapping fetches; a
 * slower earlier response can land after a newer one and overwrite the UI with
 * stale data. Each logical view takes a token before awaiting and checks it
 * afterwards — only the newest request for that view may render.
 *
 * Usage:
 *   const t = beginRequest("chart");
 *   const data = await api(...);
 *   if (!isCurrent("chart", t)) return;   // a newer request superseded us
 */
const _reqTokens = new Map();

function beginRequest(view) {
  const next = (_reqTokens.get(view) || 0) + 1;
  _reqTokens.set(view, next);
  return next;
}

function isCurrent(view, token) {
  return _reqTokens.get(view) === token;
}

function fmtNum(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  const sign = x > 0 ? "+" : "";
  return sign + fmtNum(x, 2);
}
function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  const sign = x > 0 ? "+" : "";
  return sign + fmtNum(x, 2) + "%";
}
function toneClass(n) {
  const x = Number(n);
  if (Number.isNaN(x) || x === 0) return "flat";
  return x > 0 ? "up" : "down";
}
/** 展示用代码：避免 full 已含 .SH/.SZ 时再拼一次市场后缀 */
function fmtSymbol(code, market, full) {
  const f = (full || "").trim();
  const c = (code || "").trim();
  const m = (market || "").trim().toUpperCase();
  if (!f) return m ? `${c}.${m}` : c;
  // full 已带交易所后缀
  if (/\.(SH|SZ|BJ)$/i.test(f)) return f;
  return m ? `${f}.${m}` : f;
}
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2800);
}

async function api(path) {
  const res = await fetch(API + path);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function setActiveSymbol(code, market, name) {
  state.symbol = code;
  state.market = market;
  state.name = name || `${code}.${market}`;
  $("#activeSymbol").innerHTML = `<strong>${escapeHtml(state.name)}</strong>${escapeHtml(code)}.${escapeHtml(market)}`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/* ---------- Navigation ---------- */
$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".view").forEach((v) => v.classList.remove("active"));
    const view = btn.dataset.view;
    $(`#view-${view}`).classList.add("active");
    if (view === "chart") loadChart();
    if (view === "fund") loadFundamentals();
    if (view === "compare") loadCompare();
    if (view === "dashboard") loadPortfolio();
    if (view === "reference") loadReference();
    if (view === "live") loadLive();
    if (view === "advice") loadAdvice();
  });
});

/* ---------- Search ---------- */
const searchInput = $("#searchInput");
const searchResults = $("#searchResults");
let searchTimer = null;

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) {
    searchResults.classList.add("hidden");
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const items = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (!items.length) {
        searchResults.innerHTML = `<div class="search-item muted">无匹配结果</div>`;
      } else {
        searchResults.innerHTML = items
          .map(
            (it) => `<div class="search-item" data-code="${escapeHtml(it.code)}" data-market="${escapeHtml(it.market)}" data-name="${escapeHtml(it.name)}">
              <span>${escapeHtml(it.name)}</span>
              <span class="muted">${escapeHtml(it.full)} · ${it.type === "etf" ? "ETF" : "股票"}</span>
            </div>`
          )
          .join("");
      }
      searchResults.classList.remove("hidden");
    } catch (e) {
      searchResults.innerHTML = `<div class="search-item">搜索失败：${escapeHtml(e.message)}</div>`;
      searchResults.classList.remove("hidden");
    }
  }, 280);
});

searchResults.addEventListener("click", (e) => {
  const item = e.target.closest(".search-item");
  if (!item || !item.dataset.code) return;
  selectSymbol(item.dataset.code, item.dataset.market, item.dataset.name);
  searchResults.classList.add("hidden");
  searchInput.value = "";
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".symbol-picker")) searchResults.classList.add("hidden");
});

function selectSymbol(code, market, name) {
  setActiveSymbol(code, market, name);
  const active = $(".nav-item.active")?.dataset.view || "dashboard";
  if (active === "chart") loadChart();
  else if (active === "fund") loadFundamentals();
  else if (active === "compare") loadCompare();
  else loadSignalsSide();
}

/* ---------- Chart controls ---------- */
$$("#periodSeg button").forEach((b) => {
  b.addEventListener("click", () => {
    $$("#periodSeg button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    if (b.dataset.mode === "intraday") {
      state.mode = "intraday";
    } else {
      state.mode = "daily";
      state.days = Number(b.dataset.days);
    }
    loadChart();
  });
});
$$("#adjustSeg button").forEach((b) => {
  b.addEventListener("click", () => {
    $$("#adjustSeg button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.adjust = b.dataset.adjust || "";
    loadChart();
  });
});
$$("#indicatorChips input").forEach((c) => {
  c.addEventListener("change", () => {
    state.indicators[c.dataset.ind] = c.checked;
    if (state.mode === "intraday") {
      if (state.intraday) renderIntraday(state.intraday);
    } else if (state.history) renderKline(state.history);
  });
});

$("#btnRefresh").addEventListener("click", () => {
  const active = $(".nav-item.active")?.dataset.view || "dashboard";
  if (active === "dashboard") loadPortfolio();
  else if (active === "chart") loadChart();
  else if (active === "fund") loadFundamentals();
  else loadCompare();
  toast("已刷新");
});

/* ---------- Portfolio ---------- */
async function loadPortfolio() {
  const box = $("#statsRow");
  box.innerHTML = `<div class="stat-card"><div class="label">加载中…</div><div class="value">…</div></div>`;
  try {
    const data = await api("/api/portfolio");
    state.portfolio = data;
    renderPortfolio(data);
  } catch (e) {
    box.innerHTML = `<div class="stat-card"><div class="label">加载失败</div><div class="value">${escapeHtml(e.message)}</div></div>`;
  }
}

function renderPortfolio(data) {
  const stats = $("#statsRow");
  stats.innerHTML = `
    <div class="stat-card">
      <div class="label">总市值</div>
      <div class="value">${fmtNum(data.total_mv, 2)}</div>
      <div class="sub">成本合计 ${fmtNum(data.total_cost, 2)}</div>
    </div>
    <div class="stat-card">
      <div class="label">浮动盈亏</div>
      <div class="value ${toneClass(data.total_pnl)}">${fmtMoney(data.total_pnl)}</div>
      <div class="sub ${toneClass(data.total_pnl_pct)}">${fmtPct(data.total_pnl_pct)}</div>
    </div>
    <div class="stat-card">
      <div class="label">今日盈亏</div>
      <div class="value ${toneClass(data.day_pnl)}">${fmtMoney(data.day_pnl)}</div>
      <div class="sub">按最新价变动估算</div>
    </div>
    <div class="stat-card">
      <div class="label">持仓数量</div>
      <div class="value">${data.holdings.length}</div>
      <div class="sub">自选 ${data.watchlist.length} 只</div>
    </div>
  `;

  const tbody = $("#holdingsTable tbody");
  if (!data.holdings.length) {
    tbody.innerHTML = `<tr><td colspan="10"><div class="empty">暂无持仓，点击右上角添加</div></td></tr>`;
  } else {
    tbody.innerHTML = data.holdings
      .map(
        (h, i) => {
          const lines = [];
          if (h.stop_line != null) lines.push(`损 ${fmtNum(h.stop_line, 3)}`);
          if (h.take_line != null) lines.push(`标 ${fmtNum(h.take_line, 3)}`);
          return `<tr data-code="${escapeHtml(h.code)}" data-market="${escapeHtml(h.market)}" data-name="${escapeHtml(h.name)}" data-idx="${i}">
          <td>
            <div><strong>${escapeHtml(h.name)}</strong></div>
            <div class="muted">${escapeHtml(fmtSymbol(h.code, h.market, h.full))}</div>
          </td>
          <td>${fmtNum(h.shares, 0)}</td>
          <td>${fmtNum(h.cost, 3)}</td>
          <td>${fmtNum(h.price, 3)}</td>
          <td>${fmtNum(h.mv, 2)}</td>
          <td class="${toneClass(h.pnl)}">${fmtMoney(h.pnl)}<div class="muted ${toneClass(h.pnl_pct)}">${fmtPct(h.pnl_pct)}</div></td>
          <td class="${toneClass(h.change_pct)}">${fmtPct(h.change_pct)}<div class="muted ${toneClass(h.day_pnl)}">${fmtMoney(h.day_pnl)}</div></td>
          <td>${fmtNum(h.weight, 1)}%</td>
          <td class="muted">${lines.length ? escapeHtml(lines.join(" / ")) : "—"}</td>
          <td>
            <button class="btn ghost btn-edit" data-idx="${i}">设线</button>
            <button class="btn danger ghost btn-del" data-idx="${i}">删除</button>
          </td>
        </tr>`;
        }
      )
      .join("");
  }

  tbody.querySelectorAll("tr[data-code]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      selectSymbol(tr.dataset.code, tr.dataset.market, tr.dataset.name);
    });
  });
  tbody.querySelectorAll(".btn-edit").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openHoldingDialog(Number(btn.dataset.idx));
    });
  });
  tbody.querySelectorAll(".btn-del").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("确认删除该持仓？")) return;
      try {
        const res = await fetch(`${API}/api/portfolio/holdings/${btn.dataset.idx}`, { method: "DELETE" });
        if (!res.ok) throw new Error("删除失败");
        state.portfolio = await res.json();
        renderPortfolio(state.portfolio);
        toast("已删除持仓");
      } catch (err) {
        toast(err.message);
      }
    });
  });

  renderAlloc(data.holdings);
}

function renderAlloc(holdings) {
  const el = $("#allocChart");
  const chart = echarts.getInstanceByDom(el) || echarts.init(el);
  if (!holdings.length) {
    chart.clear();
    chart.setOption({
      backgroundColor: "transparent",
      title: { text: "暂无持仓", left: "center", top: "middle", textStyle: { color: "#6b7a90", fontSize: 14 } },
    });
    return;
  }
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "item", formatter: "{b}<br/>仓位 {d}%" },
    legend: { bottom: 0, textStyle: { color: "#6b7a90" } },
    series: [
      {
        type: "pie",
        radius: ["42%", "68%"],
        center: ["50%", "46%"],
        itemStyle: { borderColor: "#f4f6fb", borderWidth: 2 },
        label: { color: "#1a2233", formatter: "{b}\n{d}%" },
        data: holdings.map((h) => ({ name: h.name || h.code, value: Number(h.mv.toFixed(2)) })),
      },
    ],
  });
}

/* Add / edit holding */
const holdingDialog = $("#holdingDialog");

function openHoldingDialog(editIdx = null) {
  const form = $("#holdingForm");
  form.reset();
  form.edit_index.value = editIdx == null ? "" : String(editIdx);
  $("#holdingDialogTitle").textContent = editIdx == null ? "添加持仓" : "编辑持仓与提醒线";

  if (editIdx == null) {
    form.code.value = state.symbol;
    form.market.value = state.market;
    form.code.readOnly = false;
    form.market.disabled = false;
    form.shares.readOnly = false;
    form.cost.readOnly = false;
  } else {
    const h = state.portfolio?.holdings?.[editIdx];
    if (!h) return;
    form.code.value = h.code || "";
    form.market.value = h.market || "SZ";
    form.shares.value = h.shares ?? "";
    form.cost.value = h.cost ?? "";
    form.stop_line.value = h.stop_line ?? "";
    form.take_line.value = h.take_line ?? "";
    // 编辑模式：代码/数量/成本只读，专注提醒线；仍可通过删除重加改数量
    form.code.readOnly = true;
    form.market.disabled = true;
    form.shares.readOnly = true;
    form.cost.readOnly = true;
  }
  holdingDialog.showModal();
}

$("#btnAddHolding").addEventListener("click", () => openHoldingDialog(null));
$("#btnHoldingCancel").addEventListener("click", () => {
  holdingDialog.close();
});

function parseOptionalNum(v) {
  if (v === "" || v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

$("#holdingForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  if (!form.checkValidity()) {
    form.reportValidity();
    return;
  }
  const editIdx = form.edit_index.value === "" ? null : Number(form.edit_index.value);
  const stop = parseOptionalNum(form.stop_line.value);
  const take = parseOptionalNum(form.take_line.value);

  try {
    let res;
    if (editIdx == null) {
      const payload = {
        code: form.code.value.trim(),
        market: form.market.value,
        shares: Number(form.shares.value),
        cost: Number(form.cost.value),
      };
      if (stop != null) payload.stop_line = stop;
      if (take != null) payload.take_line = take;
      res = await fetch(`${API}/api/portfolio/holdings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      // PATCH：显式 null 清除；空字符串也按清除处理
      const payload = {
        stop_line: form.stop_line.value === "" ? null : stop,
        take_line: form.take_line.value === "" ? null : take,
      };
      res = await fetch(`${API}/api/portfolio/holdings/${editIdx}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "保存失败");
    }
    state.portfolio = await res.json();
    renderPortfolio(state.portfolio);
    holdingDialog.close();
    toast(editIdx == null ? "持仓已添加" : "提醒线已更新");
  } catch (err) {
    toast(err.message);
  }
});

/* ---------- K-line ---------- */
let klineChart = null;

async function loadChart() {
  const el = $("#klineChart");
  const token = beginRequest("chart");
  state.signals = null; // 防跨标的残留
  if (!klineChart) klineChart = echarts.init(el);
  klineChart.showLoading({ text: "加载中", color: "#2f6fed", maskColor: "rgba(244,246,251,0.6)" });
  if (state.mode === "intraday") {
    try {
      const data = await api(
        `/api/intraday?symbol=${encodeURIComponent(state.symbol)}&market=${encodeURIComponent(state.market)}`
      );
      if (!isCurrent("chart", token)) return; // 已被更新的请求取代
      state.intraday = data;
      setActiveSymbol(data.code, data.market, data.name);
      renderAdjustNotice(null); // 分时无复权概念，清掉提示
      renderIntraday(data);
      await loadSignalsSide();
    } catch (e) {
      if (!isCurrent("chart", token)) return;
      klineChart.hideLoading();
      toast("分时加载失败：" + e.message);
    }
    return;
  }
  try {
    const data = await api(
      `/api/history?symbol=${encodeURIComponent(state.symbol)}&market=${encodeURIComponent(state.market)}&days=${state.days}&adjust=${encodeURIComponent(state.adjust)}`
    );
    if (!isCurrent("chart", token)) return; // 已被更新的请求取代
    state.history = data;
    setActiveSymbol(data.code, data.market, data.name);
    renderAdjustNotice(data);
    renderKline(data);
    await loadSignalsSide();
  } catch (e) {
    if (!isCurrent("chart", token)) return;
    klineChart.hideLoading();
    toast("K线加载失败：" + e.message);
  }
}

function renderIntraday(data) {
  if (!klineChart) klineChart = echarts.init($("#klineChart"));
  const bars = data.bars || [];
  if (!bars.length) {
    klineChart.hideLoading();
    klineChart.clear();
    klineChart.setOption({ title: { text: "暂无分时数据（休市或数据源未覆盖）", left: "center", top: "middle", textStyle: { color: "#6b7a90", fontSize: 14 } } }, true);
    return;
  }
  const prev = Number(data.prev_close) || 0;
  const times = bars.map((b) => (b.time || "").slice(11, 16));
  const prices = bars.map((b) => b.close);
  const avgs = bars.map((b) => b.avg_price);
  const vols = bars.map((b, i) => ({
    value: b.volume,
    itemStyle: {
      color: prev && b.close >= (i > 0 ? bars[i - 1].close : b.open)
        ? "rgba(224,49,49,0.55)" : "rgba(12,166,120,0.55)",
    },
  }));
  // 右轴相对昨收的涨跌幅；无昨收则退化为价格原轴
  const lastP = prices[prices.length - 1];
  const pct = (p) => (prev ? ((p / prev - 1) * 100) : null);
  klineChart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      legend: { top: 8, data: ["价格", "均价"], textStyle: { color: "#6b7a90" } },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "rgba(255,255,255,0.95)",
        textStyle: { color: "#1a2233", fontSize: 12 },
        formatter: (params) => {
          const i = params[0]?.dataIndex ?? 0;
          const b = bars[i];
          if (!b) return "";
          const chg = prev ? ((b.close / prev - 1) * 100) : null;
          const color = chg == null ? "#6b7a90" : chg >= 0 ? "#e03131" : "#0ca678";
          return [
            `<div style="font-weight:600">${(b.time || "").slice(5, 16)}</div>`,
            `价 <b>${fmtNum(b.close, 3)}</b>　均 ${fmtNum(b.avg_price, 3)}`,
            chg != null ? `<span style="color:${color}">较昨收 ${fmtPct(chg)}</span>` : "",
            `量 ${fmtNum(b.volume, 0)}`,
          ].join("<br/>");
        },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 56, right: 56, top: 40, height: "58%" },
        { left: 56, right: 56, top: "76%", height: "14%" },
      ],
      xAxis: [
        { type: "category", data: times, gridIndex: 0, boundaryGap: false, axisLabel: { color: "#6b7a90" }, axisLine: { lineStyle: { color: "#c5d0e0" } } },
        { type: "category", data: times, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#c5d0e0" } } },
      ],
      yAxis: [
        prev
          ? {
              gridIndex: 0, splitNumber: 4,
              min: (v) => Math.min(v.min, prev), max: (v) => Math.max(v.max, prev),
              axisLabel: { color: "#6b7a90", formatter: (v) => (((v / prev - 1) * 100).toFixed(2) + "%") },
              splitLine: { lineStyle: { color: "rgba(107,122,144,0.08)" } },
            }
          : { gridIndex: 0, scale: true, axisLabel: { color: "#6b7a90" }, splitLine: { lineStyle: { color: "rgba(107,122,144,0.08)" } } },
        { gridIndex: 1, splitLine: { show: false }, axisLabel: { color: "#6b7a90", formatter: (v) => (v > 1e8 ? (v / 1e8).toFixed(1) + "亿" : v > 1e4 ? (v / 1e4).toFixed(0) + "万" : v) } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 8, height: 16, fillerColor: "rgba(47,111,237,0.12)", textStyle: { color: "#6b7a90" } },
      ],
      series: [
        {
          name: "价格", type: "line", data: prices, showSymbol: false, smooth: false,
          xAxisIndex: 0, yAxisIndex: 0,
          lineStyle: { width: 1.4, color: "#2f6fed" },
          areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [ { offset: 0, color: "rgba(47,111,237,0.18)" }, { offset: 1, color: "rgba(47,111,237,0.02)" } ] } },
          markLine: prev ? {
            silent: true, symbol: "none",
            data: [{ yAxis: prev, label: { formatter: "昨收 " + fmtNum(prev, 3), color: "#6b7a90", fontSize: 10 }, lineStyle: { color: "#9aa8bb", type: "dashed", width: 1 } }],
          } : undefined,
        },
        { name: "均价", type: "line", data: avgs, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: "#f5a524" } },
        { name: "成交量", type: "bar", data: vols, xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 3 },
      ],
    },
    true
  );
  klineChart.hideLoading();
}

function buildSignalMarks(bars) {
  const sigs = (state.signals || []).filter((s) => s.kind === "buy" || s.kind === "sell");
  if (!sigs.length) return { data: [] };
  const dateIdx = new Map(bars.map((b, i) => [b.date, i]));
  const data = [];
  for (const s of sigs) {
    const i = dateIdx.get(s.date);
    if (i === undefined) continue; // 信号早于当前视窗，跳过
    const b = bars[i];
    const buy = s.kind === "buy";
    data.push({
      name: s.name,
      coord: [i, buy ? b.low : b.high],
      symbol: buy ? "triangle" : "triangle",
      symbolSize: 11,
      symbolRotate: buy ? 0 : 180,
      symbolOffset: buy ? [0, 8] : [0, -8],
      itemStyle: { color: buy ? "#e03131" : "#0ca678", borderColor: "#ffffff", borderWidth: 1 },
      label: { show: false },
      tooltip: { formatter: `${s.date}<br/>${s.name}（${s.detail}）<br/>收盘 ${fmtNum(s.close, 3)}` },
    });
  }
  return { data };
}

/**
 * Report the price convention actually returned by /api/history.
 *
 * Two upstreams cannot honour the requested adjust — sina's ETF endpoint has no
 * adjust parameter (always 不复权) and tencent hardcodes qfq — so when the
 * fallback chain lands on one of them the requested and actual conventions
 * differ. Showing that explicitly is the whole point: previously the UI
 * silently displayed prices under the wrong label.
 */
function renderAdjustNotice(data) {
  const el = $("#adjustNotice");
  if (!el) return;
  const applied = data?.adjust_applied;
  if (!data?.adjust_mismatch || (applied !== "qfq" && applied !== "")) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  const label = applied === "qfq" ? "前复权" : "不复权";
  el.textContent = `当前数据源不支持所选口径，实际为「${label}」`;
  el.classList.remove("hidden");
}

function renderKline(data) {
  if (!klineChart) klineChart = echarts.init($("#klineChart"));
  const bars = data.bars || [];
  if (!bars.length) {
    klineChart.hideLoading();
    return;
  }
  const dates = bars.map((b) => b.date);
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high]);
  const vols = bars.map((b) => b.volume);
  const showMA = state.indicators.ma;
  const showBoll = state.indicators.boll;
  const showVol = state.indicators.vol;
  const showMacd = state.indicators.macd;

  const series = [
    {
      name: "K线",
      type: "candlestick",
      data: ohlc,
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: {
        color: "#e03131",
        color0: "#0ca678",
        borderColor: "#e03131",
        borderColor0: "#0ca678",
      },
      markPoint: buildSignalMarks(bars),
    },
  ];

  if (showMA) {
    ["ma5", "ma10", "ma20", "ma60"].forEach((key, i) => {
      const colors = ["#f5c542", "#2f6fed", "#c084fc", "#38bdf8"];
      series.push({
        name: key.toUpperCase(),
        type: "line",
        data: bars.map((b) => b[key]),
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.2, color: colors[i] },
        xAxisIndex: 0,
        yAxisIndex: 0,
      });
    });
  }

  if (showBoll) {
    [
      ["boll_upper", "#f5a524"],
      ["boll_mid", "#6b7a90"],
      ["boll_lower", "#0ca678"],
    ].forEach(([key, color]) => {
      series.push({
        name: key,
        type: "line",
        data: bars.map((b) => b[key]),
        showSymbol: false,
        lineStyle: { width: 1, type: key === "boll_mid" ? "solid" : "dashed", color },
        xAxisIndex: 0,
        yAxisIndex: 0,
      });
    });
  }

  // 动态子图栈：主图之后依次是 成交量、MACD（各自独占 grid/xAxis/yAxis 槽位）
  const panels = [];
  if (showVol) panels.push("vol");
  if (showMacd) panels.push("macd");

  if (showVol) {
    const idx = 1 + panels.indexOf("vol");
    series.push({
      name: "成交量",
      type: "bar",
      data: vols.map((v, i) => ({
        value: v,
        itemStyle: {
          color: bars[i].close >= bars[i].open ? "rgba(224,49,49,0.55)" : "rgba(12,166,120,0.55)",
        },
      })),
      xAxisIndex: idx,
      yAxisIndex: idx,
      barMaxWidth: 8,
    });
  }

  if (showMacd) {
    const idx = 1 + panels.indexOf("macd");
    series.push(
      {
        name: "MACD柱",
        type: "bar",
        data: bars.map((b) => ({
          value: b.macd_hist,
          itemStyle: {
            color: (b.macd_hist ?? 0) >= 0 ? "rgba(224,49,49,0.7)" : "rgba(12,166,120,0.7)",
          },
        })),
        xAxisIndex: idx,
        yAxisIndex: idx,
        barMaxWidth: 6,
      },
      {
        name: "DIF",
        type: "line",
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.1, color: "#f5c542" },
        data: bars.map((b) => b.macd_dif),
        xAxisIndex: idx,
        yAxisIndex: idx,
      },
      {
        name: "DEA",
        type: "line",
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.1, color: "#38bdf8" },
        data: bars.map((b) => b.macd_dea),
        xAxisIndex: idx,
        yAxisIndex: idx,
      }
    );
  }

  // 布局：主图占大头，每个子图 14%；无子图时主图吃满
  const grid = [{ left: 56, right: 16, top: 40 }];
  if (panels.length === 0) {
    grid[0].bottom = 48;
  } else if (panels.length === 1) {
    grid[0].height = "58%";
    grid.push({ left: 56, right: 16, top: "76%", height: "14%" });
  } else {
    grid[0].height = "50%";
    grid.push({ left: 56, right: 16, top: "64%", height: "12%" });
    grid.push({ left: 56, right: 16, top: "79%", height: "12%" });
  }

  const xAxis = grid.map((_, i) => ({
    type: "category",
    data: dates,
    gridIndex: i,
    axisLine: { lineStyle: { color: "#c5d0e0" } },
    axisLabel: { show: i === 0, color: "#6b7a90" },
  }));

  const yAxis = grid.map((_, i) => {
    if (i === 1 && panels[0] === "vol") {
      return {
        scale: true,
        gridIndex: i,
        splitLine: { show: false },
        axisLabel: { color: "#6b7a90", formatter: (v) => (v > 1e8 ? (v / 1e8).toFixed(1) + "亿" : v > 1e4 ? (v / 1e4).toFixed(0) + "万" : v) },
      };
    }
    if (i === 1 && panels[0] === "macd") {
      return { scale: true, gridIndex: i, splitLine: { show: false }, axisLabel: { color: "#6b7a90" }, name: "MACD", nameTextStyle: { color: "#6b7a90", fontSize: 10 } };
    }
    if (i === 2) {
      return { scale: true, gridIndex: i, splitLine: { show: false }, axisLabel: { color: "#6b7a90" }, name: "MACD", nameTextStyle: { color: "#6b7a90", fontSize: 10 } };
    }
    return { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "rgba(107,122,144,0.08)" } }, axisLabel: { color: "#6b7a90" } };
  });

  const allXIdx = panels.length ? Array.from({ length: 1 + panels.length }, (_, i) => i) : 0;

  klineChart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      legend: { top: 8, textStyle: { color: "#6b7a90" }, selectedMode: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "rgba(255,255,255,0.95)",
        borderColor: "rgba(107,122,144,0.2)",
        textStyle: { color: "#1a2233", fontSize: 12 },
        formatter: (params) => {
          const i = params[0]?.dataIndex ?? 0;
          const b = bars[i];
          if (!b) return "";
          const lines = [
            `<div style="font-weight:600;margin-bottom:4px">${b.date}</div>`,
            `开 ${fmtNum(b.open, 3)}　收 <b>${fmtNum(b.close, 3)}</b>`,
            `高 ${fmtNum(b.high, 3)}　低 ${fmtNum(b.low, 3)}`,
          ];
          if (b.change_pct !== undefined && b.change_pct !== null) {
            const t = toneClass(b.change_pct);
            lines.push(`<span style="color:${t === "up" ? "#e03131" : t === "down" ? "#0ca678" : "#6b7a90"}">涨跌 ${fmtPct(b.change_pct)}</span>`);
          }
          if (b.rsi14 != null) lines.push(`RSI14 ${fmtNum(b.rsi14, 1)}`);
          if (b.macd_dif != null) lines.push(`MACD DIF ${fmtNum(b.macd_dif, 3)} DEA ${fmtNum(b.macd_dea, 3)} 柱 ${fmtNum(b.macd_hist, 3)}`);
          return lines.join("<br/>");
        },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      dataZoom: [
        { type: "inside", xAxisIndex: allXIdx },
        { type: "slider", xAxisIndex: allXIdx, bottom: 8, height: 16, borderColor: "rgba(107,122,144,0.2)", fillerColor: "rgba(47,111,237,0.12)", textStyle: { color: "#6b7a90" } },
      ],
      grid,
      xAxis,
      yAxis,
      series,
    },
    true
  );
  klineChart.hideLoading();
}

async function loadSignalsSide() {
  const trendBox = $("#trendBox");
  const signalList = $("#signalList");
  trendBox.innerHTML = `<div class="muted">加载中…</div>`;
  signalList.innerHTML = `<div class="muted">加载中…</div>`;
  try {
    const data = await api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}&market=${encodeURIComponent(state.market)}&days=${Math.max(state.days, 120)}`);
    const t = data.trend || {};
    const tone = t.tone === "up" ? "up" : t.tone === "down" ? "down" : "flat";
    trendBox.innerHTML = `
      <div class="trend-label ${tone}" style="background:${tone === "up" ? "rgba(224,49,49,0.12)" : tone === "down" ? "rgba(12,166,120,0.12)" : "rgba(107,122,144,0.12)"}">
        综合倾向：${escapeHtml(t.label || "—")}
      </div>
      <ul>${(t.points || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("") || "<li>暂无结论</li>"}</ul>
      <div class="muted" style="margin-top:10px">多头计分 ${t.buy_votes ?? 0} · 空头计分 ${t.sell_votes ?? 0}</div>
    `;
    const sigs = data.signals || [];
    state.signals = sigs;
    // 日线图在位且标的未变时，把信号点同步标到 K 线上
    if (state.mode === "daily" && state.history && data.code === state.history.code && data.market === state.history.market) {
      renderKline(state.history);
    }
    if (!sigs.length) {
      signalList.innerHTML = `<div class="empty">近期无经典交叉信号（属正常，信号较稀疏）</div>`;
    } else {
      signalList.innerHTML = sigs
        .map(
          (s) => `<div class="signal-item">
            <div class="date">${escapeHtml(s.date)}</div>
            <div>
              <div class="name">${escapeHtml(s.name)} <span class="muted">强度 ${escapeHtml(s.strength)}</span></div>
              <div class="detail">${escapeHtml(s.detail)} · 收盘 ${fmtNum(s.close, 3)}</div>
            </div>
            <div class="badge ${s.kind}">${s.kind === "buy" ? "偏多" : s.kind === "sell" ? "偏空" : "中性"}</div>
          </div>`
        )
        .join("");
    }
  } catch (e) {
    trendBox.innerHTML = `<div class="muted">${escapeHtml(e.message)}</div>`;
    signalList.innerHTML = `<div class="muted">信号加载失败</div>`;
  }
}

/* ---------- Fundamentals ---------- */
async function loadFundamentals() {
  const grid = $("#fundGrid");
  const tips = $("#fundTips");
  const token = beginRequest("fund");
  grid.innerHTML = `<div class="fund-item"><div class="k">加载中</div><div class="v">…</div></div>`;
  try {
    const data = await api(`/api/fundamentals?symbol=${encodeURIComponent(state.symbol)}&market=${encodeURIComponent(state.market)}`);
    if (!isCurrent("fund", token)) return; // 已被更新的请求取代
    setActiveSymbol(data.code, data.market, data.name);
    $("#fundName").textContent = `${data.name || ""} · ${data.code}.${data.market}`;
    const items = data.items || [];
    if (!items.length) {
      grid.innerHTML = `<div class="empty">暂无基本面数据</div>`;
    } else {
      grid.innerHTML = items
        .map(
          (it) => `<div class="fund-item">
            <div class="k">${escapeHtml(it.label)}</div>
            <div class="v">${escapeHtml(typeof it.value === "number" ? fmtNum(it.value, 3) : it.value)}</div>
            <div class="h">${escapeHtml(it.hint || "")}</div>
          </div>`
        )
        .join("");
    }

    const tipLines = buildFundTips(data);
    tips.innerHTML = tipLines.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  } catch (e) {
    if (!isCurrent("fund", token)) return;
    grid.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function buildFundTips(data) {
  const tips = [];
  const isEtf = data.type === "etf";
  if (isEtf) {
    tips.push("你当前看的是 ETF：更贴近一篮子资产的表现，费率通常低于主动基金，适合观察行业/指数趋势。");
    tips.push("ETF 重点看跟踪误差、流动性（成交额）和溢价率；市盈率对债券/货币类 ETF 参考意义有限。");
  } else {
    tips.push("市盈率（PE）反映“按当前利润多少年回本”，过高可能透支成长，过低也可能说明基本面弱。");
    tips.push("市净率（PB）适合重资产行业；轻资产成长股 PB 天然更高，需结合行业对比。");
    tips.push("换手率突然放大，往往伴随情绪或事件驱动，需结合价格位置看。");
  }
  tips.push("市值越大，通常波动相对温和；小市值弹性大，风险也更高。");
  tips.push("基本面是慢变量，买卖决策建议结合 K线信号与仓位管理，不要单指标重仓。");
  return tips;
}

/* ---------- Compare / Watchlist ---------- */
let compareChart = null;

async function loadCompare() {
  try {
    const data = await api("/api/portfolio");
    state.portfolio = data;
    renderWatch(data.watchlist || []);
    await renderCompareChart(data.watchlist || []);
  } catch (e) {
    toast("自选加载失败：" + e.message);
  }
}

function renderWatch(list) {
  const box = $("#watchList");
  if (!list.length) {
    box.innerHTML = `<div class="empty">暂无自选，添加代码后可对比走势</div>`;
    return;
  }
  box.innerHTML = list
    .map(
      (w) => `<div class="watch-item" data-code="${escapeHtml(w.code)}" data-market="${escapeHtml(w.market)}" data-name="${escapeHtml(w.name || w.code)}">
        <div class="meta">
          <strong>${escapeHtml(w.name || w.code)}</strong>
          <span class="muted">${escapeHtml(fmtSymbol(w.code, w.market, w.full))} · ${w.type === "etf" ? "ETF" : "股票"}</span>
        </div>
        <div style="text-align:right">
          <div>${fmtNum(w.price, 3)}</div>
          <div class="${toneClass(w.change_pct)}">${fmtPct(w.change_pct)}</div>
        </div>
        <div class="actions">
          <button class="btn ghost btn-open">查看</button>
          <button class="btn danger ghost btn-unwatch">移除</button>
        </div>
      </div>`
    )
    .join("");

  box.querySelectorAll(".watch-item").forEach((el) => {
    el.querySelector(".btn-open").addEventListener("click", () => {
      selectSymbol(el.dataset.code, el.dataset.market, el.dataset.name);
      $$(".nav-item").forEach((b) => b.classList.remove("active"));
      $('.nav-item[data-view="chart"]').classList.add("active");
      $$(".view").forEach((v) => v.classList.remove("active"));
      $("#view-chart").classList.add("active");
      loadChart();
    });
    el.querySelector(".btn-unwatch").addEventListener("click", async () => {
      try {
        const res = await fetch(
          `${API}/api/portfolio/watch?code=${encodeURIComponent(el.dataset.code)}&market=${encodeURIComponent(el.dataset.market)}`,
          { method: "DELETE" }
        );
        if (!res.ok) throw new Error("移除失败");
        state.portfolio = await res.json();
        renderWatch(state.portfolio.watchlist || []);
        renderCompareChart(state.portfolio.watchlist || []);
      } catch (e) {
        toast(e.message);
      }
    });
  });
}

async function renderCompareChart(list) {
  const el = $("#compareChart");
  if (!compareChart) compareChart = echarts.init(el);
  if (!list.length) {
    compareChart.clear();
    compareChart.setOption({
      backgroundColor: "transparent",
      title: { text: "添加自选后显示对比", left: "center", top: "middle", textStyle: { color: "#6b7a90", fontSize: 14 } },
    });
    return;
  }

  compareChart.showLoading({ text: "加载中", color: "#2f6fed", maskColor: "rgba(244,246,251,0.5)" });
  try {
    const series = [];
    let dates = null;
    const colors = ["#2f6fed", "#e03131", "#f5c542", "#38bdf8", "#c084fc", "#0ca678"];
    for (let i = 0; i < list.length; i++) {
      const w = list[i];
      const hist = await api(`/api/history?symbol=${encodeURIComponent(w.code)}&market=${encodeURIComponent(w.market)}&days=250`);
      const bars = hist.bars || [];
      if (!bars.length) continue;
      if (!dates) dates = bars.map((b) => b.date);
      const base = bars.find((b) => b.close > 0)?.close || 1;
      series.push({
        name: hist.name || w.code,
        type: "line",
        showSymbol: false,
        smooth: true,
        data: bars.map((b) => Number(((b.close / base) * 100).toFixed(2))),
        lineStyle: { width: 2, color: colors[i % colors.length] },
        itemStyle: { color: colors[i % colors.length] },
      });
    }
    compareChart.setOption(
      {
        backgroundColor: "transparent",
        tooltip: { trigger: "axis", backgroundColor: "rgba(255,255,255,0.95)", textStyle: { color: "#1a2233" } },
        legend: { top: 8, textStyle: { color: "#6b7a90" } },
        grid: { left: 48, right: 16, top: 40, bottom: 40 },
        xAxis: { type: "category", data: dates || [], axisLabel: { color: "#6b7a90" } },
        yAxis: { scale: true, splitLine: { lineStyle: { color: "rgba(107,122,144,0.08)" } }, axisLabel: { color: "#6b7a90" } },
        series,
      },
      true
    );
  } catch (e) {
    toast("对比图失败：" + e.message);
  } finally {
    compareChart.hideLoading();
  }
}

$("#btnAddWatch").addEventListener("click", async () => {
  const code = $("#watchInput").value.trim();
  const market = $("#watchMarket").value.trim().toUpperCase() || "";
  if (!code) return toast("请输入代码");
  try {
    const res = await fetch(`${API}/api/portfolio/watch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, market }),
    });
    if (!res.ok) throw new Error("添加失败");
    state.portfolio = await res.json();
    $("#watchInput").value = "";
    renderWatch(state.portfolio.watchlist || []);
    renderCompareChart(state.portfolio.watchlist || []);
    toast("已加入自选");
  } catch (e) {
    toast(e.message);
  }
});

/* ---------- Reference (稳健参考) ---------- */
async function loadReference() {
  const box = $("#refGroups");
  box.innerHTML = `<div class="empty">加载参考清单…</div>`;
  try {
    const data = await api("/api/reference");
    const groups = data.groups || [];
    box.innerHTML = groups
      .map((g) => {
        const cards = (g.items || [])
          .map((it) => {
            const name = it.live_name || it.name;
            return `<div class="ref-card" data-code="${escapeHtml(it.code)}" data-market="${escapeHtml(it.market)}" data-name="${escapeHtml(name)}">
              <div class="top">
                <div>
                  <div class="nm">${escapeHtml(name)}</div>
                  <div class="cd">${escapeHtml(it.full)} · ${it.type === "etf" ? "ETF" : "股票"}</div>
                </div>
                ${it.tag ? `<span class="tag">${escapeHtml(it.tag)}</span>` : ""}
              </div>
              <div>
                <div class="px">${fmtNum(it.price, 3)}</div>
                <div class="${toneClass(it.change_pct)}">${fmtPct(it.change_pct)}</div>
              </div>
              <div class="acts">
                <button class="btn ghost btn-view">看K线</button>
                <button class="btn btn-watch">加自选</button>
              </div>
            </div>`;
          })
          .join("");
        return `<div class="ref-group">
          <div class="ref-group-title">
            <h3>${escapeHtml(g.title)}</h3>
            <span class="ref-risk">风险：${escapeHtml(g.risk)}</span>
          </div>
          <p class="ref-blurb">${escapeHtml(g.blurb)}</p>
          <div class="ref-grid">${cards}</div>
        </div>`;
      })
      .join("");

    box.querySelectorAll(".ref-card").forEach((card) => {
      const code = card.dataset.code;
      const market = card.dataset.market;
      const name = card.dataset.name;
      card.querySelector(".btn-view").addEventListener("click", () => {
        selectSymbol(code, market, name);
        $$(".nav-item").forEach((b) => b.classList.remove("active"));
        $('.nav-item[data-view="chart"]').classList.add("active");
        $$(".view").forEach((v) => v.classList.remove("active"));
        $("#view-chart").classList.add("active");
        loadChart();
      });
      card.querySelector(".btn-watch").addEventListener("click", async () => {
        try {
          const res = await fetch(`${API}/api/portfolio/watch`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code, market }),
          });
          if (!res.ok) throw new Error("添加失败");
          state.portfolio = await res.json();
          toast(`${name} 已加入自选`);
        } catch (e) {
          toast(e.message);
        }
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

/* ---------- Live alerts ---------- */
let lastAlertTs = 0;
let notifySeeded = false; // 首次拉取只登记基线，不推送历史
let liveTimer = null;
let notifiedIds = new Set();

function notifyBrowser(title, body) {
  if (!("Notification" in window)) return false;
  if (Notification.permission !== "granted") return false;
  try {
    const n = new Notification(title, {
      body: body || "",
      tag: title,
      requireInteraction: false,
    });
    n.onclick = () => {
      window.focus();
      n.close();
    };
    return true;
  } catch (_) {
    return false;
  }
}

function showInPageAlert(title, body, kind = "buy") {
  const old = document.getElementById("inpageAlert");
  if (old) old.remove();
  const el = document.createElement("div");
  el.id = "inpageAlert";
  el.className = "inpage-alert " + (kind || "");
  el.innerHTML = `<div class="t">${escapeHtml(title)}</div>
    <div class="b">${escapeHtml(body || "")}</div>
    <button class="x" type="button">知道了</button>`;
  el.querySelector(".x").addEventListener("click", () => el.remove());
  document.body.appendChild(el);
  setTimeout(() => {
    if (el.isConnected) el.remove();
  }, 12000);
}

function notifyAny(title, body, kind) {
  const ok = notifyBrowser(title, body);
  // 无系统权限时页内强提示，保证开市仍能看到
  if (!ok) showInPageAlert(title, body, kind);
  else showInPageAlert(title, body, kind); // 双通道：页内也留一条，便于挂机页可见
}

function notifyHelpText() {
  return [
    "Edge 开启系统通知步骤：",
    "1. 地址栏左侧锁形/调节图标 →「网站权限」→「通知」→ 允许",
    "2. 或打开 edge://settings/content/notifications ，允许本机站点",
    "3. Windows 设置 → 系统 → 通知，打开并允许 Microsoft Edge",
    "4. 关闭「专注助手/勿扰」，避免拦截右下角横幅",
    "5. 改完后刷新本页，再点一次「开启系统通知」",
    "若权限显示为“已拒绝”，浏览器不会再次弹窗，必须到上述设置里手动改回“允许”。",
  ].join("\n");
}

function updateNotifyStatus() {
  const el = $("#notifyStatus");
  const btn = $("#btnNotify");
  if (!el) return;
  if (!("Notification" in window)) {
    el.textContent = "当前浏览器不支持系统通知，将使用页内提醒。";
    el.className = "notify-status muted";
    return;
  }
  const p = Notification.permission;
  if (p === "granted") {
    el.textContent = "系统通知：已授权。保持本页打开即可收到横幅。";
    el.className = "notify-status ok";
    if (btn) btn.textContent = "系统通知已开启";
  } else if (p === "denied") {
    el.textContent = "系统通知：已被拒绝。请按「通知权限说明」在 Edge 里改回允许，再刷新本页。页内提醒仍可用。";
    el.className = "notify-status warn";
    if (btn) btn.textContent = "去设置里允许通知";
  } else {
    el.textContent = "系统通知：尚未授权。点击「开启系统通知」，若无弹窗请点「通知权限说明」。";
    el.className = "notify-status muted";
    if (btn) btn.textContent = "开启系统通知";
  }
}

async function requestNotifyPermission() {
  if (!("Notification" in window)) {
    toast("当前浏览器不支持系统通知，将使用页内提醒");
    updateNotifyStatus();
    return;
  }
  // 已拒绝时 requestPermission 不会再弹窗
  if (Notification.permission === "denied") {
    toast("权限已被拒绝，请在 Edge 网站设置里允许通知");
    alert(notifyHelpText());
    updateNotifyStatus();
    return;
  }
  try {
    const p = await Notification.requestPermission();
    if (p === "granted") {
      toast("系统通知已开启（请保持本页打开）");
      try {
        notifyBrowser("通知已开启", "开市信号与价格线提醒将推送到系统通知");
      } catch (_) {}
    } else if (p === "denied") {
      toast("通知权限被拒绝，已改用页内提醒");
      alert(notifyHelpText());
    } else {
      toast("未选择允许；可再点一次，或查看权限说明");
    }
  } catch (e) {
    toast("请求通知权限失败：" + e.message);
  }
  updateNotifyStatus();
}

$("#btnNotify")?.addEventListener("click", requestNotifyPermission);
$("#btnNotifyHelp")?.addEventListener("click", () => {
  alert(notifyHelpText());
  updateNotifyStatus();
});

$("#btnScanNow")?.addEventListener("click", async () => {
  try {
    toast("扫描中…");
    const res = await fetch(`${API}/api/monitor/scan`, { method: "POST" });
    if (!res.ok) throw new Error("扫描失败");
    await loadLive();
    toast("扫描完成");
  } catch (e) {
    toast(e.message);
  }
});

$("#btnToggleMon")?.addEventListener("click", async () => {
  try {
    const st = await api("/api/monitor");
    const url = st.running ? "/api/monitor/stop" : "/api/monitor/start";
    const res = await fetch(API + url, { method: "POST" });
    if (!res.ok) throw new Error("操作失败");
    await loadLive();
    toast(st.running ? "监控已暂停" : "监控已启动");
  } catch (e) {
    toast(e.message);
  }
});

$("#btnClearAlerts")?.addEventListener("click", async () => {
  try {
    await fetch(`${API}/api/alerts`, { method: "DELETE" });
    await loadLive();
    toast("提醒已清空");
  } catch (e) {
    toast(e.message);
  }
});

function scoreColor(score) {
  const s = Number(score) || 50;
  if (s >= 72) return "var(--up)";
  if (s >= 58) return "#e8590c";
  if (s > 42) return "var(--muted)";
  if (s > 30) return "#0b7285";
  return "var(--down)";
}

function renderAlerts(alerts, clock, monitor) {
  const live = clock?.is_trading && monitor?.running;
  $("#liveStats").innerHTML = `
    <div class="stat-card">
      <div class="label">市场状态</div>
      <div class="value" style="font-size:20px">
        <span class="live-dot ${live ? "" : "off"}"></span>${escapeHtml(clock?.detail || "—")}
      </div>
      <div class="sub">${escapeHtml((clock?.now || "").replace("T", " ").slice(0, 19))}</div>
    </div>
    <div class="stat-card">
      <div class="label">监控</div>
      <div class="value" style="font-size:20px">${monitor?.running ? "运行中" : "已停止"}</div>
      <div class="sub">间隔约 ${monitor?.interval_sec || 45}s · 扫描 ${monitor?.scans || 0} 次</div>
    </div>
    <div class="stat-card">
      <div class="label">监控标的</div>
      <div class="value">${(monitor?.watch_codes || []).length}</div>
      <div class="sub">持仓 + 自选</div>
    </div>
    <div class="stat-card">
      <div class="label">提醒列表</div>
      <div class="value">${alerts.length}</div>
      <div class="sub">历史 ${monitor?.restored_count ?? 0}</div>
    </div>
    <div class="stat-card">
      <div class="label">本次推送</div>
      <div class="value">${monitor?.session_pushed ?? monitor?.alerts_emitted ?? 0}</div>
      <div class="sub">累计 ${monitor?.total_pushed ?? 0}</div>
    </div>
  `;

  const btn = $("#btnToggleMon");
  if (btn) btn.textContent = monitor?.running ? "暂停监控" : "启动监控";

  const box = $("#alertList");
  if (!alerts.length) {
    box.innerHTML = `<div class="empty">暂无提醒。开市时段或点「立即扫描」后，若触发阈值会出现在这里。</div>`;
  } else {
    box.innerHTML = alerts
      .map(
        (a) => `<div class="alert-item ${escapeHtml(a.kind || "")}">
          <div class="hd">
            <div class="ttl">${escapeHtml(a.title || a.name)}</div>
            <div class="tm">${escapeHtml(a.time_str || "")}</div>
          </div>
          <div class="bd">${escapeHtml(a.body || a.advice || "")}</div>
          <div class="sc">
            <span class="pill">分 ${fmtNum(a.score, 0)}</span>
            ${a.price != null ? `<span class="pill">价 ${fmtNum(a.price, 3)}</span>` : ""}
            ${a.stop_hint != null ? `<span class="pill">止损观察 ${fmtNum(a.stop_hint, 3)}</span>` : ""}
            ${a.target_hint != null ? `<span class="pill">目标观察 ${fmtNum(a.target_hint, 3)}</span>` : ""}
          </div>
        </div>`
      )
      .join("");
  }

  // 仅推送：开市中 + 本次会话基线之后的新提醒；历史只展示不弹
  const nowTs = (clock?.now ? Date.parse(clock.now) / 1000 : Date.now() / 1000);
  const newestTs = alerts.reduce((m, a) => Math.max(m, Number(a.ts) || 0), 0);
  if (!notifySeeded) {
    lastAlertTs = newestTs || lastAlertTs;
    notifySeeded = true;
    for (const a of alerts) notifiedIds.add(a.id || a.ts);
  } else if (clock?.is_trading) {
    for (const a of alerts) {
      const id = a.id || a.ts;
      const ts = Number(a.ts) || 0;
      if (!id || notifiedIds.has(id)) continue;
      if (ts <= lastAlertTs) continue;
      // 防时钟偏差：太旧的不推
      if (nowTs - ts > 300) continue;
      if (a.kind && a.kind !== "info") {
        notifyAny(a.title || "行情提醒", (a.body || a.advice || "").slice(0, 160), a.kind);
      }
      notifiedIds.add(id);
    }
  } else {
    // 休市：只记录，不推送
    for (const a of alerts) notifiedIds.add(a.id || a.ts);
  }
  if (newestTs) lastAlertTs = Math.max(lastAlertTs, newestTs);
}

async function loadScoreBoard() {
  const box = $("#scoreBoard");
  box.innerHTML = `<div class="empty">计算中…</div>`;
  try {
    const data = await api("/api/signals/pro");
    const items = (data.items || []).filter((x) => x.ok);
    if (!items.length) {
      box.innerHTML = `<div class="empty">无标的，请先添加持仓或自选</div>`;
      return;
    }
    box.innerHTML = items
      .map((it) => {
        const pct = Math.max(0, Math.min(100, Number(it.score) || 50));
        const color = scoreColor(it.score);
        return `<div class="score-row" data-code="${escapeHtml(it.code)}" data-market="${escapeHtml(it.market)}" data-name="${escapeHtml(it.name)}">
          <div>
            <div class="nm">${escapeHtml(it.name)}</div>
            <div class="sub">${escapeHtml(it.action)} · ${escapeHtml(it.full || "")}${it.pos_note ? " · " + escapeHtml(it.pos_note) : ""}</div>
          </div>
          <div class="score-bar"><i style="width:${pct}%;background:${color}"></i></div>
          <div class="score-num" style="color:${color}">${fmtNum(it.score, 0)}</div>
        </div>`;
      })
      .join("");
    box.querySelectorAll(".score-row").forEach((row) => {
      row.addEventListener("click", () => {
        selectSymbol(row.dataset.code, row.dataset.market, row.dataset.name);
        $$(".nav-item").forEach((b) => b.classList.remove("active"));
        $('.nav-item[data-view="chart"]').classList.add("active");
        $$(".view").forEach((v) => v.classList.remove("active"));
        $("#view-chart").classList.add("active");
        loadChart();
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

async function loadLive() {
  updateNotifyStatus();
  try {
    const data = await api("/api/alerts");
    renderAlerts(data.alerts || [], data.clock, data.monitor);
  } catch (e) {
    $("#liveStats").innerHTML = `<div class="stat-card"><div class="label">错误</div><div class="value" style="font-size:14px">${escapeHtml(e.message)}</div></div>`;
  }
  loadScoreBoard();
}

function startLivePolling() {
  if (liveTimer) return;
  const tick = async () => {
    const active = $(".nav-item.active")?.dataset.view;
    try {
      const data = await api("/api/alerts");
      const trading = !!data.clock?.is_trading;
      if (active === "live") {
        renderAlerts(data.alerts || [], data.clock, data.monitor);
      } else if ((data.alerts || []).length) {
        const newest = data.alerts[0];
        const kind = newest?.kind || "";
        const important =
          kind.includes("strong") || kind === "price_break" || kind === "price_target";
        const ts = Number(newest?.ts) || 0;
        // 休市或历史消息一律不推
        if (!notifySeeded) {
          lastAlertTs = (data.alerts || []).reduce((m, a) => Math.max(m, Number(a.ts) || 0), 0) || lastAlertTs;
          notifySeeded = true;
          for (const a of data.alerts || []) notifiedIds.add(a.id || a.ts);
        } else if (trading && newest && important && ts > lastAlertTs) {
          notifyAny(newest.title, (newest.body || "").slice(0, 160), newest.kind);
          lastAlertTs = ts;
          notifiedIds.add(newest.id || newest.ts);
        } else if (!trading && data.alerts?.length) {
          lastAlertTs = Math.max(lastAlertTs, ...data.alerts.map((a) => Number(a.ts) || 0));
        }
      }
      const next = trading ? 10000 : 30000;
      if (tick._ms !== next) {
        tick._ms = next;
        clearInterval(liveTimer);
        liveTimer = setInterval(tick, next);
      }
    } catch (_) {}
  };
  liveTimer = setInterval(tick, 10000);
}

/* ---------- Advice cards ---------- */
async function loadAdvice() {
  const box = $("#adviceCards");
  box.innerHTML = `<div class="empty">计算中…</div>`;
  const useLLM = !!$("#chkLLM")?.checked;
  try {
    const data = await api(`/api/advice?use_llm=${useLLM ? "true" : "false"}`);
    const items = (data.items || []).filter((x) => x.ok);
    if (!items.length) {
      box.innerHTML = `<div class="empty">暂无标的，请先添加持仓或自选</div>`;
      return;
    }
    box.innerHTML = items.map((it) => {
      const tone = it.tone || "neutral";
      const conf = it.confidence ?? 50;
      const res = it.resonance || {};
      const scen = (it.scenarios || []).map((s) => `<li><b>${escapeHtml(s.if)}</b> → ${escapeHtml(s.then)}</li>`).join("");
      const pts = (it.explain_points || []).slice(0, 4).map((p) => `<li>${escapeHtml(p)}</li>`).join("");
      return `<div class="advice-card tone-${escapeHtml(tone)}" data-code="${escapeHtml(it.code)}" data-market="${escapeHtml(it.market)}" data-name="${escapeHtml(it.name)}">
        <div class="hd">
          <div>
            <div class="nm">${escapeHtml(it.name)}</div>
            <div class="meta">${escapeHtml(it.full || "")} · 分 ${fmtNum(it.score, 0)} · 置信 ${conf}%</div>
          </div>
          <div class="px ${toneClass(it.change_pct)}">${fmtNum(it.price, 3)}</div>
        </div>
        <div class="act">${escapeHtml(it.action || "")}</div>
        <div class="meta">${escapeHtml(res.resonance || "")}｜周${fmtNum(res.weekly_vote ?? 0, 1)} 日${fmtNum(res.daily_vote ?? 0, 1)}</div>
        <div class="meta">${escapeHtml(it.pos_hint || "")}</div>
        ${pts ? `<ul>${pts}</ul>` : ""}
        ${scen ? `<ul>${scen}</ul>` : ""}
        ${it.llm_text ? `<div class="llm"><b>本地模型：</b>${escapeHtml(it.llm_text)}</div>` : ""}
      </div>`;
    }).join("");

    box.querySelectorAll(".advice-card").forEach((card) => {
      card.style.cursor = "pointer";
      card.addEventListener("click", () => {
        selectSymbol(card.dataset.code, card.dataset.market, card.dataset.name);
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

$("#btnAdviceRefresh")?.addEventListener("click", loadAdvice);
$("#chkLLM")?.addEventListener("change", loadAdvice);

$("#btnBacktest")?.addEventListener("click", async () => {
  const box = $("#backtestBox");
  box.textContent = "回测中（约数秒）…";
  try {
    const d = await api(`/api/backtest?symbol=${encodeURIComponent(state.symbol)}&market=${encodeURIComponent(state.market)}&days=500&hold_days=5`);
    if (!d.ok) {
      box.textContent = d.error || "回测失败";
      return;
    }
    const rec = d.recommended || {};
    const rows = (d.rows || []).map((r) => {
      if (!r.n) return `<tr><td>${r.side === "buy" ? "≥" : "≤"}${r.level}</td><td>0</td><td>—</td><td>—</td></tr>`;
      return `<tr>
        <td>${r.side === "buy" ? "≥" : "≤"}${r.level}</td>
        <td>${r.n}</td>
        <td>${fmtPct((r.win_rate || 0) * 100)}</td>
        <td>${fmtPct((r.avg_ret || 0) * 100)}</td>
      </tr>`;
    }).join("");
    box.innerHTML = `
      <div>${escapeHtml(d.code)}.${escapeHtml(d.market)} · 近 ${d.bars} 根日K · 看后 ${d.hold_days} 日涨跌</div>
      <div>基线（持有后${d.hold_days}日平均）：${d.baseline?.avg_fwd_ret != null ? fmtPct(d.baseline.avg_fwd_ret * 100) : "—"} · 样本 ${d.baseline?.n ?? 0}</div>
      <div>建议更严阈值：买入≥ <b>${rec.buy_score}</b>（历史胜率 ${rec.buy_win_rate != null ? fmtPct(rec.buy_win_rate * 100) : "—"}，n=${rec.buy_n}）
        ｜卖出≤ <b>${rec.sell_score}</b>（历史“随后下跌”率 ${rec.sell_win_rate != null ? fmtPct(rec.sell_win_rate * 100) : "—"}，n=${rec.sell_n}）</div>
      <table>
        <thead><tr><th>阈值</th><th>次数</th><th>胜率*</th><th>平均收益</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="muted">*买入侧胜率=后N日上涨占比；卖出侧胜率=后N日下跌占比。样本外可能失效，非投资建议。</div>
    `;
  } catch (e) {
    box.textContent = "回测失败：" + e.message;
  }
});

/* ---------- Boot ---------- */
window.addEventListener("resize", () => {
  klineChart?.resize();
  compareChart?.resize();
  const alloc = echarts.getInstanceByDom($("#allocChart"));
  alloc?.resize();
});

async function boot() {
  setActiveSymbol("159516", "SZ", "159516.SZ");
  await loadPortfolio();
  loadSignalsSide();
  updateNotifyStatus();
  startLivePolling();
}

boot();
