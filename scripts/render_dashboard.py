"""
把 signals.json + 原始 K 线数据渲染为单文件 HTML 仪表盘。

特点:
    - 单文件输出(内嵌 JS 数据 + ECharts via CDN)
    - A 股配色:红涨绿跌
    - 每只股票一张卡片,含 K 线 + 均线 + 布林带 + 成交量
    - AI 综合分析区块保留占位,便于后续填入
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime


# ============================== 辅助计算 ==============================
def rolling_mean(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in window):
                out.append(None)
            else:
                out.append(sum(window) / period)
    return out


def rolling_std(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            m = sum(window) / period
            var = sum((v - m) ** 2 for v in window) / period
            out.append(var ** 0.5)
    return out


def bollinger_bands(closes: list[float], period: int = 20, std: float = 2.0):
    mid = rolling_mean(closes, period)
    sd = rolling_std(closes, period)
    upper = [m + std * s if (m is not None and s is not None) else None for m, s in zip(mid, sd)]
    lower = [m - std * s if (m is not None and s is not None) else None for m, s in zip(mid, sd)]
    return lower, mid, upper


# ============================== 决策归一化 ==============================
def resolve_decision(r: dict) -> tuple[str, str]:
    """返回 (决策文本, CSS class)"""
    if "exit_analysis" in r:
        action = r["exit_analysis"]["action"]
        if action == "TAKE_PROFIT":
            return "全部止盈", "tag-tp"
        if action == "TAKE_PROFIT_PARTIAL":
            return "部分止盈", "tag-tp-partial"
        if action == "STOP_LOSS":
            return "止损", "tag-sl"
        if action == "HOLD":
            return "持有", "tag-hold"
    d = r["dip_analysis"]
    if d["triggered"]:
        if d["hit_count"] >= 5:
            return "强烈抄底候选", "tag-dip-strong"
        return "抄底候选", "tag-dip"
    return "观望", "tag-watch"


# ============================== 数据构造 ==============================
def build_chart_payload(kline: list[dict], lookback: int = 30) -> dict:
    """从日线构造前端图表所需数据。"""
    recent = kline[-lookback:] if len(kline) > lookback else kline
    dates = [k["date"] for k in recent]
    opens = [k["open"] for k in recent]
    closes = [k["close"] for k in recent]
    highs = [k["high"] for k in recent]
    lows = [k["low"] for k in recent]
    volumes = [k["volume"] for k in recent]

    # ECharts candlestick data: [open, close, low, high]
    ohlc = [[o, c, l, h] for o, c, l, h in zip(opens, closes, lows, highs)]

    # 用完整的 K 线计算均线和布林带,只取最近 lookback 个
    full_closes = [k["close"] for k in kline]
    ma5_full = rolling_mean(full_closes, 5)
    ma20_full = rolling_mean(full_closes, 20)
    ma60_full = rolling_mean(full_closes, 60)
    bb_lo, bb_mid, bb_hi = bollinger_bands(full_closes, 20, 2.0)

    tail_slice = slice(-len(recent), None)
    return {
        "dates": dates,
        "ohlc": ohlc,
        "volumes": volumes,
        "ma5": ma5_full[tail_slice],
        "ma20": ma20_full[tail_slice],
        "ma60": ma60_full[tail_slice],
        "bb_upper": bb_hi[tail_slice],
        "bb_lower": bb_lo[tail_slice],
        "bb_mid": bb_mid[tail_slice],
    }


def load_kline(data_dir: str, code: str) -> list[dict]:
    path = os.path.join(data_dir, f"{code}.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("kline", [])


def build_dashboard_data(signals_path: str) -> dict:
    with open(signals_path, "r", encoding="utf-8") as f:
        sig = json.load(f)

    data_dir = os.path.dirname(os.path.abspath(signals_path))
    date = sig["date"]
    stocks = []
    dip_count = tp_count = sl_count = hold_count = 0

    for r in sig["results"]:
        code = r["code"]
        kline = load_kline(data_dir, code)
        chart = build_chart_payload(kline) if kline else None
        decision, dec_class = resolve_decision(r)
        if dec_class in ("tag-dip", "tag-dip-strong"):
            dip_count += 1
        elif dec_class in ("tag-tp", "tag-tp-partial"):
            tp_count += 1
        elif dec_class == "tag-sl":
            sl_count += 1
        elif dec_class == "tag-hold":
            hold_count += 1

        stocks.append({
            "code": code,
            "name": r["name"],
            "quote": r.get("quote", {}),
            "dip_analysis": r["dip_analysis"],
            "exit_analysis": r.get("exit_analysis"),
            "position": r.get("position"),
            "money_flow_5d": r.get("money_flow_5d", []),
            "chart": chart,
            "decision": decision,
            "decision_class": dec_class,
        })

    return {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total": len(stocks),
            "dip_candidates": dip_count,
            "take_profit": tp_count,
            "stop_loss": sl_count,
            "hold": hold_count,
        },
        "stocks": stocks,
    }


# ============================== HTML 模板 ==============================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股每日分析 · __DATE__</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --up: #d9322d;      /* A 股红 = 涨 */
    --down: #0a9a5a;    /* A 股绿 = 跌 */
    --bg: #f7f8fa;
    --card: #ffffff;
    --border: #e4e7eb;
    --text: #1f2329;
    --text-sec: #6b7280;
    --accent: #4c6ef5;
    --warn: #f59e0b;
    --danger: #ef4444;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 24px 16px 64px;
  }
  .container { max-width: 1400px; margin: 0 auto; }
  header {
    background: linear-gradient(135deg, #1e3a8a, #4c6ef5);
    color: white;
    padding: 24px 28px;
    border-radius: 12px;
    margin-bottom: 20px;
  }
  header h1 { font-size: 24px; font-weight: 600; }
  header .date { font-size: 14px; opacity: 0.85; margin-top: 4px; }
  header .disclaimer {
    margin-top: 12px;
    font-size: 12px;
    opacity: 0.8;
    padding: 10px 12px;
    background: rgba(255,255,255,0.1);
    border-left: 3px solid #fff;
    border-radius: 4px;
    line-height: 1.6;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
  }
  .stat .label { font-size: 12px; color: var(--text-sec); }
  .stat .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
  .stat.dip .value { color: var(--accent); }
  .stat.tp .value { color: var(--warn); }
  .stat.sl .value { color: var(--danger); }

  section {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 20px;
  }
  section > h2 {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 14px;
    color: var(--text);
    border-left: 3px solid var(--accent);
    padding-left: 10px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  th {
    background: #f9fafb;
    font-weight: 500;
    color: var(--text-sec);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  tr:hover td { background: #fafbfc; }
  td.code { font-family: "SF Mono", Menlo, monospace; font-weight: 600; }
  .price-up { color: var(--up); font-weight: 500; }
  .price-down { color: var(--down); font-weight: 500; }
  .price-flat { color: var(--text-sec); }

  .tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
  }
  .tag-dip       { background: #e0e7ff; color: #3730a3; }
  .tag-dip-strong{ background: #4c6ef5; color: white; }
  .tag-watch     { background: #f3f4f6; color: #6b7280; }
  .tag-hold      { background: #fef3c7; color: #92400e; }
  .tag-tp        { background: #fed7aa; color: #9a3412; }
  .tag-tp-partial{ background: #fef3c7; color: #9a3412; }
  .tag-sl        { background: #fee2e2; color: #991b1b; font-weight: 600; }

  .stock-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(560px, 1fr));
    gap: 16px;
  }
  @media (max-width: 720px) {
    .stock-grid { grid-template-columns: 1fr; }
  }
  .stock-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
    transition: box-shadow 0.15s;
  }
  .stock-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .stock-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 12px;
  }
  .stock-head .code {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-sec);
  }
  .stock-head .name { font-size: 18px; font-weight: 600; }
  .stock-head .price { font-size: 20px; font-weight: 600; margin-left: auto; }
  .stock-head .change { font-size: 14px; font-weight: 500; }

  .chart {
    width: 100%;
    height: 320px;
    margin: 8px 0;
  }
  .indicators {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 8px;
    padding: 10px;
    background: #f9fafb;
    border-radius: 6px;
    margin: 10px 0;
    font-size: 12px;
  }
  .indicators div span.k { color: var(--text-sec); }
  .indicators div span.v { font-weight: 500; margin-left: 4px; }

  .signals-list { margin: 8px 0; }
  .signals-list .title { font-size: 13px; font-weight: 500; margin-bottom: 6px; }
  .signals-list ul { list-style: none; }
  .signals-list li {
    font-size: 12px;
    padding: 4px 0 4px 22px;
    position: relative;
    color: var(--text);
  }
  .signals-list li::before {
    content: "✓";
    position: absolute;
    left: 4px;
    color: var(--accent);
    font-weight: 600;
  }
  .signals-list.exit li::before { content: "⚠"; color: var(--warn); }

  .position-bar {
    background: #fff7ed;
    border-left: 3px solid var(--warn);
    padding: 10px 12px;
    margin: 10px 0;
    border-radius: 4px;
    font-size: 13px;
  }
  .position-bar.loss {
    background: #fef2f2;
    border-left-color: var(--danger);
  }

  .ai-block {
    margin-top: 14px;
    padding: 12px 14px;
    background: #f5f3ff;
    border: 1px dashed #c4b5fd;
    border-radius: 6px;
    font-size: 13px;
    color: #6d28d9;
  }
  .ai-block .placeholder { color: #8b5cf6; font-style: italic; }
  .ai-block.filled {
    background: #faf5ff;
    color: var(--text);
    border-style: solid;
    border-color: #ddd6fe;
  }

  footer {
    margin-top: 32px;
    padding: 16px;
    text-align: center;
    font-size: 12px;
    color: var(--text-sec);
    line-height: 1.8;
  }
  footer .disclaimer {
    max-width: 700px;
    margin: 0 auto;
    padding: 12px;
    background: #fef2f2;
    color: #7f1d1d;
    border-radius: 6px;
    border: 1px solid #fecaca;
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>📈 A股每日分析仪表盘</h1>
    <div class="date">交易日:<span id="header-date">__DATE__</span> · 生成于 <span id="header-gen">__GEN__</span></div>
    <div class="disclaimer">
      <strong>免责声明:</strong> 本报告由自动化工具 + AI 辅助生成,仅供学习研究参考,<strong>不构成投资建议</strong>。
      "抄底"是高风险策略,信号命中不保证盈利,20% 止盈只是一种常见纪律而非铁律。股市有风险,决策请独立判断并自负盈亏。
    </div>
  </header>

  <div class="stats" id="stats"></div>

  <section>
    <h2>📋 决策汇总</h2>
    <table id="summary-table">
      <thead>
        <tr>
          <th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th>
          <th>抄底信号</th><th>浮动盈亏</th><th>决策</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>🔍 个股详情</h2>
    <div class="stock-grid" id="stock-grid"></div>
  </section>

  <footer>
    <div class="disclaimer">
      ⚠ 本工具仅做数据采集与信号识别,不提供投资建议。请结合自身风险承受能力独立决策。
    </div>
    <div style="margin-top:12px">astock-daily-analysis · routine v1.1 · 数据源 akshare</div>
  </footer>

</div>

<script>
const DATA = __DATA_JSON__;

// ====== 渲染统计卡 ======
function renderStats() {
  const s = DATA.summary;
  const html = `
    <div class="stat"><div class="label">关注股票</div><div class="value">${s.total}</div></div>
    <div class="stat dip"><div class="label">抄底候选</div><div class="value">${s.dip_candidates}</div></div>
    <div class="stat tp"><div class="label">止盈触发</div><div class="value">${s.take_profit}</div></div>
    <div class="stat sl"><div class="label">止损触发</div><div class="value">${s.stop_loss}</div></div>
    <div class="stat"><div class="label">持仓持有</div><div class="value">${s.hold}</div></div>
  `;
  document.getElementById('stats').innerHTML = html;
}

// ====== 工具函数 ======
function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '-';
  const n = parseFloat(v);
  const cls = n > 0 ? 'price-up' : (n < 0 ? 'price-down' : 'price-flat');
  const sign = n > 0 ? '+' : '';
  return `<span class="${cls}">${sign}${n.toFixed(2)}%</span>`;
}
function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '') return '-';
  const n = parseFloat(v);
  if (isNaN(n)) return '-';
  return n.toFixed(digits);
}
function priceColor(pct) {
  if (pct === null || pct === undefined) return 'price-flat';
  return pct > 0 ? 'price-up' : (pct < 0 ? 'price-down' : 'price-flat');
}

// ====== 汇总表 ======
function renderSummaryTable() {
  const tbody = document.querySelector('#summary-table tbody');
  const rows = DATA.stocks.map(s => {
    const q = s.quote || {};
    const d = s.dip_analysis;
    const pct = q.pct_change;
    const dipCell = d.triggered
      ? `<strong style="color:var(--accent)">🟢 ${d.hit_count}/6</strong>`
      : `⚪ ${d.hit_count}/6`;
    let pnlCell = '<span style="color:var(--text-sec)">-</span>';
    if (s.exit_analysis) {
      const ex = s.exit_analysis;
      pnlCell = `<span class="${priceColor(ex.pnl_pct)}">${ex.pnl_pct >= 0 ? '+' : ''}${ex.pnl_pct.toFixed(2)}%</span>`;
    }
    return `<tr>
      <td class="code">${s.code}</td>
      <td>${s.name}</td>
      <td>${fmtNum(q.price)}</td>
      <td>${fmtPct(pct)}</td>
      <td>${dipCell}</td>
      <td>${pnlCell}</td>
      <td><span class="tag ${s.decision_class}">${s.decision}</span></td>
    </tr>`;
  }).join('');
  tbody.innerHTML = rows;
}

// ====== 单只股票卡片 ======
function renderStockCard(s) {
  const q = s.quote || {};
  const d = s.dip_analysis;
  const det = d.details || {};

  const indicatorsHTML = `
    <div><span class="k">RSI14</span><span class="v">${fmtNum(det.rsi_14)}</span></div>
    <div><span class="k">KDJ J</span><span class="v">${fmtNum(det.kdj_j)}</span></div>
    <div><span class="k">KDJ K</span><span class="v">${fmtNum(det.kdj_k)}</span></div>
    <div><span class="k">MACD柱</span><span class="v">${fmtNum(det.macd_hist, 3)}</span></div>
    <div><span class="k">BB下轨</span><span class="v">${fmtNum(det.bb_lower)}</span></div>
    <div><span class="k">MA20</span><span class="v">${fmtNum(det.ma20)}</span></div>
    <div><span class="k">MA60</span><span class="v">${fmtNum(det.ma60)}</span></div>
    <div><span class="k">量比20日</span><span class="v">${fmtNum(det.volume_ratio_20d)}</span></div>
    <div><span class="k">换手率</span><span class="v">${fmtNum(q.turnover)}%</span></div>
    <div><span class="k">PE动</span><span class="v">${fmtNum(q.pe_ttm)}</span></div>
    <div><span class="k">PB</span><span class="v">${fmtNum(q.pb)}</span></div>
  `;

  let signalsHTML = '';
  if (d.signals && d.signals.length > 0) {
    signalsHTML = `
      <div class="signals-list">
        <div class="title">抄底信号命中 ${d.hit_count}/6 · 得分 ${d.score}</div>
        <ul>${d.signals.map(x => `<li>${x}</li>`).join('')}</ul>
      </div>
    `;
  }

  let positionHTML = '';
  if (s.exit_analysis) {
    const ex = s.exit_analysis;
    const cls = ex.pnl_pct < 0 ? 'loss' : '';
    const signalsBlock = ex.signals && ex.signals.length
      ? `<div class="signals-list exit"><ul>${ex.signals.map(x => `<li>${x}</li>`).join('')}</ul></div>`
      : '';
    positionHTML = `
      <div class="position-bar ${cls}">
        <strong>持仓</strong> · 成本 ${ex.cost_price.toFixed(2)} / 现价 ${ex.current_price.toFixed(2)} ·
        持股 ${ex.shares} · 浮动盈亏
        <strong class="${priceColor(ex.pnl_pct)}">
          ${ex.pnl_pct >= 0 ? '+' : ''}${ex.pnl_pct.toFixed(2)}%
          (${ex.pnl_amount >= 0 ? '+' : ''}${ex.pnl_amount.toLocaleString('zh-CN', {maximumFractionDigits: 2})}元)
        </strong>
        ${signalsBlock}
      </div>
    `;
  }

  const pct = q.pct_change;
  const pctStr = (pct !== null && pct !== undefined)
    ? `<span class="change ${priceColor(pct)}">${pct >= 0 ? '+' : ''}${parseFloat(pct).toFixed(2)}%</span>`
    : '';

  return `
    <div class="stock-card" id="card-${s.code}">
      <div class="stock-head">
        <span class="code">${s.code}</span>
        <span class="name">${s.name}</span>
        <span class="tag ${s.decision_class}">${s.decision}</span>
        <span class="price ${priceColor(pct)}">${fmtNum(q.price)}</span>
        ${pctStr}
      </div>
      <div class="chart" id="chart-${s.code}"></div>
      <div class="indicators">${indicatorsHTML}</div>
      ${signalsHTML}
      ${positionHTML}
      <div class="ai-block" id="ai-${s.code}">
        <strong>🤖 AI 综合分析</strong>
        <div class="placeholder">（待 Claude 在对话里按 prompts/analyst_prompt.md 结构填入:消息面 / 技术面翻译 / 基本面速查 / 决策 / 置信度 / 风险点）</div>
      </div>
    </div>
  `;
}

// ====== K 线图(ECharts) ======
function renderChart(stock) {
  const container = document.getElementById(`chart-${stock.code}`);
  if (!stock.chart || !container) return;
  const c = stock.chart;
  const chart = echarts.init(container);

  const option = {
    animation: false,
    grid: [
      { left: '8%', right: '4%', top: '6%', height: '62%' },
      { left: '8%', right: '4%', top: '74%', height: '18%' }
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#e4e7eb',
      textStyle: { color: '#1f2329', fontSize: 12 }
    },
    legend: {
      data: ['K线', 'MA5', 'MA20', 'MA60', '布林上', '布林下'],
      top: 0,
      textStyle: { fontSize: 11 },
      selected: { '布林上': false, '布林下': false }
    },
    xAxis: [
      {
        type: 'category', data: c.dates,
        boundaryGap: false, axisLine: { onZero: false },
        splitLine: { show: false },
        axisLabel: { fontSize: 10 }
      },
      {
        type: 'category', gridIndex: 1, data: c.dates,
        boundaryGap: false, axisLine: { onZero: false },
        axisTick: { show: false }, axisLabel: { show: false }
      }
    ],
    yAxis: [
      { scale: true, splitArea: { show: false }, axisLabel: { fontSize: 10 } },
      {
        gridIndex: 1, splitNumber: 2, axisLine: { show: false },
        axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false }
      }
    ],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100 }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: c.ohlc,
        itemStyle: {
          color: '#d9322d', color0: '#0a9a5a',
          borderColor: '#d9322d', borderColor0: '#0a9a5a'
        }
      },
      { name: 'MA5', type: 'line', data: c.ma5,
        smooth: true, lineStyle: { width: 1, color: '#f59e0b' }, symbol: 'none' },
      { name: 'MA20', type: 'line', data: c.ma20,
        smooth: true, lineStyle: { width: 1, color: '#4c6ef5' }, symbol: 'none' },
      { name: 'MA60', type: 'line', data: c.ma60,
        smooth: true, lineStyle: { width: 1, color: '#8b5cf6' }, symbol: 'none' },
      { name: '布林上', type: 'line', data: c.bb_upper,
        lineStyle: { width: 1, type: 'dashed', color: '#9ca3af', opacity: 0.6 }, symbol: 'none' },
      { name: '布林下', type: 'line', data: c.bb_lower,
        lineStyle: { width: 1, type: 'dashed', color: '#9ca3af', opacity: 0.6 }, symbol: 'none' },
      {
        name: '成交量', type: 'bar',
        xAxisIndex: 1, yAxisIndex: 1,
        data: c.volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: c.ohlc[i][1] >= c.ohlc[i][0] ? '#d9322d' : '#0a9a5a' }
        }))
      }
    ]
  };
  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

// ====== 启动 ======
function init() {
  renderStats();
  renderSummaryTable();
  const grid = document.getElementById('stock-grid');
  grid.innerHTML = DATA.stocks.map(renderStockCard).join('');
  // ECharts 需要 DOM 挂载后再 init
  DATA.stocks.forEach(s => renderChart(s));
}
document.addEventListener('DOMContentLoaded', init);

// ====== 对外:用 JS 填入 AI 分析 ======
// 使用:window.fillAIAnalysis('600519', '...分析文本(可含 HTML)...')
window.fillAIAnalysis = function(code, htmlContent) {
  const el = document.getElementById(`ai-${code}`);
  if (!el) return;
  el.classList.add('filled');
  el.innerHTML = `<strong>🤖 AI 综合分析</strong><div style="margin-top:8px">${htmlContent}</div>`;
};
</script>
</body>
</html>
"""


def render_dashboard(signals_path: str, output_path: str) -> None:
    data = build_dashboard_data(signals_path)
    data_json = json.dumps(data, ensure_ascii=False, default=float)
    html = (HTML_TEMPLATE
            .replace("__DATE__", data["date"])
            .replace("__GEN__", data["generated_at"])
            .replace("__DATA_JSON__", data_json))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[done] HTML 仪表盘 -> {output_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True, help="signals.json 路径")
    ap.add_argument("--output-dir", default="reports")
    ap.add_argument("--output", default=None,
                    help="指定输出文件路径,默认为 <output-dir>/<date>_dashboard.html")
    args = ap.parse_args()

    with open(args.signals, "r", encoding="utf-8") as f:
        date = json.load(f)["date"]
    out = args.output or os.path.join(args.output_dir, f"{date}_dashboard.html")
    render_dashboard(args.signals, out)


if __name__ == "__main__":
    main()
