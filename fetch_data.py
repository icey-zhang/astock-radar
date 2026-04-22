"""
一分钟诊断脚本。

用法:python scripts/diagnose.py

检查:
    1. akshare 版本
    2. 对几个关键数据源域名的基本 HTTP 连通性
    3. 关键 akshare 接口的实际返回 (trade_cal / stock_hist / spot)

遇到全局失败时,先跑这个。
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime

OK = "\033[32mOK\033[0m"
WARN = "\033[33mWARN\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def pad(s: str, width: int = 50) -> str:
    return s + " " * max(0, width - len(s))


# ---------------- 1. akshare 版本 ----------------
print("=" * 60)
print(" Section 1. akshare 版本")
print("=" * 60)
try:
    import akshare as ak
    ver = getattr(ak, "__version__", "unknown")
    print(f"  akshare version: {ver}")
    if ver != "unknown":
        # 解析版本号大致判断
        try:
            major, minor, *_ = ver.split(".")
            if int(major) < 1 or (int(major) == 1 and int(minor) < 13):
                print(f"  {WARN} 版本偏旧,推荐 >= 1.14.0,请: pip install -U akshare")
            else:
                print(f"  {OK} 版本新度可接受")
        except Exception:
            print(f"  {WARN} 无法解析版本号")
except ImportError:
    print(f"  {FAIL} akshare 未安装")
    sys.exit(1)


# ---------------- 2. 网络连通性 ----------------
print()
print("=" * 60)
print(" Section 2. 网络连通性(TCP + HTTP 探活)")
print("=" * 60)

domains = [
    ("push2his.eastmoney.com",  "东财历史行情"),
    ("push2.eastmoney.com",     "东财实时行情"),
    ("datacenter-web.eastmoney.com", "东财数据中心"),
    ("hq.sinajs.cn",            "新浪行情"),
    ("finance.sina.com.cn",     "新浪财经"),
    ("www.baidu.com",           "基准(百度)"),
    ("github.com",              "基准(GitHub)"),
]

net_ok = 0
for host, desc in domains:
    line = f"  {pad(host, 36)} ({desc})"
    try:
        # TCP 连通性
        socket.setdefaulttimeout(5)
        socket.create_connection((host, 443), timeout=5).close()
        # HTTPS GET 基本响应
        req = urllib.request.Request(
            f"https://{host}/",
            headers={"User-Agent": "Mozilla/5.0 (diagnose)"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            code = resp.status
            body_len = len(resp.read(200))
        if code == 200 and body_len > 0:
            print(f"{line} -> {OK} (HTTP {code}, body {body_len}B)")
            net_ok += 1
        else:
            print(f"{line} -> {WARN} (HTTP {code}, body {body_len}B)")
    except (socket.timeout, socket.gaierror) as e:
        print(f"{line} -> {FAIL} TCP/DNS: {e}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 405):
            # 有些域名根路径 403/404 是正常的,说明网络通
            print(f"{line} -> {OK} (HTTP {e.code}, 路径不存在但网络通)")
            net_ok += 1
        else:
            print(f"{line} -> {WARN} HTTP {e.code}")
    except Exception as e:
        print(f"{line} -> {FAIL} {type(e).__name__}: {e}")


# ---------------- 3. akshare 接口实测 ----------------
print()
print("=" * 60)
print(" Section 3. 关键 akshare 接口实测")
print("=" * 60)


def test_api(name: str, func, *args, **kwargs):
    line = f"  {pad(name, 40)}"
    try:
        df = func(*args, **kwargs)
        if df is None:
            print(f"{line} -> {FAIL} 返回 None")
            return None
        n = len(df) if hasattr(df, "__len__") else 0
        if n == 0:
            print(f"{line} -> {WARN} 返回空结果")
            return df
        print(f"{line} -> {OK} {n} 行")
        return df
    except Exception as e:
        print(f"{line} -> {FAIL} {type(e).__name__}: {str(e)[:100]}")
        return None


# 交易日历
test_api("tool_trade_date_hist_sina()", ak.tool_trade_date_hist_sina)

# 单只股票日线(最稳的测试)
test_api(
    "stock_zh_a_hist(600519, 最近30天)",
    ak.stock_zh_a_hist,
    symbol="600519", period="daily",
    start_date=(datetime.now()).strftime("%Y%m%d"),
    end_date=datetime.now().strftime("%Y%m%d"),
    adjust="qfq",
)

# 实时全市场快照
spot = test_api("stock_zh_a_spot_em()", ak.stock_zh_a_spot_em)
if spot is not None and len(spot) > 0:
    print(f"       示例列名: {list(spot.columns)[:8]}")

# 个股资金流
test_api(
    "stock_individual_fund_flow(600519, sh)",
    ak.stock_individual_fund_flow, stock="600519", market="sh",
)

# 新闻
test_api(
    "stock_news_em(600519)",
    ak.stock_news_em, symbol="600519",
)


# ---------------- 总结 ----------------
print()
print("=" * 60)
print(" 诊断总结")
print("=" * 60)
if net_ok < 3:
    print(f"  ❗ 只有 {net_ok}/{len(domains)} 个域名可访问 → 网络问题居多")
    print("     - 如在 Claude Code Routine: 检查 Network allowlist 是否包含东财/新浪域名")
    print("     - 如在国外服务器:akshare 对非大陆 IP 限流严,考虑切到 Mac 本地")
    print("     - 如在大陆且走代理:akshare 目标是国内站点,别开全局代理,反而更慢/更不稳")
else:
    print(f"  ✓ {net_ok}/{len(domains)} 个域名可访问,网络没明显问题")
    print("     如果 akshare 接口仍失败,大概率是:")
    print("     1. akshare 版本过旧 → pip install -U akshare")
    print("     2. 东财/新浪接口临时变更 → 等几小时或升级 akshare")
    print("     3. User-Agent 被目标站识别拦截 → 较少见")
