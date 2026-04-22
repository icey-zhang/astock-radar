"""
判断今天是否为 A 股交易日。
退出码:
    0 - 是交易日
    1 - 不是交易日(周末 / 节假日)
    2 - 环境错误(akshare 未安装)
"""
from __future__ import annotations

import sys
from datetime import datetime


def is_trading_day(date_str: str | None = None) -> bool:
    """判断指定日期是否为 A 股交易日。date_str: 'YYYY-MM-DD',None 表示今天。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        import akshare as ak
    except ImportError:
        print("ERROR: 需要 akshare。pip install akshare", file=sys.stderr)
        sys.exit(2)

    try:
        cal = ak.tool_trade_date_hist_sina()
        cal["trade_date"] = cal["trade_date"].astype(str)
        return date_str in set(cal["trade_date"])
    except Exception as e:
        # 网络异常或接口变更 -> 退回为"非周末"的弱判断
        print(f"WARN: akshare 交易日历接口失败 ({e}),退回周末规则", file=sys.stderr)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() < 5


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    today = target or datetime.now().strftime("%Y-%m-%d")
    ok = is_trading_day(today)
    print(f"{today} is_trading_day={ok}")
    sys.exit(0 if ok else 1)
