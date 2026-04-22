"""
用 akshare 采集 A 股市场数据,输出每只股票一份 JSON 到 data/YYYY-MM-DD/<code>.json。

包含:
    - 日线(前复权,默认 250 个交易日)
    - 实时快照(当日涨跌幅、量价、PE/PB、总市值)
    - 主力资金流向(近 5 日)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any

import yaml

try:
    import akshare as ak
except ImportError:
    print("ERROR: 需要 akshare。pip install akshare", file=sys.stderr)
    sys.exit(2)


# ------------------------------ 工具 ------------------------------
def _guess_market(code: str) -> str:
    """粗略判断沪/深。主板/科创板用 sh,深市主板/创业板用 sz。"""
    if code.startswith(("5", "6", "9", "11", "13")):
        return "sh"
    return "sz"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


# ------------------------------ 数据源 ------------------------------
def fetch_kline(code: str, days: int = 250) -> list[dict]:
    """日线(前复权)。"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    df = ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date=start, end_date=end, adjust="qfq",
    )
    rename_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
        "换手率": "turnover", "涨跌幅": "pct_change",
        "振幅": "amplitude",
    }
    df = df.rename(columns=rename_map)
    keep = [c for c in
            ["date", "open", "close", "high", "low",
             "volume", "amount", "turnover", "pct_change", "amplitude"]
            if c in df.columns]
    df = df[keep].tail(days)
    df["date"] = df["date"].astype(str)
    return df.to_dict(orient="records")


_SPOT_CACHE: dict | None = None


def fetch_quote(code: str) -> dict[str, Any]:
    """实时行情快照(全市场缓存一次)。"""
    global _SPOT_CACHE
    if _SPOT_CACHE is None:
        try:
            df = ak.stock_zh_a_spot_em()
            _SPOT_CACHE = {str(r["代码"]): r for _, r in df.iterrows()}
        except Exception as e:
            print(f"WARN fetch_quote spot: {e}", file=sys.stderr)
            _SPOT_CACHE = {}

    r = _SPOT_CACHE.get(code)
    if r is None:
        return {}
    return {
        "code": code,
        "name": str(r.get("名称", "")),
        "price": _safe_float(r.get("最新价")),
        "pct_change": _safe_float(r.get("涨跌幅")),
        "change": _safe_float(r.get("涨跌额")),
        "volume": _safe_float(r.get("成交量")),
        "amount": _safe_float(r.get("成交额")),
        "amplitude": _safe_float(r.get("振幅")),
        "high": _safe_float(r.get("最高")),
        "low": _safe_float(r.get("最低")),
        "open": _safe_float(r.get("今开")),
        "prev_close": _safe_float(r.get("昨收")),
        "turnover": _safe_float(r.get("换手率")),
        "pe_ttm": _safe_float(r.get("市盈率-动态")),
        "pb": _safe_float(r.get("市净率")),
        "market_cap": _safe_float(r.get("总市值")),
        "float_cap": _safe_float(r.get("流通市值")),
    }


def fetch_money_flow(code: str, days: int = 5) -> list[dict]:
    """个股主力资金流向(近 N 日)。"""
    try:
        df = ak.stock_individual_fund_flow(stock=code, market=_guess_market(code))
        df = df.tail(days)
        # 统一列名(akshare 会返回中文列名)
        out = []
        for _, row in df.iterrows():
            out.append({
                "date": str(row.get("日期", "")),
                "close": _safe_float(row.get("收盘价")),
                "pct_change": _safe_float(row.get("涨跌幅")),
                "main_net_inflow": _safe_float(row.get("主力净流入-净额")),
                "main_net_inflow_pct": _safe_float(row.get("主力净流入-净占比")),
                "super_large_net": _safe_float(row.get("超大单净流入-净额")),
                "large_net": _safe_float(row.get("大单净流入-净额")),
            })
        return out
    except Exception as e:
        print(f"WARN fetch_money_flow({code}): {e}", file=sys.stderr)
        return []


# ------------------------------ 主流程 ------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/stocks.yml")
    ap.add_argument("--output-dir", default="data")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    rt = cfg.get("runtime", {})
    days = rt.get("kline_days", 250)
    mf_days = rt.get("money_flow_days", 5)

    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(args.output_dir, today)
    os.makedirs(out_dir, exist_ok=True)

    fail = []
    for s in cfg["watchlist"]:
        code = str(s["code"]).zfill(6)
        name = s.get("name", "")
        print(f"[fetch] {code} {name} ...", flush=True)
        try:
            payload = {
                "code": code,
                "name": name,
                "fetched_at": datetime.now().isoformat(),
                "kline": fetch_kline(code, days=days),
                "quote": fetch_quote(code),
                "money_flow": fetch_money_flow(code, days=mf_days),
            }
            with open(os.path.join(out_dir, f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            fail.append(code)

    if fail:
        print(f"[partial] 失败 {len(fail)} 只: {fail}", file=sys.stderr)
        sys.exit(1)
    print(f"[done] 数据已保存到 {out_dir}")


if __name__ == "__main__":
    main()
