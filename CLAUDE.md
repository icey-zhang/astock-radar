# A股每日分析 · Claude Code 工作区

这是一个 A 股每日复盘 routine。当你作为 Claude Code agent(CLI / Desktop / Routine)在这个目录下运行时,按以下顺序工作:

## 优先读取的文件

1. **`SKILL.md`** — 完整的 skill 说明(触发条件、6 步执行流程、硬规则)
2. **`prompts/analyst_prompt.md`** — LLM 综合分析的 prompt 模板(决策分档、硬规则)
3. **`config/stocks.yml`** — 关注列表 + 策略参数
4. **`config/positions.json`** — 持仓记录(用于止盈判断)

## 执行模式

### 作为 Routine(定时任务)运行时

执行 `docs/routine_prompt.md` 里定义的"仅数据管道"流程:
1. `python scripts/trading_day_check.py`(非交易日直接退出)
2. `bash scripts/run_daily.sh --with-news`(拉数据、算信号、生成 MD/HTML 骨架)
3. git add / commit / push 到 reports 分支

**不**做 AI 综合分析 — 那是 Claude.ai Project 的事。Routine 保持轻量、确定性。

### 作为交互式会话(CLI / Desktop)运行时

可以做端到端完整闭环:数据采集 → 信号计算 → web_search 补消息面 → 综合分析 → 产出报告 artifact。

## 硬规则(不得违反)

- 浮盈 ≥ +20% 默认建议全部止盈(除非消息面明显超预期 + 无顶背离)
- 浮亏 ≤ -8% 默认止损(不看消息面)
- "抄底候选" ≠ "最低点确认" — 永远用概率性表述
- 不给仓位百分比建议,不推荐 watchlist 外股票
- 所有结论附 `⚠ 仅供参考,不构成投资建议`

## 配置指南

第一次用 → 看 [`docs/SETUP.md`](docs/SETUP.md)
