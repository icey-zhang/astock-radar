"""
Markdown 报告渲染 v3.0 - 三策略架构。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime


TEMPLATE = """# A股三策略分析报告 · {date}

> **免责声明**:本报告由自动化工具 + AI 辅助生成,仅供学习研究参考,**不构成投资建议**。
> 三个策略独立输出信号,消息面综合请在 Claude.ai 对话中补充。
> 股市有风险,"抄底""止盈""波段"本质都是高风险操作,决策请独立判断并自负盈亏。

## 📋 当日汇总

| 代码 | 名称 | 现价 | 涨跌 | 5日累计 | 90日位置 | 主要动作 |
|------|------|------|------|---------|----------|----------|
{summary_rows}

**说明**:
- "5 日累计"= 近 5 个交易日累计涨跌幅 · "90 日位置"= 当前价在近 90 日区间的百分位
- 三策略独立评估,一只股票可能同时触发"持有"与"波段关注"

---

## 🎯 策略触发速览

### 策略 1 · 止盈(持仓)
{tp_summary}

### 策略 2 · 抄底(未持仓)
{dip_summary}

### 策略 3 · 波段关注(独立信号)
{swing_summary}

---

{details}

---

## 策略参数速查

### 策略 1 · 止盈
- 浮盈 ≥ 18% (`tp_min_pnl_pct`) 为触发底线
- 近 5 日累计涨 ≥ 3% (`tp_trend_cum_pct`)
- 90 日区间位置 ≥ 60% (`tp_90d_position_pct`)
- 卖出价 = max(成本×1.20, 90日高×0.98)

### 策略 2 · 抄底
- 近 5 日创新低 + 累计跌 ≥ 3% (`dip_trend_cum_pct`) → 初级候选
- 加上接近/跌破 90 日低位 × 1.02 (`dip_90d_low_tol`) → 强候选
- 激进档 = 现价;稳健档 = 90日低 × 1.01
- 止损距离 5% (`dip_stop_loss_pct`)

### 策略 3 · 波段关注
- 信号命中 ≥2 个触发(共 4 个:MACD 金叉/KDJ 低位金叉/量比>1.5/突破10日高)
- 目标 +6% (`swing_target_pct`) / 止损 -3% (`swing_stop_pct`)
- ⚠ 历史命中率有限(~50%),仅作候选筛选

全部参数在 `config/stocks.yml` 的 `strategy` 下可调。

