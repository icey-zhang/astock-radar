"""
把 signals.json(+ 可选的消息面 JSON)渲染为 Markdown 每日报告。
AI 综合分析区块保留 {{PLACEHOLDER}},由 Claude 在对话里填充。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime


ACTION_CN = {
    "HOLD": "持有",
    "TAKE_PROFIT": "⚠ 全部止盈",
    "TAKE_PROFIT_PARTIAL": "⚠ 部分止盈",
    "STOP_LOSS": "🔴 止损",
}


TEMPLATE = """# A股每日分析报告 · {date}

> **免责声明**:本报告由自动化工具 + AI 辅助生成,仅供学习研究参考,**不构成投资建议**。股市有风险,任何决策请独立判断并自负盈亏。Claude 不是金融顾问,"抄底"本身是高风险策略,信号命中不保证盈利。

## 📋 决策汇总

| 代码 | 名称 | 现价 | 涨跌幅 | 抄底信号 | 浮动盈亏 | 动作建议 |
|------|------|------|--------|----------|----------|----------|
{summary_rows}

**说明**:抄底信号最大 6 分,命中 ≥ 3 个且消息面无重大利空才算有效候选。持仓部分以成本价为基准判定 +20% 止盈 / -8% 止损。

---

{details}

---

## 策略参数快照

- **抄底信号**:RSI<30 / 跌破布林带下轨 / KDJ J<0 / 地量(量比<0.7) / 60日底部10% / MACD 简化底背离
- **止盈**:浮盈 ≥ **+20%** 触发(核心)。浮盈 ≥ +15% 且 RSI>70 触发部分止盈。
- **止损**:浮亏 ≤ -8% 触发。
- 参数在 `config/stocks.yml` 的 `strategy` 下可调。

