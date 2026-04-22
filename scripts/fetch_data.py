"""
A 股数据采集 v2.0 - 分层数据模型(针对云端 IP 限流优化)

数据结构:
    close_history  - 250 个交易日,只要 date + close(轻量,用于 MA60/RSI/MACD/长期分位)
    kline_30d      - 最近 30 交易日完整 OHLCV(用于 KDJ/布林带/量比/仪表盘 K 线图)
    quote          - 当日实时快照(用于现价/涨跌幅/PE/PB)
    money_flow     - 近 5 天主力资金流(非关键,失败不阻塞)

减少请求:
    - 用 data/history/ 缓存,首次全量后只拉增量
    - 30 天 K 线从 close_history 衍生(同一次请求获得的字段),0 额外请求
    - 股票之间 sleep 3-7 秒抖动,降低限流触发

参考 akshare issue #6100 官方推荐:增量更新,本地维护历史
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

import yaml

try:
    import akshare as ak
except ImportError:
    print("ERROR: 需要 akshare。pip install akshare", file=sys.stderr)
    sys.exit(2)


VERBOSE = False
RATE_LIMIT_BACKOFF = 30.0  # 限流基础退避


# ------------------------------ 工具 ------------------------------
def _guess_market(code: str) -> str:
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


def _is_rate_limit_err(exc: Exception) -> bool:
    s = str(exc).lower()
    markers = ["remotedisconnected", "connection aborted", "connection reset",
               "403", "too many requests", "429", "timed out", "timeout"]
    return any(m in s for m in markers)


def with_retry(fn: Callable, *args, name: str = "call", tries: int = 3, **kwargs):
    """指数退避重试。限流错误用更长退避。"""
    last_err: Exception | None = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if VERBOSE:
                print(f"    [retry] {name} attempt {i+1}/{tries}: "
                      f"{type(e).__name__}: {str(e)[:180]}", file=sys.stderr)
            if i < tries - 1:
                if _is_rate_limit_err(e):
                    backoff = RATE_LIMIT_BACKOFF * (i + 1) + random.uniform(0, 10)
                    print(f"    [retry] 检测到限流,等待 {backoff:.0f}s...", file=sys.stderr)
                    time.sleep(backoff)
                else:
                    time.sleep((2 ** i) + random.random())
    assert last_err is not None
    raise last_err


# ============================== 历史缓存 ==============================
def load_history(history_dir: str, code: str) -> list[dict]:
    """读取本地缓存的完整日线历史(含 OHLCV)。"""
    path = os.path.join(history_dir, f"{code}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("kline", [])
    except Exception as e:
        if VERBOSE:
            print(f"    [cache] load failed: {e}", file=sys.stderr)
        return []


def save_history(history_dir: str, code: str, kline: list[dict]) -> None:
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, f"{code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "updated_at": datetime.now().isoformat(),
                   "kline": kline}, f, ensure_ascii=False, indent=2)


# ============================== 核心:增量拉取日线 ==============================
def fetch_kline_range(code: str, start: str, end: str) -> list[dict]:
    """拉指定区间的日线(前复权)。双源 fallback。"""
    errors: list[str] = []

    try:
        df = with_retry(
            ak.stock_zh_a_hist,
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq",
            name=f"stock_zh_a_hist({code},{start}-{end})",
        )
        if df is not None and len(df) > 0:
            return _format_kline_em(df)
        errors.append("stock_zh_a_hist 返回空")
    except Exception as e:
        errors.append(f"stock_zh_a_hist: {type(e).__name__}: {str(e)[:120]}")

    try:
        prefix = _guess_market(code)
        df = with_retry(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{code}", adjust="qfq",
            start_date=start, end_date=end,
            name=f"stock_zh_a_daily({prefix}{code})",
            tries=2,
        )
        if df is not None and len(df) > 0:
            return _format_kline_sina(df)
        errors.append("stock_zh_a_daily 返回空")
    except Exception as e:
        errors.append(f"stock_zh_a_daily: {type(e).__name__}: {str(e)[:120]}")

    raise RuntimeError(f"所有日线接口失败 | {' | '.join(errors)}")


def fetch_kline_incremental(code: str, days: int, history_dir: str) -> list[dict]:
    """增量获取:已有缓存时只拉增量,无缓存时首次全量。"""
    today_dt = datetime.now()
    today = today_dt.strftime("%Y%m%d")
    history = load_history(history_dir, code)

    if not history:
        # 首次冷启动:拉 days*1.6 天历史(保守一些,含周末/假期)
        start = (today_dt - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        print(f"  [cold] 首次拉取 {code} 全量 {start}-{today}", file=sys.stderr)
        kline = fetch_kline_range(code, start, today)
        save_history(history_dir, code, kline)
        return kline[-days:] if len(kline) > days else kline

    # 已有缓存:从最后一天重拉(覆盖最后一天的数据 + 拉新增)
    last_date_str = history[-1]["date"]
    last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
    start = last_date.strftime("%Y%m%d")

    if start == today:
        # 缓存里已有今天 → 只刷新今日最新数据
        try:
            today_k = fetch_kline_range(code, start, today)
            if today_k:
                history[-1] = today_k[-1]
                save_history(history_dir, code, history)
        except Exception as e:
            print(f"  [warn] 今日数据刷新失败: {str(e)[:100]}", file=sys.stderr)
        return history[-days:] if len(history) > days else history

    # 拉增量
    print(f"  [incr] 增量拉取 {code} {start}-{today}", file=sys.stderr)
    new_k = fetch_kline_range(code, start, today)
    existing_dates = {k["date"] for k in history[:-1]}
    merged = list(history[:-1])
    for k in new_k:
        if k["date"] not in existing_dates:
            merged.append(k)
            existing_dates.add(k["date"])
    merged.sort(key=lambda x: x["date"])
    save_history(history_dir, code, merged)
    return merged[-days:] if len(merged) > days else merged


def _format_kline_em(df) -> list[dict]:
    rename_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
        "换手率": "turnover", "涨跌幅": "pct_change", "振幅": "amplitude",
    }
    df = df.rename(columns=rename_map)
    keep = [c for c in ["date", "open", "close", "high", "low",
                        "volume", "amount", "turnover", "pct_change", "amplitude"]
            if c in df.columns]
    df = df[keep].copy()
    df["date"] = df["date"].astype(str)
    return df.to_dict(orient="records")


def _format_kline_sina(df) -> list[dict]:
    if "date" not in df.columns and df.index.name in ("date", None):
        df = df.reset_index()
    df = df.copy()
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
    keep = [c for c in ["date", "open", "close", "high", "low", "volume"] if c in df.columns]
    return df[keep].to_dict(orient="records")


# ============================== 分层数据构造 ==============================
def build_close_history(kline_full: list[dict], days: int) -> list[dict]:
    """只提取 {date, close}。轻量,用于长周期技术指标。"""
    tail = kline_full[-days:] if len(kline_full) > days else kline_full
    return [{"date": k["date"], "close": k["close"]} for k in tail]


def build_kline_detail(kline_full: list[dict], days: int) -> list[dict]:
    """近 N 天完整 OHLCV。用于短周期技术指标和 K 线图。"""
    tail = kline_full[-days:] if len(kline_full) > days else kline_full
    # 保留必要字段
    out = []
    for k in tail:
        out.append({
            "date": k["date"],
            "open": k.get("open"),
            "close": k.get("close"),
            "high": k.get("high"),
            "low": k.get("low"),
            "volume": k.get("volume"),
            "pct_change": k.get("pct_change"),
            "turnover": k.get("turnover"),
        })
    return out


# ============================== 实时快照 ==============================
_SPOT_CACHE: dict | None = None


def fetch_quote(code: str) -> dict[str, Any]:
    global _SPOT_CACHE
    if _SPOT_CACHE is None:
        try:
            df = with_retry(ak.stock_zh_a_spot_em, name="stock_zh_a_spot_em")
            _SPOT_CACHE = {str(r["代码"]): r for _, r in df.iterrows()}
        except Exception as e:
            print(f"  WARN spot cache failed: {type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)
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


# ============================== 资金流(非关键) ==============================
def fetch_money_flow(code: str, days: int = 5) -> list[dict]:
    try:
        df = with_retry(
            ak.stock_individual_fund_flow,
            stock=code, market=_guess_market(code),
            name=f"stock_individual_fund_flow({code})",
            tries=2,
        )
        df = df.tail(days)
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
        print(f"  WARN money_flow({code}) 跳过: {type(e).__name__}: {str(e)[:100]}",
              file=sys.stderr)
        return []


# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/stocks.yml")
    ap.add_argument("--output-dir", default="data")
    ap.add_argument("--history-dir", default="data/history")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--min-success-rate", type=float, default=0.5)
    ap.add_argument("--no-money-flow", action="store_true")
    ap.add_argument("--inter-sleep-min", type=float, default=3.0)
    ap.add_argument("--inter-sleep-max", type=float, default=7.0)
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    rt = cfg.get("runtime", {})
    close_days = rt.get("close_history_days", 250)
    detail_days = rt.get("kline_detail_days", 30)
    mf_days = rt.get("money_flow_days", 5)

    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(args.output_dir, today)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(args.history_dir, exist_ok=True)

    total = len(cfg["watchlist"])
    ok_list: list[str] = []
    fail_list: list[tuple[str, str]] = []

    for idx, s in enumerate(cfg["watchlist"]):
        code = str(s["code"]).zfill(6)
        name = s.get("name", "")
        print(f"[fetch {idx+1}/{total}] {code} {name} ...", flush=True)

        if idx > 0:
            sleep_s = random.uniform(args.inter_sleep_min, args.inter_sleep_max)
            if VERBOSE:
                print(f"  [wait] 随机等待 {sleep_s:.1f}s...", file=sys.stderr)
            time.sleep(sleep_s)

        try:
            # 只请求一次:拿 max(close_days, detail_days) 的完整数据,然后分层裁剪
            full_kline = fetch_kline_incremental(
                code, days=max(close_days, detail_days) + 10,
                history_dir=args.history_dir,
            )
            if not full_kline:
                raise RuntimeError("日线为空")

            payload = {
                "code": code,
                "name": name,
                "fetched_at": datetime.now().isoformat(),
                "close_history": build_close_history(full_kline, close_days),
                "kline_30d": build_kline_detail(full_kline, detail_days),
                "quote": fetch_quote(code),
                "money_flow": [] if args.no_money_flow else fetch_money_flow(code, days=mf_days),
            }
            with open(os.path.join(out_dir, f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            ok_list.append(code)
            print(f"  -> 成功 (close 历史 {len(payload['close_history'])} 天, "
                  f"详细 {len(payload['kline_30d'])} 天)")
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            fail_list.append((code, reason))
            print(f"  -> 失败:{reason}", file=sys.stderr)
            if VERBOSE:
                traceback.print_exc()

    print()
    print("=" * 60)
    print(f"[summary] 成功 {len(ok_list)}/{total},失败 {len(fail_list)}/{total}")
    if fail_list:
        print(f"[summary] 失败列表:")
        for code, reason in fail_list:
            print(f"  - {code}: {reason}")

    success_rate = len(ok_list) / total if total else 0
    if success_rate < args.min_success_rate:
        print(f"\n❗ 成功率 {success_rate*100:.0f}% 低于阈值 {args.min_success_rate*100:.0f}%",
              file=sys.stderr)
        sys.exit(1)

    print(f"[done] 数据 -> {out_dir}  |  历史 -> {args.history_dir}")


if __name__ == "__main__":
    main()