*报告生成时间:{generated}*
"""


def fmt_num(v, digits=2):
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v):
    if v is None or v == "":
        return "-"
    try:
        n = float(v)
        return f"{n:+.2f}%"
    except (TypeError, ValueError):
        return "-"


def primary_decision(r):
    """主要动作文字(用于汇总表)。"""
    tp = r.get("strategy_take_profit")
    db = r.get("strategy_dip_buy")
    sw = r.get("strategy_swing", {})

    # 生成浮亏预警后缀(仅持仓)
    loss_suffix = ""
    if tp and tp.get("loss_warning"):
        lw = tp["loss_warning"]
        badge_map = {
            "light":    "🟡 轻度浮亏",
            "moderate": "🟠 中度浮亏",
            "deep":     "🔴 深度浮亏",
        }
        loss_suffix = f" · {badge_map.get(lw['level'], '')}"

    if tp:
        a = tp["action"]
        if a == "SELL_STRONG":
            return "🔴 **全部止盈**" + loss_suffix
        if a == "SELL_PARTIAL":
            return "⚠ 部分止盈" + loss_suffix
        if a == "WATCH_TO_SELL":
            return "观察止盈" + loss_suffix
        if sw.get("triggered"):
            return "持有+波段" + loss_suffix
        return "持有" + loss_suffix

    if db:
        if db["tier"] == "STRONG":
            return "🟢 **强抄底候选**"
        if db["tier"] == "LIGHT":
            return "🔵 抄底候选"
    if sw.get("triggered"):
        return "🟣 波段关注"
    return "观望"


def render_detail(r):
    code = r["code"]
    name = r["name"]
    q = r.get("quote", {})
    ctx = r.get("context", {})
    trend = ctx.get("trend_5d", {})
    rng = ctx.get("range_90d", {})

    lines = [f"## {code} · {name}\n"]

    # 现价
    price_str = fmt_num(q.get("price"))
    pct_str = fmt_pct(q.get("pct_change"))
    lines.append(f"**现价**:{price_str}  **涨跌**:{pct_str}")
    lines.append("")

    # 共用上下文
    lines.append("**上下文**")
    lines.append(
        f"- 近 5 日累计:**{fmt_pct(trend.get('cum_pct'))}** "
        f"({trend.get('days_up', 0)} 涨 / {trend.get('days_down', 0)} 跌)"
    )
    lines.append(
        f"- 90 日区间:低 {fmt_num(rng.get('low'))} ~ 高 {fmt_num(rng.get('high'))} "
        f"| 当前位置 **{rng.get('position_pct', 0):.0f}%**"
    )
    lines.append(
        f"- PE(动) {fmt_num(q.get('pe_ttm'))}  PB {fmt_num(q.get('pb'))}  "
        f"换手率 {fmt_num(q.get('turnover'))}%"
    )
    lines.append("")

    # --- 策略 1:止盈 ---
    tp = r.get("strategy_take_profit")
    if tp:
        pos = tp["position"]
        pnl = pos["pnl_pct"]
        pnl_str = f"{pnl:+.2f}%" if pnl is not None else "-"
        status = "🔴 **触发**" if tp["triggered"] else "⚪ 未触发"

        # 浮亏预警(仅视觉)
        lw = tp.get("loss_warning")
        warn_badge = ""
        if lw:
            badge_map = {
                "light":    "🟡 **轻度浮亏预警**",
                "moderate": "🟠 **中度浮亏预警**",
                "deep":     "🔴 **深度浮亏预警**",
            }
            warn_badge = f" · {badge_map.get(lw['level'], '')}"

        lines.append(f"### 💰 策略 1 · 止盈 {status}{warn_badge}")
        lines.append(
            f"- 成本 {fmt_num(pos['cost_price'])} / 现价 {fmt_num(pos['current_price'])} / "
            f"持股 {pos['shares']} / 浮盈 **{pnl_str}**"
        )
        if lw:
            lines.append(f"- _{lw['level_cn']}({pnl_str}) — 仅视觉提示,不给具体动作,请自行判断_")
        lines.append(
            f"- 三项条件:浮盈达标={'✓' if tp['conditions']['pnl_over_threshold'] else '✗'} · "
            f"5日涨势={'✓' if tp['conditions']['trend_5d_up'] else '✗'} · "
            f"90日高位={'✓' if tp['conditions']['at_90d_high'] else '✗'}"
        )
        if tp["reasons"]:
            for reason in tp["reasons"]:
                lines.append(f"  - ✓ {reason}")
        lines.append(
            f"- **建议卖出价:{fmt_num(tp['suggested_exit_price'])}** "
            f"(依据 {'90日高×0.98' if tp['exit_basis'] == '90d_high' else '成本×1.20'})"
        )
        lines.append(f"- 动作:**{tp['action_cn']}** · 置信度:{tp['confidence']}")
        lines.append("")

    # --- 策略 2:抄底 ---
    db = r.get("strategy_dip_buy")
    if db:
        status = ("🟢 **触发**" if db["triggered"] else "⚪ 未触发")
        lines.append(f"### 📉 策略 2 · 抄底 {status}")
        lines.append(
            f"- 两项条件:5日新低+累计跌≥3%={'✓' if db['conditions']['new_low_5d_with_drop'] else '✗'} · "
            f"接近/跌破90日低={'✓' if db['conditions']['below_90d_low'] else '✗'}"
        )
        if db["reasons"]:
            for reason in db["reasons"]:
                lines.append(f"  - ✓ {reason}")
        if db["triggered"]:
            lines.append(
                f"- **激进档买入价:{fmt_num(db['suggested_entry_price_aggressive'])}**"
            )
            if db["suggested_entry_price_conservative"]:
                lines.append(
                    f"- **稳健档买入价:{fmt_num(db['suggested_entry_price_conservative'])}** "
                    f"(90日最低 × 1.01)"
                )
            lines.append(f"- 止损参考:{fmt_num(db['suggested_stop_loss'])} (-5%)")
            lines.append(f"- 档位:**{db['tier_cn']}** · 置信度:{db['confidence']}")
        lines.append("")

    # --- 策略 3:波段 ---
    sw = r.get("strategy_swing")
    if sw:
        status = ("🟣 **触发**" if sw["triggered"] else f"⚪ 命中 {sw['hit_count']}/4 未达阈值")
        lines.append(f"### ⚡ 策略 3 · 波段关注 {status}")
        if sw["signals"]:
            for sig in sw["signals"]:
                lines.append(f"  - ✓ {sig}")
        if sw["triggered"]:
            lines.append(
                f"- **买入:{fmt_num(sw['suggested_entry'])} → "
                f"目标:{fmt_num(sw['suggested_target'])} (+{sw['expected_move_pct']}%) → "
                f"止损:{fmt_num(sw['suggested_stop'])}**"
            )
            lines.append(f"- 置信度:{sw['confidence']}")
            lines.append(f"- _{sw.get('note', '')}_")
        lines.append("")

    # --- AI 综合分析占位 ---
    lines.append("### 🤖 AI 综合分析(消息面)")
    lines.append("")
    lines.append("<!-- CLAUDE_FILL_START -->")
    lines.append(f"> Claude 在 Claude.ai Project 里填充。对于 **{code} {name}**:")
    lines.append("> 1. **消息面**(web_search 最近 2 天):公告/业绩/行业政策/突发")
    lines.append("> 2. **信号有效性复核**:技术信号是否被消息面证伪/增强")
    lines.append("> 3. **最终建议**:")
    lines.append(">    - 若策略 1 触发 → 确认卖出价位及分批计划")
    lines.append(">    - 若策略 2 触发 → 确认买入档位及分批计划")
    lines.append(">    - 若策略 3 触发 → 确认是否值得快进,或放弃(消息面不支持)")
    lines.append("> 4. **风险点**(1-3 条)")
    lines.append("<!-- CLAUDE_FILL_END -->")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def render_strategy_summary(results, strategy_key: str, filter_fn) -> str:
    matching = [r for r in results if filter_fn(r)]
    if not matching:
        return "_今日无触发。_"
    lines = []
    for r in matching:
        lines.append(f"- **{r['code']} {r['name']}**")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--output-dir", default="reports")
    args = ap.parse_args()

    with open(args.signals, "r", encoding="utf-8") as f:
        data = json.load(f)

    date = data["date"]
    results = data["results"]

    # 汇总表
    rows = []
    for r in results:
        q = r.get("quote", {})
        ctx = r.get("context", {})
        trend = ctx.get("trend_5d", {})
        rng = ctx.get("range_90d", {})
        price = fmt_num(q.get("price"))
        pct = fmt_pct(q.get("pct_change"))
        cum5 = fmt_pct(trend.get("cum_pct"))
        pos90 = rng.get("position_pct")
        pos90_s = f"{pos90:.0f}%" if pos90 is not None else "-"
        decision = primary_decision(r)
        rows.append(f"| {r['code']} | {r['name']} | {price} | {pct} | {cum5} | {pos90_s} | {decision} |")

    tp_summary = render_strategy_summary(
        results, "tp",
        lambda r: r.get("strategy_take_profit", {}).get("triggered"))
    dip_summary = render_strategy_summary(
        results, "dip",
        lambda r: r.get("strategy_dip_buy", {}).get("triggered"))
    swing_summary = render_strategy_summary(
        results, "swing",
        lambda r: r.get("strategy_swing", {}).get("triggered"))

    details = "\n".join(render_detail(r) for r in results)
    report = TEMPLATE.format(
        date=date,
        summary_rows="\n".join(rows),
        tp_summary=tp_summary,
        dip_summary=dip_summary,
        swing_summary=swing_summary,
        details=details,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(args.output_dir, f"{date}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[done] 报告骨架 -> {out}")


if __name__ == "__main__":
    main()