*报告生成时间:{generated}*
"""


def fmt_num(v, digits=2):
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def render_news_block(news_items: list[dict], limit: int = 5) -> str:
    if not news_items:
        return "_无离线新闻数据,建议在对话里让 Claude 用 web_search 补充。_"
    lines = []
    for n in news_items[:limit]:
        title = n.get("title", "").strip()
        pub = n.get("published", "")
        url = n.get("url", "")
        src = n.get("source", "")
        if url:
            lines.append(f"- [{title}]({url}) · {src} · {pub}")
        else:
            lines.append(f"- {title} · {src} · {pub}")
    return "\n".join(lines)


def render_detail(r: dict, news_root: str | None) -> str:
    code = r["code"]
    name = r["name"]
    q = r.get("quote", {})
    d = r["dip_analysis"]
    det = d["details"]

    lines = [f"## {code} · {name}\n"]

    # --- 行情快照 ---
    price = fmt_num(q.get("price"), 2)
    pct = q.get("pct_change", 0)
    pct_str = f"{float(pct):+.2f}%" if pct not in (None, "", "-") else "-"
    lines.append("**行情快照**")
    lines.append(
        f"- 现价:{price}  涨跌幅:{pct_str}  |  "
        f"今开 {fmt_num(q.get('open'))} / 最高 {fmt_num(q.get('high'))} / 最低 {fmt_num(q.get('low'))}"
    )
    lines.append(
        f"- 换手率 {fmt_num(q.get('turnover'))}%  |  "
        f"PE(动) {fmt_num(q.get('pe_ttm'))}  PB {fmt_num(q.get('pb'))}  |  "
        f"总市值 {fmt_num(q.get('market_cap'), 0)}"
    )

    # --- 资金流 ---
    mf = r.get("money_flow_5d", [])
    if mf:
        net_5d = sum(m.get("main_net_inflow", 0) for m in mf)
        lines.append(f"- 近 5 日主力资金净流入合计:{net_5d:,.0f} 元")
    lines.append("")

    # --- 技术指标 ---
    lines.append("**技术指标**")
    lines.append(
        f"- RSI14={fmt_num(det.get('rsi_14'))}  |  "
        f"KDJ J={fmt_num(det.get('kdj_j'))}  K={fmt_num(det.get('kdj_k'))}  D={fmt_num(det.get('kdj_d'))}"
    )
    lines.append(
        f"- 布林带:下轨 {fmt_num(det.get('bb_lower'))} / 中轨 {fmt_num(det.get('bb_mid'))}"
    )
    lines.append(
        f"- MACD: DIF={fmt_num(det.get('macd_dif'), 3)} DEA={fmt_num(det.get('macd_dea'), 3)} "
        f"柱={fmt_num(det.get('macd_hist'), 3)}"
    )
    lines.append(
        f"- 均线:MA20={fmt_num(det.get('ma20'))} MA60={fmt_num(det.get('ma60'))}  |  "
        f"量比(20日)={fmt_num(det.get('volume_ratio_20d'))}"
    )
    lines.append("")

    # --- 抄底信号 ---
    flag = "🟢 **触发抄底候选**" if d["triggered"] else "⚪ 未触发"
    lines.append(f"**抄底信号命中 {d['hit_count']}/{d['total']} · 得分 {d['score']} · {flag}**")
    if d["signals"]:
        for s in d["signals"]:
            lines.append(f"- ✓ {s}")
    else:
        lines.append("- (无命中)")
    lines.append("")

    # --- 持仓与止盈 ---
    if "exit_analysis" in r:
        ex = r["exit_analysis"]
        pos = r["position"]
        lines.append("**持仓状态**")
        lines.append(
            f"- 成本 {ex['cost_price']:.2f}  |  现价 {ex['current_price']:.2f}  |  "
            f"持股 {ex['shares']}  |  建仓 {ex.get('entry_date', '-')}"
        )
        lines.append(
            f"- 浮动盈亏 **{ex['pnl_pct']:+.2f}% / {ex['pnl_amount']:+,.2f}元**  "
            f"→ 动作:**{ACTION_CN.get(ex['action'], ex['action'])}**"
        )
        for s in ex["signals"]:
            lines.append(f"  - ⚠ {s}")
        if pos.get("note"):
            lines.append(f"- 备注:{pos['note']}")
        lines.append("")

    # --- 离线消息面(如有) ---
    if news_root:
        news_path = os.path.join(news_root, f"{code}_news.json")
        if os.path.exists(news_path):
            with open(news_path, "r", encoding="utf-8") as f:
                nd = json.load(f)
            lines.append("**离线消息面(akshare / searxng)**")
            lines.append(render_news_block(nd.get("news", [])))
            lines.append("")

    # --- AI 综合分析占位 ---
    lines.append("### 🤖 AI 综合分析")
    lines.append("")
    lines.append("<!-- CLAUDE_FILL_START -->")
    lines.append(f"> 请 Claude 按 `prompts/analyst_prompt.md` 的结构为 **{code} {name}** 填写:")
    lines.append(">")
    lines.append("> 1. **消息面摘要**(web_search 补充)")
    lines.append("> 2. **技术面翻译**")
    lines.append("> 3. **基本面速查**")
    lines.append("> 4. **决策**:强烈抄底候选 / 抄底候选 / 观望 / 持有 / 部分止盈 / 全部止盈 / 止损")
    lines.append("> 5. **置信度**:低 / 中 / 高")
    lines.append("> 6. **主要风险点**(1-3 条)")
    lines.append("<!-- CLAUDE_FILL_END -->")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True, help="signals.json 路径")
    ap.add_argument("--news-dir", default=None,
                    help="离线消息面目录(默认同 signals.json 目录)")
    ap.add_argument("--output-dir", default="reports")
    args = ap.parse_args()

    with open(args.signals, "r", encoding="utf-8") as f:
        data = json.load(f)

    date = data["date"]
    results = data["results"]

    news_root = args.news_dir or os.path.dirname(os.path.abspath(args.signals))
    if not glob.glob(os.path.join(news_root, "*_news.json")):
        news_root = None

    # 汇总表
    rows = []
    for r in results:
        q = r.get("quote", {})
        d = r["dip_analysis"]
        price = fmt_num(q.get("price"), 2)
        pct = q.get("pct_change")
        pct_str = f"{float(pct):+.2f}%" if pct not in (None, "", "-") else "-"
        if d["triggered"]:
            dip_flag = f"🟢 **{d['hit_count']}/6**"
        else:
            dip_flag = f"⚪ {d['hit_count']}/6"
        if "exit_analysis" in r:
            ex = r["exit_analysis"]
            pnl = f"{ex['pnl_pct']:+.2f}%"
            action = ACTION_CN.get(ex["action"], ex["action"])
        else:
            pnl = "(无持仓)"
            action = "抄底候选" if d["triggered"] else "观望"
        rows.append(
            f"| {r['code']} | {r['name']} | {price} | {pct_str} | {dip_flag} | {pnl} | **{action}** |"
        )

    details = "\n".join(render_detail(r, news_root) for r in results)
    report = TEMPLATE.format(
        date=date,
        summary_rows="\n".join(rows),
        details=details,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(args.output_dir, f"{date}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[done] 报告骨架 -> {out}")
    print(f"       下一步:在对话里让 Claude 补全各股票的 AI 综合分析区块。")


if __name__ == "__main__":
    main()
