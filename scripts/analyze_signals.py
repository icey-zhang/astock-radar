"""
技术指标 + 抄底/止盈信号评估。
读取 fetch_data.py 生成的 JSON,输出 signals.json。

关键策略:
    抄底(dip_buy)  -> 6 个信号,命中数 >= min_signals 判定为抄底候选
    止盈(take_profit) -> 浮盈 >= 20% 触发(核心目标)
    止损(stop_loss)  -> 浮亏 >= 8% 触发
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import yaml


# ============================== 技术指标 ==============================
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger(close: pd.Series, period: int = 20, std: float = 2.0):
    mid = close.rolling(period).mean()
    s = close.rolling(period).std()
    return mid - std * s, mid, mid + std * s  # lower, mid, upper


def kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    rng = (highest - lowest).replace(0, np.nan)
    rsv = (close - lowest) / rng * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


# ============================== 信号判定 ==============================
def evaluate_dip_signals(df: pd.DataFrame, sp: dict) -> dict[str, Any]:
    """抄底信号评估。返回命中的信号列表 + 分数 + 是否触发。"""
    close = df["close"]
    vol = df["volume"]
    last_close = float(close.iloc[-1])

    # --- 指标计算 ---
    rsi_v = rsi(close, sp.get("rsi_period", 14))
    last_rsi = rsi_v.iloc[-1]

    bb_lo, bb_mid, bb_hi = bollinger(close, sp["bbands_period"], sp["bbands_std"])
    last_bb_lo = bb_lo.iloc[-1]

    k, d, j = kdj(df["high"], df["low"], close)
    last_k, last_d, last_j = k.iloc[-1], d.iloc[-1], j.iloc[-1]

    dif, dea, hist = macd(close)

    # 地量
    vol_ratio = vol.iloc[-1] / vol.iloc[-20:].mean() if len(vol) >= 20 else 1.0

    # 近 N 日低位
    window = sp["low_position_window"]
    recent = close.iloc[-window:] if len(close) >= window else close
    rng_min, rng_max = float(recent.min()), float(recent.max())
    low_band = rng_min + (rng_max - rng_min) * sp["low_position_pct"]

    # 均线与价格偏离
    ma20 = ma(close, 20).iloc[-1]
    ma60 = ma(close, 60).iloc[-1]

    # --- 信号判定 ---
    signals: list[str] = []
    details = {
        "close": round(last_close, 3),
        "rsi_14": round(float(last_rsi), 2) if not np.isnan(last_rsi) else None,
        "bb_lower": round(float(last_bb_lo), 3) if not np.isnan(last_bb_lo) else None,
        "bb_mid": round(float(bb_mid.iloc[-1]), 3) if not np.isnan(bb_mid.iloc[-1]) else None,
        "kdj_k": round(float(last_k), 2) if not np.isnan(last_k) else None,
        "kdj_d": round(float(last_d), 2) if not np.isnan(last_d) else None,
        "kdj_j": round(float(last_j), 2) if not np.isnan(last_j) else None,
        "macd_dif": round(float(dif.iloc[-1]), 3),
        "macd_dea": round(float(dea.iloc[-1]), 3),
        "macd_hist": round(float(hist.iloc[-1]), 3),
        "volume_ratio_20d": round(float(vol_ratio), 2),
        "ma20": round(float(ma20), 3) if not np.isnan(ma20) else None,
        "ma60": round(float(ma60), 3) if not np.isnan(ma60) else None,
        f"low_{window}d_min": round(rng_min, 3),
        f"low_{window}d_band": round(low_band, 3),
    }

    # 1) RSI 超卖
    if not np.isnan(last_rsi) and last_rsi < sp["rsi_threshold"]:
        signals.append(f"RSI={last_rsi:.1f} < {sp['rsi_threshold']} (超卖)")

    # 2) 跌破布林带下轨
    if not np.isnan(last_bb_lo) and last_close <= last_bb_lo * sp["bbands_tolerance"]:
        signals.append(f"收盘 {last_close:.2f} ≤ 布林带下轨 {last_bb_lo:.2f} × {sp['bbands_tolerance']}")

    # 3) KDJ J 极度超卖
    if not np.isnan(last_j) and last_j < sp["kdj_j_threshold"]:
        signals.append(f"KDJ J={last_j:.1f} < {sp['kdj_j_threshold']} (极度超卖)")

    # 4) 地量
    if vol_ratio < sp["low_volume_ratio"]:
        signals.append(f"地量:当日量/20日均量 = {vol_ratio:.2f} < {sp['low_volume_ratio']}")

    # 5) 低位区间
    if last_close <= low_band:
        signals.append(
            f"位于近 {window} 日底部 {int(sp['low_position_pct']*100)}% 区间 "
            f"(当前 {last_close:.2f} / 低点 {rng_min:.2f})"
        )

    # 6) MACD 简化底背离:价格创近 N 日新低,但 DIF 未同步新低
    if len(close) >= window:
        recent_slice = close.iloc[-window:]
        recent_dif = dif.iloc[-window:]
        if last_close <= recent_slice.min() * 1.01 and recent_dif.iloc[-1] > recent_dif.min():
            signals.append("MACD 简化底背离:价近新低,DIF 未同步")

    score = round(100 * len(signals) / 6)
    triggered = len(signals) >= sp.get("min_signals", 3)

    return {
        "signals": signals,
        "hit_count": len(signals),
        "total": 6,
        "score": score,
        "triggered": triggered,
        "details": details,
    }


def evaluate_exit_signals(df: pd.DataFrame, position: dict, sp: dict) -> dict[str, Any]:
    """止盈/止损评估。"""
    close = df["close"]
    last_close = float(close.iloc[-1])
    cost = float(position["cost_price"])
    pct = (last_close - cost) / cost if cost > 0 else 0.0

    rsi_v = rsi(close, 14)
    last_rsi = float(rsi_v.iloc[-1]) if not np.isnan(rsi_v.iloc[-1]) else None

    # 顶背离(简版):价格创近 20 日新高,DIF 未同步
    dif, dea, hist = macd(close)
    top_divergence = False
    if len(close) >= 20:
        recent_hi = close.iloc[-20:].max()
        recent_dif_hi = dif.iloc[-20:].max()
        if last_close >= recent_hi * 0.99 and dif.iloc[-1] < recent_dif_hi * 0.95:
            top_divergence = True

    signals: list[str] = []
    action = "HOLD"

    if pct >= sp["take_profit_pct"]:
        signals.append(f"浮盈 {pct*100:+.2f}% ≥ {int(sp['take_profit_pct']*100)}% 止盈目标")
        action = "TAKE_PROFIT"
    elif (pct >= sp["aggressive_tp_pct"]
          and last_rsi is not None and last_rsi > sp["rsi_overbought"]):
        signals.append(
            f"浮盈 {pct*100:+.2f}% ≥ {int(sp['aggressive_tp_pct']*100)}% 且 RSI={last_rsi:.1f} 超买"
        )
        action = "TAKE_PROFIT_PARTIAL"
    elif pct <= -sp["stop_loss_pct"]:
        signals.append(f"浮亏 {pct*100:+.2f}% ≤ -{int(sp['stop_loss_pct']*100)}% 止损触发")
        action = "STOP_LOSS"

    if top_divergence and action == "HOLD" and pct > 0.05:
        signals.append("MACD 简化顶背离预警 (参考)")
        action = "TAKE_PROFIT_PARTIAL"

    return {
        "cost_price": cost,
        "current_price": last_close,
        "shares": position.get("shares", 0),
        "entry_date": position.get("entry_date", ""),
        "pnl_pct": round(pct * 100, 2),
        "pnl_amount": round((last_close - cost) * position.get("shares", 0), 2),
        "rsi_14": round(last_rsi, 2) if last_rsi is not None else None,
        "top_divergence": top_divergence,
        "signals": signals,
        "action": action,
    }


# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/stocks.yml")
    ap.add_argument("--positions", default="config/positions.json")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sp_dip = cfg["strategy"]["dip_buy"]
    sp_exit = cfg["strategy"]["exit"]

    # 持仓
    positions: dict[str, dict] = {}
    if os.path.exists(args.positions):
        with open(args.positions, "r", encoding="utf-8") as f:
            pos_data = json.load(f)
        for p in pos_data.get("positions", []):
            if p.get("cost_price", 0) > 0:
                positions[str(p["code"]).zfill(6)] = p

    data_dir = os.path.join(args.data_dir, args.date)
    if not os.path.isdir(data_dir):
        print(f"ERROR: 数据目录不存在 {data_dir},先运行 fetch_data.py", file=sys.stderr)
        sys.exit(1)

    results = []
    for s in cfg["watchlist"]:
        code = str(s["code"]).zfill(6)
        jpath = os.path.join(data_dir, f"{code}.json")
        if not os.path.exists(jpath):
            print(f"[skip] no data for {code}")
            continue

        with open(jpath, "r", encoding="utf-8") as f:
            raw = json.load(f)

        kline = pd.DataFrame(raw.get("kline", []))
        if len(kline) < 30:
            print(f"[skip] insufficient kline for {code} ({len(kline)} days)")
            continue

        for col in ["open", "close", "high", "low", "volume"]:
            if col in kline.columns:
                kline[col] = pd.to_numeric(kline[col], errors="coerce")
        kline = kline.dropna(subset=["close"]).reset_index(drop=True)

        item: dict[str, Any] = {
            "code": code,
            "name": s.get("name", raw.get("name", "")),
            "quote": raw.get("quote", {}),
            "money_flow_5d": raw.get("money_flow", []),
            "dip_analysis": evaluate_dip_signals(kline, sp_dip),
        }
        if code in positions:
            item["position"] = positions[code]
            item["exit_analysis"] = evaluate_exit_signals(kline, positions[code], sp_exit)
        results.append(item)

    out_path = args.output or os.path.join(data_dir, "signals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"date": args.date, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"[done] signals -> {out_path}\n")

    # ---- 终端摘要 ----
    print(f"{'代码':<8}{'名称':<10}{'抄底':<10}{'盈亏':<12}{'动作'}")
    print("-" * 60)
    for r in results:
        d = r["dip_analysis"]
        flag = f"🟢{d['hit_count']}/6" if d["triggered"] else f"⚪{d['hit_count']}/6"
        if "exit_analysis" in r:
            ex = r["exit_analysis"]
            pnl = f"{ex['pnl_pct']:+.2f}%"
            action = ex["action"]
        else:
            pnl = "-"
            action = "抄底候选" if d["triggered"] else "观望"
        print(f"{r['code']:<8}{r['name']:<10}{flag:<10}{pnl:<12}{action}")


if __name__ == "__main__":
    main()
