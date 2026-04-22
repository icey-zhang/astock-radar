"""
HTML 仪表盘 v3.0 - 三策略架构展示。

每只股票卡片展示:
    - 共用上下文:5 天趋势 / 90 天区间 / 现价
    - 策略 1(持仓):止盈信号 + 建议卖出价
    - 策略 2(未持仓):抄底信号 + 激进/稳健买入价
    - 策略 3:波段关注信号 + 快进快出价位(持仓和未持仓都展示)
    - 30 天 K 线图(ECharts)
    - AI 综合分析占位
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime


# ============================== 辅助计算 ==============================
def rolling_mean(values, period):
    out = []
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


def rolling_std(values, period):
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            m = sum(window) / period
            var = sum((v - m) ** 2 for v in window) / period
            out.append(var ** 0.5)
    return out


def bollinger_bands(closes, period=20, std=2.0):
    mid = rolling_mean(closes, period)
    sd = rolling_std(closes, period)
    upper = [m + std * s if (m is not None and s is not None) else None
             for m, s in zip(mid, sd)]
    lower = [m - std * s if (m is not None and s is not None) else None
             for m, s in zip(mid, sd)]
    return lower, mid, upper


# ============================== 决策归一化 ==============================
def resolve_primary_decision(r):
    """返回 (决策文本, CSS class)。按优先级:止损/止盈 > 抄底 > 波段 > 观望。"""
    tp = r.get("strategy_take_profit")
    db = r.get("strategy_dip_buy")
    sw = r.get("strategy_swing", {})

    # 持仓 - 止盈优先
    if tp:
        action = tp["action"]
        if action == "SELL_STRONG":
            return tp["action_cn"], "tag-tp-strong"
        if action == "SELL_PARTIAL":
            return tp["action_cn"], "tag-tp-partial"
        if action == "WATCH_TO_SELL":
            return tp["action_cn"], "tag-tp-watch"
        # action == HOLD
        if sw.get("triggered"):
            return "持有 + 波段关注", "tag-swing-hold"
        return "持有", "tag-hold"

    # 未持仓
    if db:
        if db["tier"] == "STRONG":
            return "强抄底候选", "tag-dip-strong"
        if db["tier"] == "LIGHT":
            return "抄底候选", "tag-dip"
    if sw.get("triggered"):
        return "波段关注", "tag-swing"
    return "观望", "tag-watch"


# ============================== K 线图数据 ==============================
def build_chart_payload(kline, lookback=30):
    recent = kline[-lookback:] if len(kline) > lookback else kline
    dates = [k["date"] for k in recent]
    opens = [k["open"] for k in recent]
    closes = [k["close"] for k in recent]
    highs = [k["high"] for k in recent]
    lows = [k["low"] for k in recent]
    volumes = [k["volume"] for k in recent]

    ohlc = [[o, c, l, h] for o, c, l, h in zip(opens, closes, lows, highs)]

    full_closes = [k["close"] for k in kline]
    ma5 = rolling_mean(full_closes, 5)
    ma10 = rolling_mean(full_closes, 10)
    ma20 = rolling_mean(full_closes, 20)
    bb_lo, bb_mid, bb_hi = bollinger_bands(full_closes, 20, 2.0)

    tail = slice(-len(recent), None)
    return {
        "dates": dates,
        "ohlc": ohlc,
        "volumes": volumes,
        "ma5": ma5[tail],
        "ma10": ma10[tail],
        "ma20": ma20[tail],
        "bb_upper": bb_hi[tail],
        "bb_lower": bb_lo[tail],
    }


def load_kline(data_dir, code):
    path = os.path.join(data_dir, f"{code}.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("kline_30d") or payload.get("kline", [])


def build_dashboard_data(signals_path):
    with open(signals_path, "r", encoding="utf-8") as f:
        sig = json.load(f)

    data_dir = os.path.dirname(os.path.abspath(signals_path))
    date = sig["date"]
    stocks = []
    cnt_tp = cnt_dip = cnt_swing = cnt_hold = 0

    for r in sig["results"]:
        code = r["code"]
        kline = load_kline(data_dir, code)
        chart = build_chart_payload(kline) if kline else None
        decision, dec_class = resolve_primary_decision(r)

        if "strategy_take_profit" in r and r["strategy_take_profit"]["triggered"]:
            cnt_tp += 1
        elif r.get("strategy_dip_buy", {}).get("triggered"):
            cnt_dip += 1
        elif r.get("strategy_swing", {}).get("triggered"):
            cnt_swing += 1
        elif "strategy_take_profit" in r:
            cnt_hold += 1

        stocks.append({
            "code": code,
            "name": r["name"],
            "quote": r.get("quote", {}),
            "context": r.get("context", {}),
            "strategy_take_profit": r.get("strategy_take_profit"),
            "strategy_dip_buy": r.get("strategy_dip_buy"),
            "strategy_swing": r.get("strategy_swing"),
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
            "take_profit": cnt_tp,
            "dip_candidates": cnt_dip,
            "swing": cnt_swing,
            "hold": cnt_hold,
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
    --up: #d9322d; --down: #0a9a5a;
    --bg: #f7f8fa; --card: #ffffff; --border: #e4e7eb;
    --text: #1f2329; --text-sec: #6b7280;
    --accent: #4c6ef5; --warn: #f59e0b; --danger: #ef4444;
    --swing: #8b5cf6;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
    padding: 24px 16px 64px;
  }
  .container { max-width: 1400px; margin: 0 auto; }
  header {
    background: linear-gradient(135deg, #1e3a8a, #4c6ef5);
    color: white; padding: 24px 28px; border-radius: 12px; margin-bottom: 20px;
  }
  header h1 { font-size: 24px; font-weight: 600; }
  header .date { font-size: 14px; opacity: 0.85; margin-top: 4px; }
  header .disclaimer {
    margin-top: 12px; font-size: 12px; opacity: 0.85;
    padding: 10px 12px; background: rgba(255,255,255,0.1);
    border-left: 3px solid #fff; border-radius: 4px; line-height: 1.6;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }
  .stat {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }
  .stat .label { font-size: 12px; color: var(--text-sec); }
  .stat .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
  .stat.tp .value { color: var(--warn); }
  .stat.dip .value { color: var(--accent); }
  .stat.swing .value { color: var(--swing); }

  section {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; margin-bottom: 20px;
  }
  section > h2 {
    font-size: 16px; font-weight: 600; margin-bottom: 14px;
    border-left: 3px solid var(--accent); padding-left: 10px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
  th { background: #f9fafb; font-weight: 500; color: var(--text-sec); font-size: 12px; }
  tr:hover td { background: #fafbfc; }
  td.code { font-family: "SF Mono", Menlo, monospace; font-weight: 600; }

  .price-up { color: var(--up); font-weight: 500; }
  .price-down { color: var(--down); font-weight: 500; }
  .price-flat { color: var(--text-sec); }

  .tag {
    display: inline-block; padding: 3px 10px; border-radius: 4px;
    font-size: 12px; font-weight: 500; white-space: nowrap;
  }
  .tag-hold        { background: #fef3c7; color: #92400e; }
  .tag-tp-watch    { background: #fed7aa; color: #9a3412; }
  .tag-tp-partial  { background: #fdba74; color: #7c2d12; }
  .tag-tp-strong   { background: #ea580c; color: white; font-weight: 600; }
  .tag-dip         { background: #e0e7ff; color: #3730a3; }
  .tag-dip-strong  { background: #4c6ef5; color: white; font-weight: 600; }
  .tag-swing       { background: #ede9fe; color: #6d28d9; }
  .tag-swing-hold  { background: #c4b5fd; color: #4c1d95; }
  .tag-watch       { background: #f3f4f6; color: #6b7280; }
  /* 浮亏预警标签(仅视觉,不给动作) */
  .tag-loss-light    { background: #fef3c7; color: #92400e; }
  .tag-loss-moderate { background: #fed7aa; color: #9a3412; font-weight: 600; }
  .tag-loss-deep     { background: #fecaca; color: #991b1b; font-weight: 700; border: 1px solid #f87171; }

  .stock-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(580px, 1fr));
    gap: 16px;
  }
  @media (max-width: 720px) { .stock-grid { grid-template-columns: 1fr; } }

  .stock-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 18px;
  }
  .stock-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.06); }

  .stock-head {
    display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
    padding-bottom: 12px; border-bottom: 1px solid var(--border);
    margin-bottom: 12px;
  }
  .stock-head .code { font-family: "SF Mono", Menlo, monospace; font-size: 15px; color: var(--text-sec); font-weight: 600; }
  .stock-head .name { font-size: 18px; font-weight: 600; }
  .stock-head .price { font-size: 20px; font-weight: 600; margin-left: auto; }
  .stock-head .change { font-size: 14px; font-weight: 500; }

  .context-box {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    background: #f9fafb; border-radius: 6px; padding: 10px 12px;
    font-size: 12px; margin: 10px 0;
  }
  .context-box .k { color: var(--text-sec); }
  .context-box .v { font-weight: 500; margin-left: 4px; }

  .chart { width: 100%; height: 280px; margin: 8px 0; }

  .strategy-box {
    margin: 12px 0; padding: 12px 14px; border-radius: 6px;
    border-left: 3px solid; font-size: 13px;
  }
  .strategy-box.tp        { background: #fff7ed; border-left-color: var(--warn); }
  .strategy-box.tp.strong { background: #fff7ed; border-left-color: var(--up); border-left-width: 4px; }
  .strategy-box.dip       { background: #eff6ff; border-left-color: var(--accent); }
  .strategy-box.dip.strong{ background: #eff6ff; border-left-color: var(--accent); border-left-width: 4px; }
  .strategy-box.swing     { background: #faf5ff; border-left-color: var(--swing); }
  .strategy-box.hold      { background: #fffbeb; border-left-color: #d1d5db; }
  .strategy-box.inactive  { background: #f9fafb; border-left-color: #d1d5db; color: var(--text-sec); }

  .strategy-box .title { font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
  .strategy-box .price-row {
    display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    margin: 8px 0; padding: 8px; background: rgba(255,255,255,0.6); border-radius: 4px;
  }
  .strategy-box .price-row > div .label {
    font-size: 11px; color: var(--text-sec); display: block;
  }
  .strategy-box .price-row > div .val {
    font-size: 15px; font-weight: 600; font-family: "SF Mono", Menlo, monospace;
  }
  .strategy-box ul.reasons { margin: 4px 0 0 0; list-style: none; }
  .strategy-box ul.reasons li {
    font-size: 12px; padding: 2px 0 2px 18px; position: relative;
  }
  .strategy-box ul.reasons li::before {
    content: "✓"; position: absolute; left: 2px; font-weight: 600;
  }
  .strategy-box.tp ul.reasons li::before { color: var(--warn); }
  .strategy-box.dip ul.reasons li::before { color: var(--accent); }
  .strategy-box.swing ul.reasons li::before { color: var(--swing); }

  .confidence {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 500;
    background: white; color: var(--text-sec); border: 1px solid var(--border);
  }
  .confidence.h, .confidence.较高 { background: #fef3c7; color: #92400e; border-color: #fde68a; }
  .confidence.m { background: #dbeafe; color: #1e40af; border-color: #bfdbfe; }
  .confidence.l { background: #f3f4f6; color: #6b7280; }

  .ai-block {
    margin-top: 14px; padding: 12px 14px;
    background: #f5f3ff; border: 1px dashed #c4b5fd;
    border-radius: 6px; font-size: 13px; color: #6d28d9;
  }
  .ai-block .placeholder { color: #8b5cf6; font-style: italic; }
  .ai-block.filled {
    background: #faf5ff; color: var(--text);
    border-style: solid; border-color: #ddd6fe;
  }

  footer {
    margin-top: 32px; padding: 16px; text-align: center;
    font-size: 12px; color: var(--text-sec); line-height: 1.8;
  }
  footer .disclaimer {
    max-width: 720px; margin: 0 auto; padding: 12px;
    background: #fef2f2; color: #7f1d1d;
    border-radius: 6px; border: 1px solid #fecaca;
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>📈 A股三策略仪表盘</h1>
    <div class="date">交易日 <span>__DATE__</span> · 生成于 <span>__GEN__</span></div>
    <div class="disclaimer">
      <strong>免责声明:</strong> 本报告由自动化工具 + AI 辅助生成,仅供学习研究参考,<strong>不构成投资建议</strong>。
      三个策略独立输出信号,消息面综合请在 Claude.ai 对话中补充。股市有风险,决策请独立判断并自负盈亏。
    </div>
  </header>

  <div class="stats" id="stats"></div>

  <section>
    <h2>📋 当日汇总</h2>
    <table id="summary-table">
      <thead>
        <tr>
          <th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th>
          <th>5日趋势</th><th>90日位置</th><th>主要动作</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>🔍 个股三策略详情</h2>
    <div class="stock-grid" id="stock-grid"></div>
  </section>

  <footer>
    <div class="disclaimer">
      ⚠ 波段策略(策略3)的短期预测能力有限(历史命中率约 50%),仅作候选筛选参考,务必设止损。
    </div>
    <div style="margin-top:12px">astock-daily-analysis · routine v3.0 · 数据源 akshare</div>
  </footer>

</div>

<script>
const DATA = __DATA_JSON__;

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

function renderStats() {
  const s = DATA.summary;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="label">关注股票</div><div class="value">${s.total}</div></div>
    <div class="stat tp"><div class="label">止盈触发</div><div class="value">${s.take_profit}</div></div>
    <div class="stat dip"><div class="label">抄底候选</div><div class="value">${s.dip_candidates}</div></div>
    <div class="stat swing"><div class="label">波段关注</div><div class="value">${s.swing}</div></div>
    <div class="stat"><div class="label">持有观望</div><div class="value">${s.hold}</div></div>
  `;
}

function renderSummaryTable() {
  const tbody = document.querySelector('#summary-table tbody');
  const rows = DATA.stocks.map(s => {
    const q = s.quote || {};
    const ctx = s.context || {};
    const trend = ctx.trend_5d || {};
    const rng = ctx.range_90d || {};

    // 浮亏预警(不改变决策,只在标签后追加)
    let lossBadge = '';
    const tp = s.strategy_take_profit;
    if (tp && tp.loss_warning) {
      const lw = tp.loss_warning;
      lossBadge = ` <span class="tag tag-loss-${lw.level}" title="浮亏 ${lw.pnl_pct}%">⚠ ${lw.level_cn}</span>`;
    }

    return `<tr>
      <td class="code">${s.code}</td>
      <td>${s.name}</td>
      <td>${fmtNum(q.price)}</td>
      <td>${fmtPct(q.pct_change)}</td>
      <td>${fmtPct(trend.cum_pct)}</td>
      <td>${rng.position_pct !== null && rng.position_pct !== undefined ? rng.position_pct.toFixed(0) + '%' : '-'}</td>
      <td><span class="tag ${s.decision_class}">${s.decision}</span>${lossBadge}</td>
    </tr>`;
  }).join('');
  tbody.innerHTML = rows;
}

function renderStrategyTP(tp, pos) {
  if (!tp) return '';
  const triggered = tp.triggered;
  const strong = tp.action === 'SELL_STRONG';
  const cls = `strategy-box tp${strong ? ' strong' : ''}`;

  const pnl = tp.position.pnl_pct;
  const pnlCls = priceColor(pnl);
  const reasonsHTML = tp.reasons.length
    ? `<ul class="reasons">${tp.reasons.map(r => `<li>${r}</li>`).join('')}</ul>`
    : '<div style="font-size:12px;color:var(--text-sec)">(未满足触发条件)</div>';

  const confKey = {'高':'h','中':'m','低':'l','较高':'较高','未触发':'l'}[tp.confidence] || 'l';

  // 浮亏预警标签(仅视觉)
  let lossBadge = '';
  if (tp.loss_warning) {
    const lw = tp.loss_warning;
    lossBadge = `<span class="tag tag-loss-${lw.level}" title="仅视觉提示,不给具体动作">⚠ ${lw.level_cn}</span>`;
  }

  return `
    <div class="${cls}">
      <div class="title">
        💰 策略1 · 止盈(持仓)
        <span class="tag tag-${tp.action === 'SELL_STRONG' ? 'tp-strong' : 'tp-partial'}">${tp.action_cn}</span>
        ${lossBadge}
        <span class="confidence ${confKey}">置信度 ${tp.confidence}</span>
      </div>
      <div style="font-size:12px;margin-bottom:6px">
        成本 ${fmtNum(tp.position.cost_price)} · 现价 ${fmtNum(tp.position.current_price)} ·
        持股 ${tp.position.shares} ·
        浮盈 <strong class="${pnlCls}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</strong>
      </div>
      <div class="price-row">
        <div><span class="label">建议卖出价</span><span class="val">${fmtNum(tp.suggested_exit_price)}</span></div>
        <div><span class="label">价位依据</span><span class="val" style="font-size:12px">${tp.exit_basis === '90d_high' ? '90天高位 × 0.98' : '成本 × 1.20'}</span></div>
      </div>
      ${reasonsHTML}
    </div>
  `;
}

function renderStrategyDip(db) {
  if (!db) return '';
  const triggered = db.triggered;
  const strong = db.tier === 'STRONG';
  const cls = `strategy-box dip${strong ? ' strong' : ''}${triggered ? '' : ' inactive'}`;

  const reasonsHTML = db.reasons.length
    ? `<ul class="reasons">${db.reasons.map(r => `<li>${r}</li>`).join('')}</ul>`
    : '<div style="font-size:12px;color:var(--text-sec)">(未触发抄底信号)</div>';

  const confKey = {'高':'h','中':'m','低':'l','未触发':'l'}[db.confidence] || 'l';

  const priceRowHTML = triggered ? `
    <div class="price-row">
      <div>
        <span class="label">激进档买入价</span>
        <span class="val">${fmtNum(db.suggested_entry_price_aggressive)}</span>
      </div>
      <div>
        <span class="label">稳健档买入价</span>
        <span class="val">${db.suggested_entry_price_conservative !== null ? fmtNum(db.suggested_entry_price_conservative) : '-'}</span>
      </div>
    </div>
    <div style="font-size:12px;color:var(--text-sec)">
      止损参考: ${fmtNum(db.suggested_stop_loss)} (-${db.price_reference.cum_pct_5d ? '5%' : ''})
    </div>
  ` : '';

  return `
    <div class="${cls}">
      <div class="title">
        📉 策略2 · 抄底(未持仓)
        <span class="tag ${strong ? 'tag-dip-strong' : (triggered ? 'tag-dip' : 'tag-watch')}">${db.tier_cn}</span>
        ${triggered ? `<span class="confidence ${confKey}">置信度 ${db.confidence}</span>` : ''}
      </div>
      ${priceRowHTML}
      ${reasonsHTML}
    </div>
  `;
}

function renderStrategySwing(sw) {
  if (!sw) return '';
  const triggered = sw.triggered;
  const cls = `strategy-box swing${triggered ? '' : ' inactive'}`;

  const confKey = {'较高':'h','中':'m','低':'l','未触发':'l'}[sw.confidence] || 'l';

  const signalsHTML = sw.signals && sw.signals.length
    ? `<ul class="reasons">${sw.signals.map(s => `<li>${s}</li>`).join('')}</ul>`
    : `<div style="font-size:12px;color:var(--text-sec)">信号命中 ${sw.hit_count}/4,未达阈值</div>`;

  const priceRowHTML = triggered ? `
    <div class="price-row">
      <div><span class="label">买入价</span><span class="val">${fmtNum(sw.suggested_entry)}</span></div>
      <div><span class="label">目标价 (+${sw.expected_move_pct}%)</span><span class="val">${fmtNum(sw.suggested_target)}</span></div>
    </div>
    <div style="font-size:12px;color:var(--text-sec)">止损: ${fmtNum(sw.suggested_stop)}</div>
  ` : '';

  return `
    <div class="${cls}">
      <div class="title">
        ⚡ 策略3 · 波段(独立候选)
        <span class="tag ${triggered ? 'tag-swing' : 'tag-watch'}">${triggered ? '波段关注' : '未触发'}</span>
        ${triggered ? `<span class="confidence ${confKey}">置信度 ${sw.confidence}</span>` : ''}
      </div>
      ${priceRowHTML}
      ${signalsHTML}
      ${triggered ? `<div style="font-size:11px;color:var(--text-sec);margin-top:6px;font-style:italic">${sw.note || ''}</div>` : ''}
    </div>
  `;
}

function renderStockCard(s) {
  const q = s.quote || {};
  const ctx = s.context || {};
  const trend = ctx.trend_5d || {};
  const rng = ctx.range_90d || {};
  const pct = q.pct_change;
  const pctStr = (pct !== null && pct !== undefined)
    ? `<span class="change ${priceColor(pct)}">${pct >= 0 ? '+' : ''}${parseFloat(pct).toFixed(2)}%</span>`
    : '';

  const contextHTML = `
    <div class="context-box">
      <div><span class="k">5日累计</span><span class="v">${fmtPct(trend.cum_pct)}</span></div>
      <div><span class="k">5日涨/跌</span><span class="v">${trend.days_up}涨/${trend.days_down}跌</span></div>
      <div><span class="k">90日高</span><span class="v">${fmtNum(rng.high)}</span></div>
      <div><span class="k">90日低</span><span class="v">${fmtNum(rng.low)}</span></div>
      <div><span class="k">区间位置</span><span class="v">${rng.position_pct !== null && rng.position_pct !== undefined ? rng.position_pct.toFixed(0) + '%' : '-'}</span></div>
      <div><span class="k">PE/PB</span><span class="v">${fmtNum(q.pe_ttm)} / ${fmtNum(q.pb)}</span></div>
    </div>
  `;

  const tpHTML = renderStrategyTP(s.strategy_take_profit, s.position);
  const dipHTML = renderStrategyDip(s.strategy_dip_buy);
  const swHTML = renderStrategySwing(s.strategy_swing);

  return `
    <div class="stock-card" id="card-${s.code}">
      <div class="stock-head">
        <span class="code">${s.code}</span>
        <span class="name">${s.name}</span>
        <span class="tag ${s.decision_class}">${s.decision}</span>
        <span class="price ${priceColor(pct)}">${fmtNum(q.price)}</span>
        ${pctStr}
      </div>
      ${contextHTML}
      <div class="chart" id="chart-${s.code}"></div>
      ${tpHTML}
      ${dipHTML}
      ${swHTML}
      <div class="ai-block" id="ai-${s.code}">
        <strong>🤖 AI 综合分析(消息面)</strong>
        <div class="placeholder">待 Claude.ai 补充:最近公告/业绩/行业/突发事件;确认信号有效性;生成最终决策建议。</div>
      </div>
    </div>
  `;
}

function renderChart(stock) {
  const el = document.getElementById(`chart-${stock.code}`);
  if (!stock.chart || !el) return;
  const c = stock.chart;
  const chart = echarts.init(el);

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
      data: ['K线', 'MA5', 'MA10', 'MA20', '布林上', '布林下'],
      top: 0, textStyle: { fontSize: 11 },
      selected: { '布林上': false, '布林下': false }
    },
    xAxis: [
      { type: 'category', data: c.dates, boundaryGap: false, axisLine: { onZero: false },
        splitLine: { show: false }, axisLabel: { fontSize: 10 } },
      { type: 'category', gridIndex: 1, data: c.dates, boundaryGap: false,
        axisLine: { onZero: false }, axisTick: { show: false }, axisLabel: { show: false } }
    ],
    yAxis: [
      { scale: true, splitArea: { show: false }, axisLabel: { fontSize: 10 } },
      { gridIndex: 1, splitNumber: 2, axisLine: { show: false },
        axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } }
    ],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
    series: [
      { name: 'K线', type: 'candlestick', data: c.ohlc,
        itemStyle: {
          color: '#d9322d', color0: '#0a9a5a',
          borderColor: '#d9322d', borderColor0: '#0a9a5a'
        }
      },
      { name: 'MA5', type: 'line', data: c.ma5, smooth: true,
        lineStyle: { width: 1, color: '#f59e0b' }, symbol: 'none' },
      { name: 'MA10', type: 'line', data: c.ma10, smooth: true,
        lineStyle: { width: 1, color: '#4c6ef5' }, symbol: 'none' },
      { name: 'MA20', type: 'line', data: c.ma20, smooth: true,
        lineStyle: { width: 1, color: '#8b5cf6' }, symbol: 'none' },
      { name: '布林上', type: 'line', data: c.bb_upper,
        lineStyle: { width: 1, type: 'dashed', color: '#9ca3af', opacity: 0.6 }, symbol: 'none' },
      { name: '布林下', type: 'line', data: c.bb_lower,
        lineStyle: { width: 1, type: 'dashed', color: '#9ca3af', opacity: 0.6 }, symbol: 'none' },
      { name: '成交量', type: 'bar',
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

function init() {
  renderStats();
  renderSummaryTable();
  document.getElementById('stock-grid').innerHTML = DATA.stocks.map(renderStockCard).join('');
  DATA.stocks.forEach(renderChart);
}
document.addEventListener('DOMContentLoaded', init);

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


def render_dashboard(signals_path, output_path):
    data = build_dashboard_data(signals_path)
    data_json = json.dumps(data, ensure_ascii=False, default=float)
    html = (HTML_TEMPLATE
            .replace("__DATE__", data["date"])
            .replace("__GEN__", data["generated_at"])
            .replace("__DATA_JSON__", data_json))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[done] HTML 仪表盘 -> {output_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--output-dir", default="reports")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    with open(args.signals, "r", encoding="utf-8") as f:
        date = json.load(f)["date"]
    out = args.output or os.path.join(args.output_dir, f"{date}_dashboard.html")
    render_dashboard(args.signals, out)


if __name__ == "__main__":
    main()
