# A 股每日分析 Routine

一个"抄底 + 20% 止盈"策略的 A 股每日复盘工具。同一套目录既可作为 **Claude Skill** 在对话里调用,也可以作为独立 Python 项目放到 cron / GitHub Actions 里定时跑。

受参考项目 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 启发,但聚焦 A 股单一市场 + 抄底策略,大幅精简了配置。

## 🚀 快速开始

第一次使用 → 先读 **[`docs/SETUP.md`](docs/SETUP.md)**

推荐架构:
- **Claude Code Routine**(云端,每交易日 15:30)跑数据管道 + commit 报告骨架到 GitHub
- **Claude.ai Project**(你手动触发)读 signals.json → web_search 补消息面 → 综合分析 → 输出完整报告 artifact

两端的可粘贴配置文本:
- [`docs/routine_prompt.md`](docs/routine_prompt.md) — 粘到 Claude Code Routine 的 Prompt 框
- [`docs/project_instructions.md`](docs/project_instructions.md) — 粘到 Claude.ai Project 的 Custom Instructions

备用定时方案:[`/.github/workflows/daily.yml`](.github/workflows/daily.yml)(GitHub Actions) 或 Mac 本地 cron。

---

## ⚠️ 免责声明

本工具只做**数据采集 + 信号识别 + AI 辅助分析**,所有输出**不构成投资建议**。"抄底"本身是高风险策略,信号命中不保证盈利,浮盈 20% 止盈也只是一种常见纪律,不是铁律。股市有风险,决策请独立判断并自负盈亏。

---

## 目录结构

```
astock_daily_analysis/
├── SKILL.md                       ← Claude Skill 入口(触发词 + 步骤约束)
├── CLAUDE.md                      ← Claude Code 工作区入口(优先读 SKILL.md)
├── README.md                      ← 你正在看的文档
├── requirements.txt
├── config/
│   ├── stocks.yml                 ← 关注列表 + 策略参数(调这个)
│   └── positions.json             ← 手动维护的持仓记录(调这个,别 push 到 public repo)
├── scripts/
│   ├── trading_day_check.py       ← 判断是否 A 股交易日
│   ├── fetch_data.py              ← 采集行情 / 资金流 / 基本面
│   ├── fetch_news.py              ← 离线消息面(akshare + 可选 searxng)
│   ├── analyze_signals.py         ← 技术指标 + 抄底/止盈信号
│   ├── render_report.py           ← 渲染 Markdown 报告骨架
│   ├── render_dashboard.py        ← 渲染 HTML 仪表盘(K线/指标/决策)
│   └── run_daily.sh               ← 一键执行
├── prompts/
│   └── analyst_prompt.md          ← LLM 综合分析的 prompt 模板
├── docs/
│   ├── SETUP.md                   ← ★ 详细配置指南(先读这个)
│   ├── routine_prompt.md          ← 可粘贴到 Claude Code Routine
│   └── project_instructions.md    ← 可粘贴到 Claude.ai Project
├── .github/workflows/
│   └── daily.yml                  ← GitHub Actions 备用定时
├── data/                          ← (运行时生成)
└── reports/                       ← (运行时生成)
```

## 安装

```bash
cd astock_daily_analysis
python -m venv .venv && source .venv/bin/activate    # 可选
pip install -r requirements.txt
```

Mac/Apple Silicon:akshare 会自动装好;个别依赖(如 pyarrow)若有问题,直接 `pip install --no-binary :all: <包>` 跳过二进制。

## 核心策略

### 抄底候选(6 个信号,命中 ≥ 3 个算有效)

| 信号 | 含义 |
|------|------|
| RSI(14) < 30 | 超卖 |
| 收盘价 ≤ 布林带(20,2) 下轨 × 1.02 | 跌破下轨 |
| KDJ 的 J < 0 | 极度超卖 |
| 当日量 / 20 日均量 < 0.7 | 地量 |
| 股价位于近 60 日区间底部 10% | 绝对低位 |
| MACD 简化底背离 | 价创新低但 DIF 未同步 |

### 止盈/止损(仅对 `positions.json` 里的持仓生效)

| 条件 | 动作 |
|------|------|
| 浮盈 ≥ +20% | **全部止盈**(核心目标) |
| 浮盈 ≥ +15% 且 RSI > 70 | 部分止盈 |
| 浮亏 ≤ -8% | 止损 |
| 出现 MACD 顶背离 + 浮盈 > +5% | 部分止盈预警 |

策略参数都在 `config/stocks.yml` 的 `strategy` 下,自己改。

## 使用

### 方式一:一键脚本(推荐作为 cron/Actions 入口)

```bash
# 标准执行(只拉行情 + 算信号,消息面由 Claude 对话补)
bash scripts/run_daily.sh

# 同时拉离线消息面(akshare 东财新闻)
bash scripts/run_daily.sh --with-news

# 用自建 searxng 做全网搜索
export SEARXNG_BASE_URL=https://your-searxng.example.com
bash scripts/run_daily.sh --with-searxng
```

