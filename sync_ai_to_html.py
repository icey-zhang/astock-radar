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

    # 优先用 akshare 交易日历
    try:
        cal = ak.tool_trade_date_hist_sina()
        if cal is not None and len(cal) > 0:
            cal["trade_date"] = cal["trade_date"].astype(str)
            dates = set(cal["trade_date"])
            return date_str in dates
        raise ValueError("交易日历返回空")
    except Exception as e:
        # 交易日历接口失败 -> 退回"非周末"的弱判断
        # 注意:遇到春节、国庆等会误判,但比 crash 好
        print(f"WARN: akshare 交易日历接口失败 ({type(e).__name__}: {str(e)[:80]}),"
              f"退回周末规则(可能误判法定节假日)", file=sys.stderr)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() < 5


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    today = target or datetime.now().strftime("%Y-%m-%d")
    ok = is_trading_day(today)
    print(f"{today} is_trading_day={ok}")
    sys.exit(0 if ok else 1)
