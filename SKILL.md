---
name: astock-daily-analysis
description: A 股每个交易日的智能分析 routine。对一组指定股票(在 config/stocks.yml 配置)采集行情/资金流/基本面,计算技术指标,识别"抄底"机会与"20% 止盈"信号,再结合消息面(web 检索)输出中文决策报告。当用户说"A股分析""今天帮我分析股票""盘后复盘""抄底""止盈"或给出 A 股代码(6 位数字,形如 600519/300750)时触发。
---

# A 股每日分析 Routine

这个 skill 的目的是把一套"可重复执行"的日内决策流程固化下来,让 Claude 每天都按同一标准处理。**Claude 不是投资顾问,本 skill 输出只是数据分析与信号识别,不构成交易建议。**

## 策略基线(写入逻辑,不要偏离)

**抄底候选判定**(命中越多信号越强,默认至少 3 个才算有效):
1. RSI(14) < 30 — 超卖
2. 收盘价 ≤ 布林带(20,2) 下轨 × 1.02 — 跌破下轨
3. KDJ 的 J < 0 — 极度超卖
4. 当日成交量 / 近 20 日均量 < 0.7 — 地量
5. 股价位于近 60 日价格区间底部 10% — 绝对低位
6. MACD 简化底背离 — 价格创近 60 日新低但 DIF 未同步新低

**止盈/止损**(仅对 `config/positions.json` 里的持仓生效):
- 浮盈 ≥ +20% → 建议**全部或分批止盈**(核心目标)
- 浮盈 ≥ +15% 且 RSI > 70 → 建议**部分止盈**(超买预警)
- 浮亏 ≤ -8% → 建议**止损**

**重要认知**:抄底信号只表示"统计意义上接近阶段底部的概率较高",**不等于一定是最低点**。必须配合消息面排除"戴维斯双杀"式基本面塌方。

## 执行步骤

每次用户触发(例如"跑一下今天的分析"),按顺序:

### 步骤 1:前置检查
```bash
python scripts/trading_day_check.py
```
返回码非 0 表示非交易日,此时告知用户并询问是否仍要跑(可能是补做昨日分析)。

### 步骤 2:采集行情数据
```bash
python scripts/fetch_data.py --config config/stocks.yml --output-dir data
```
产出 `data/YYYY-MM-DD/<code>.json`,每只股票含 250 日 K 线、实时快照、近 5 日主力资金流向、基本面。

### 步骤 3:计算技术指标与信号
```bash
python scripts/analyze_signals.py \
    --config config/stocks.yml \
    --positions config/positions.json \
    --data-dir data
```
产出 `data/YYYY-MM-DD/signals.json`,含每只股票的 `dip_analysis`(抄底打分) + `exit_analysis`(如有持仓)。

### 步骤 4:补充消息面(这一步 Claude 在对话里做)
对**每只股票**使用 `web_search` 检索最近 2 天内的:
- 公司公告、业绩/经营动态
- 所属行业的政策/景气度变化
- 近期券商研报观点
- 突发事件(诉讼、减持、重组、黑天鹅)

查询举例:`<股票名> 公告 2026年4月`、`<股票名> 业绩 最新`、`<所属行业> 政策`。
保持简洁 — 每只股票 1-2 次搜索即可,别无限展开。

> 如果用户已配置 searxng 或有离线新闻源,也可以改用 `python scripts/fetch_news.py` 采集,再让 Claude 基于结果综合。

### 步骤 5:综合判断(Claude 自己生成)
读取 `signals.json`,结合步骤 4 的消息面,对每只股票按 `prompts/analyst_prompt.md` 的结构输出:
1. **消息面摘要**(1-3 句,注明来源;无重大新闻就说"近日无重要新闻")
2. **技术面翻译**(把 `dip_analysis.signals` 翻译成人话)
3. **基本面速查**(PE/PB 相对历史/行业,简单一句)
4. **决策**:从 [强烈抄底候选 / 抄底候选 / 观望 / 持有 / 部分止盈 / 全部止盈 / 止损] 中选一个
5. **置信度**:低 / 中 / 高
6. **主要风险点**:1-3 条

规则:
- 浮盈 ≥ 20% 默认建议止盈,除非消息面明显超预期向好且无顶背离。
- 浮亏 ≤ -8% 默认建议止损,不论消息面。
- 抄底命中 ≥ 5 个信号 + 消息面无重大利空 → 可称"强烈抄底候选";仅命中 3-4 个 → "抄底候选";< 3 个 → "观望"。
- 明确区分"信号"与"确认" — 永远说"候选",不说"一定是最低点"。

### 步骤 6:渲染报告(Markdown + HTML)
```bash
python scripts/render_report.py --signals data/YYYY-MM-DD/signals.json --output-dir reports
python scripts/render_dashboard.py --signals data/YYYY-MM-DD/signals.json --output-dir reports
```
生成两份输出:
- `reports/YYYY-MM-DD.md` — Markdown 报告骨架,AI 综合分析区块以 `<!-- CLAUDE_FILL_START/END -->` 标识
- `reports/YYYY-MM-DD_dashboard.html` — 单文件 HTML 仪表盘,含 K 线图/均线/布林带/指标卡,浏览器直接打开

把 Markdown 的 AI 区块填完后,作为 artifact 返回给用户;同时把 HTML 仪表盘也作为可下载文件附上。如果用户在对话里要求"在仪表盘里也把 AI 分析填进去",可以指导用户打开 HTML 后在 console 调用 `window.fillAIAnalysis('600519', '<p>…</p>')`(或者 Claude 直接重写 HTML 把分析硬编码进去)。

## 一键模式

如果用户说"一键跑一下",等价于:
```bash
bash scripts/run_daily.sh
```
脚本执行步骤 1-3 + 6,同时产出 Markdown 和 HTML 仪表盘。步骤 4-5(消息面 + AI 综合分析)仍由 Claude 在对话里手动完成并把结果填入 Markdown 的 AI 区块。

## 输入/输出约定

- **输入**:`config/stocks.yml` 中的 `watchlist`(股票代码列表)+ `config/positions.json`(持仓)。
- **输出**:
  - 机器可读:`data/YYYY-MM-DD/signals.json`
  - 人可读 Markdown:`reports/YYYY-MM-DD.md`(最终交付给用户的 artifact)
  - 人可读 HTML 仪表盘:`reports/YYYY-MM-DD_dashboard.html`(浏览器打开,含 K 线图)

## 免责声明(每份报告顶部必带)

> 本报告由自动化工具 + AI 辅助生成,仅供学习研究参考,**不构成投资建议**。股市有风险,任何决策请独立判断并自负盈亏。Claude 不是金融顾问,"抄底"本身是高风险策略,信号命中不保证盈利。