产出:
- `data/YYYY-MM-DD/<code>.json` — 每只股票的原始数据
- `data/YYYY-MM-DD/signals.json` — 全部信号评估结果
- `reports/YYYY-MM-DD.md` — Markdown 报告**骨架**(AI 综合分析区块待填)
- `reports/YYYY-MM-DD_dashboard.html` — **HTML 仪表盘**(单文件,浏览器打开即可查看,含 K 线+均线+布林带+指标卡)

HTML 仪表盘特点:
- 单文件自包含(内嵌数据 + ECharts via jsDelivr CDN)
- A 股习惯配色:**红涨绿跌**
- 每只股票一张卡片:顶部决策标签 + K 线图(60 日,可缩放) + 11 项关键指标 + 抄底信号命中列表 + 持仓状态(如有) + AI 分析占位
- 响应式:桌面双列,手机单列
- AI 分析区块可通过浏览器 console 调用 `window.fillAIAnalysis('600519', '<p>...</p>')` 填入,或让 Claude 直接重写 HTML

### 方式二:当作 Claude Skill 在对话里用

把整个目录丢给 Claude(或者放到 Claude Code 的项目目录),然后说:

> "跑一下今天的 A 股分析"

Claude 会按 `SKILL.md` 的流程:
1. 执行交易日检查
2. 跑 `fetch_data.py` + `analyze_signals.py`
3. 对每只**命中抄底**或**触发止盈**的股票做 `web_search` 查最近消息
4. 按 `prompts/analyst_prompt.md` 填充每只股票的综合分析
5. 生成完整报告并作为 artifact 输出

### 方式三:GitHub Actions 定时(模板)

```yaml
# .github/workflows/daily.yml
on:
  schedule:
    - cron: '30 7 * * 1-5'   # UTC 07:30 = 北京 15:30(A股收盘后)
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: bash scripts/run_daily.sh --with-news
      - uses: actions/upload-artifact@v4
        with:
          name: report-${{ github.run_number }}
          path: reports/
```

## 每日使用流程示例

```bash
# 早上开盘前 / 下午收盘后
$ bash scripts/run_daily.sh
========================================
 A 股每日分析 · 2026-04-22
========================================
[1/4] 检查是否为 A 股交易日...
2026-04-22 is_trading_day=True
[2/4] 采集行情数据...
[fetch] 600519 贵州茅台 ...
[fetch] 300750 宁德时代 ...
...
[3/4] 计算技术指标与信号...
代码      名称        抄底        盈亏          动作
------------------------------------------------------------
600519  贵州茅台    ⚪2/6      +8.42%       HOLD
300750  宁德时代    🟢3/6      -           抄底候选
002594  比亚迪      🟢4/6      -           抄底候选
...
[4/4] 渲染 Markdown 报告骨架...
[done] 报告骨架 -> reports/2026-04-22.md
```

然后在 Claude 对话里:

> 读 `reports/2026-04-22.md` 和 `data/2026-04-22/signals.json`,
> 按 `prompts/analyst_prompt.md` 补充 AI 综合分析区块,
> 重点关注抄底候选和持仓止盈信号。

## 维护

### 改关注的股票
编辑 `config/stocks.yml` 的 `watchlist`。代码必须是 6 位数字字符串(沪市 6/9 开头,深市 0/3 开头)。

### 改持仓
编辑 `config/positions.json`:
```json
{
  "positions": [
    {"code": "600519", "cost_price": 1680.00, "shares": 100, "entry_date": "2025-11-20"}
  ]
}
```
`cost_price` 应为含手续费的成本均价。没持仓的股票就不要放进去。

### 调策略灵敏度
`config/stocks.yml` 里的 `strategy`:
- 想更保守(少误报)→ 把 `dip_buy.min_signals` 从 3 调到 4 或 5
- 想更激进(更早抄底)→ 调到 2
- 止盈目标改 30%?→ `exit.take_profit_pct: 0.30`

## 常见问题

**Q: 能在中国大陆直连吗?**
A: akshare 用的是国内数据源(东财、新浪、同花顺等),不需要翻墙。但 `pip install akshare` 可能需要镜像:
```bash
pip install -i https://mirrors.aliyun.com/pypi/simple/ akshare
```

**Q: 数据源不稳定怎么办?**
A: akshare 偶尔会因为第三方接口变更失败。脚本里已经对关键接口加了 try/except,失败的股票会跳过,继续处理其他的。

**Q: 我想加港股/美股?**
A: 参考项目支持多市场,本 routine 聚焦 A 股。要扩展的话,改 `fetch_data.py` 里的接口(akshare 的 `stock_hk_hist` / `stock_us_daily`),并在 `stocks.yml` 加市场字段即可。

**Q: 信号命中 6/6 就一定该买吗?**
A: **不。** 这只是"技术面上接近底部区域的概率较高",必须配合消息面排除基本面塌方(商誉爆雷、监管处罚、行业政策反转等)。Claude 在综合分析区块就是做这件事。

## 致谢

- 参考项目:[ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) —— 多市场 + 多 LLM 渠道的完整版
- 数据源:[akshare](https://github.com/akfamily/akshare) —— 国内金融数据统一接口
