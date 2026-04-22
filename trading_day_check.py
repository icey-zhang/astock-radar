#!/usr/bin/env bash
# A 股每日分析 - 一键运行
# 用法:bash scripts/run_daily.sh [--no-trading-check] [--with-news] [--with-searxng]

set -uo pipefail  # 注意:去掉了 -e,单只股票失败不应终止整条 pipeline

cd "$(dirname "$0")/.."

# 选择 Python 解释器:优先用环境变量,其次 python3,最后 python
PY="${PYTHON_BIN:-}"
if [ -z "$PY" ]; then
    if command -v python3 >/dev/null 2>&1; then PY=python3
    elif command -v python >/dev/null 2>&1; then PY=python
    else echo "ERROR: 未找到 python3/python"; exit 2
    fi
fi

CHECK_TRADING=1
FETCH_NEWS=0
USE_SEARXNG=""

for arg in "$@"; do
    case "$arg" in
        --no-trading-check) CHECK_TRADING=0 ;;
        --with-news) FETCH_NEWS=1 ;;
        --with-searxng) USE_SEARXNG="--use-searxng"; FETCH_NEWS=1 ;;
        *) echo "未知参数: $arg"; exit 2 ;;
    esac
done

TODAY=$(date +%Y-%m-%d)
echo "========================================"
echo " A 股每日分析 · ${TODAY}"
echo "========================================"

# 1. 交易日检查
if [ "$CHECK_TRADING" = "1" ]; then
    echo "[1/4] 检查是否为 A 股交易日..."
    if ! "$PY" scripts/trading_day_check.py; then
        echo "→ 今日非 A 股交易日,跳过。"
        echo "  (如需强制运行,加 --no-trading-check)"
        exit 0
    fi
fi

# 2. 拉行情数据
echo ""
echo "[2/4] 采集行情数据..."
"$PY" scripts/fetch_data.py --config config/stocks.yml --output-dir data

# 3. 可选:拉离线消息面
if [ "$FETCH_NEWS" = "1" ]; then
    echo ""
    echo "[2.5/4] 采集离线消息面..."
    "$PY" scripts/fetch_news.py --config config/stocks.yml --output-dir data $USE_SEARXNG
fi

# 4. 计算信号
echo ""
echo "[3/4] 计算技术指标与信号..."
"$PY" scripts/analyze_signals.py \
    --config config/stocks.yml \
    --positions config/positions.json \
    --data-dir data \
    --date "$TODAY"

# 5. 渲染 Markdown 报告骨架
echo ""
echo "[4/5] 渲染 Markdown 报告..."
"$PY" scripts/render_report.py \
    --signals "data/${TODAY}/signals.json" \
    --output-dir reports

# 6. 渲染 HTML 仪表盘
echo ""
echo "[5/5] 渲染 HTML 仪表盘..."
"$PY" scripts/render_dashboard.py \
    --signals "data/${TODAY}/signals.json" \
    --output-dir reports

echo ""
echo "========================================"
echo " 完成。两份报告:"
echo "   - Markdown:  reports/${TODAY}.md"
echo "   - HTML 仪表盘: reports/${TODAY}_dashboard.html"
echo "========================================"
echo ""
echo "下一步:"
echo "  (本地查看) open reports/${TODAY}_dashboard.html"
echo "  (AI 补全) 把 reports/${TODAY}.md 发给 Claude,说"
echo "    \"按 prompts/analyst_prompt.md 的结构,用 web_search 补充消息面,"
echo "      并填充每只股票的 AI 综合分析区块\""
