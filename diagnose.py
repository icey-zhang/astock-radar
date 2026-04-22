"""
A 股每日信号分析 · 三策略架构(v3.0)

读取 fetch_data.py 生成的 JSON(含 close_history 250 天 + kline_30d 30 天完整),
输出 signals.json,每只股票包含三档独立信号:

  - strategy_take_profit   持有股票:5 天涨势 + 浮盈 + 90 天高位 → 建议卖出价
  - strategy_dip_buy       未持有:5 天新低 + 90 天低位 → 建议买入价(激进/稳健两档)
  - strategy_swing         波段关注:短期上涨预兆(MACD 金叉/量放大/突破)→ 进出价位

三个策略完全独立评估,不互斥。消息面综合交给 Claude.ai 做,本脚本只输出数值信号。

⚠ 重要:strategy_swing 的信号预测能力有限(历史命中 ~50%),
  输出措辞为"波段关注候选",不是"建议快进"。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml


# ============================== 技术指标 ==============================
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    rng = (highest - lowest).replace(0, np.nan)
    rsv = (close - lowest) / rng * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def _safe(v, digits: int = 2, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return round(f, digits)
    except (ValueError, TypeError):
        return default


# ============================== 上下文计算 ==============================
def compute_context(close_series: pd.Series, detail_df: pd.DataFrame) -> dict:
    """算出三个策略共用的上下文:5 天趋势、90 天区间、现价。"""
    last_close = float(close_series.iloc[-1])

    # --- 5 天趋势 ---
    last_5 = close_series.iloc[-5:] if len(close_series) >= 5 else close_series
    if len(last_5) >= 2:
        cum_pct_5d = (last_5.iloc[-1] / last_5.iloc[0] - 1) * 100
    else:
        cum_pct_5d = 0.0
    # 5 天内涨了几天
    diffs = last_5.diff().dropna()
    days_up_5d = int((diffs > 0).sum())
    days_down_5d = int((diffs < 0).sum())

    # --- 5 天极值 ---
    close_5d_min = float(last_5.min())
    close_5d_max = float(last_5.max())
    is_5d_low = last_close <= close_5d_min * 1.001   # 容差 0.1%
    is_5d_high = last_close >= close_5d_max * 0.999

    # --- 90 天区间 ---
    last_90 = close_series.iloc[-90:] if len(close_series) >= 90 else close_series
    high_90 = float(last_90.max())
    low_90 = float(last_90.min())
    rng_90 = high_90 - low_90
    position_90 = ((last_close - low_90) / rng_90) if rng_90 > 0 else 0.5

    return {
        "last_close": last_close,
        "trend_5d": {
            "days_up": days_up_5d,
            "days_down": days_down_5d,
            "cum_pct": _safe(cum_pct_5d),
            "min": _safe(close_5d_min, 3),
            "max": _safe(close_5d_max, 3),
            "is_5d_low": is_5d_low,
            "is_5d_high": is_5d_high,
        },
        "range_90d": {
            "high": _safe(high_90, 3),
            "low": _safe(low_90, 3),
            "position_pct": _safe(position_90 * 100),  # 0-100 表示当前价位在区间哪里
        },
    }


# ============================== 策略 1:止盈(持仓) ==============================
def evaluate_take_profit(context: dict, position: dict, sp: dict) -> dict:
    """
    止盈策略(仅对持仓生效)。

    触发条件(三者同时满足):
        A. 浮盈 >= tp_min_pnl_pct (默认 18%,接近 20% 即考虑)
        B. 近 5 天累计涨幅 >= tp_trend_cum_pct (默认 3%)
        C. 当前价处于 90 天区间相对高位 (默认 position_pct >= 60%)

    建议卖出价 = max(成本 × 1.20, 90天高 × 0.98)
    """
    last_close = context["last_close"]
    cost = float(position["cost_price"])
    shares = position.get("shares", 0)
    pnl_pct = (last_close - cost) / cost * 100 if cost > 0 else 0.0

    cum_5d = context["trend_5d"]["cum_pct"] or 0.0
    high_90 = context["range_90d"]["high"]
    position_90 = context["range_90d"]["position_pct"] or 0.0

    # 三个独立条件
    cond_a = pnl_pct >= sp.get("tp_min_pnl_pct", 18.0)
    cond_b = cum_5d >= sp.get("tp_trend_cum_pct", 3.0)
    cond_c = position_90 >= sp.get("tp_90d_position_pct", 60.0)

    hit_reasons = []
    if cond_a:
        hit_reasons.append(f"浮盈 {pnl_pct:+.2f}% ≥ {sp.get('tp_min_pnl_pct', 18.0)}%")
    if cond_b:
        hit_reasons.append(f"近 5 天累计涨 {cum_5d:+.2f}%")
    if cond_c:
        hit_reasons.append(f"位于 90 天区间 {position_90:.0f}% 高位")

    all_hit = cond_a and cond_b and cond_c
    core_hit = cond_a  # 浮盈是必要条件

    # 建议卖出价
    tp_target_pct = sp.get("tp_target_pct", 20.0) / 100
    price_cost_target = cost * (1 + tp_target_pct)         # 成本 × 1.20
    price_90d_high = high_90 * sp.get("tp_90d_high_mult", 0.98) if high_90 else None

    if price_90d_high:
        suggested_exit = max(price_cost_target, price_90d_high)
        exit_basis = "90d_high" if price_90d_high > price_cost_target else "cost_target"
    else:
        suggested_exit = price_cost_target
        exit_basis = "cost_target"

    # 置信度:全部 3 个信号命中 = 高;核心条件 + 1 个辅助 = 中;只核心 = 低
    if all_hit:
        confidence = "高"
    elif core_hit and (cond_b or cond_c):
        confidence = "中"
    elif core_hit:
        confidence = "低"
    else:
        confidence = "未触发"

    # 推荐动作
    if all_hit:
        action = "SELL_STRONG"          # 三条都满足,建议全部止盈
        action_cn = "全部止盈"
    elif core_hit and cond_c:
        action = "SELL_PARTIAL"          # 浮盈 + 高位,但涨势不确认
        action_cn = "部分止盈"
    elif core_hit:
        action = "WATCH_TO_SELL"         # 只是浮盈到位,观察
        action_cn = "观察止盈时机"
    else:
        action = "HOLD"
        action_cn = "持有"

    # 浮亏预警(不影响动作,纯视觉提示)
    # 分三档:<-3% 轻度,<-8% 中度,<-15% 深度
    if pnl_pct <= -15.0:
        loss_warning = {"level": "deep", "level_cn": "深度浮亏", "pnl_pct": _safe(pnl_pct)}
    elif pnl_pct <= -8.0:
        loss_warning = {"level": "moderate", "level_cn": "中度浮亏", "pnl_pct": _safe(pnl_pct)}
    elif pnl_pct <= -3.0:
        loss_warning = {"level": "light", "level_cn": "轻度浮亏", "pnl_pct": _safe(pnl_pct)}
    else:
        loss_warning = None

    return {
        "triggered": all_hit,
        "action": action,
        "action_cn": action_cn,
        "confidence": confidence,
        "loss_warning": loss_warning,   # None 或 {level, level_cn, pnl_pct}
        "position": {
            "cost_price": cost,
            "current_price": last_close,
            "shares": shares,
            "pnl_pct": _safe(pnl_pct),
            "pnl_amount": _safe((last_close - cost) * shares, 2),
        },
        "conditions": {
            "pnl_over_threshold": cond_a,
            "trend_5d_up": cond_b,
            "at_90d_high": cond_c,
            "all_hit": all_hit,
        },
        "reasons": hit_reasons,
        "suggested_exit_price": _safe(suggested_exit, 2),
        "exit_basis": exit_basis,  # 卖价依据:cost_target 还是 90d_high
        "price_reference": {
            "cost_target_price": _safe(price_cost_target, 2),
            "price_90d_high_mult": _safe(price_90d_high, 2) if price_90d_high else None,
            "range_90d_high": high_90,
        },
    }


# ============================== 策略 2:抄底(未持仓) ==============================
def evaluate_dip_buy(context: dict, sp: dict) -> dict:
    """
    抄底策略(仅对未持仓生效)。

    触发条件(双级):
        A. 近 5 天创新低 且 累计跌幅 >= dip_trend_cum_pct (默认 3%)
        B. (加强)跌破 90 天低位 × dip_90d_low_tol(默认 1.02)

    仅 A → 初级抄底候选,给激进买入价
    A + B → 强抄底候选,给稳健买入价(等再探底)
    """
    last_close = context["last_close"]
    cum_5d = context["trend_5d"]["cum_pct"] or 0.0
    is_5d_low = context["trend_5d"]["is_5d_low"]
    low_90 = context["range_90d"]["low"]

    # 条件 A:5 天最低 + 累计跌 >= 3%
    cond_a = is_5d_low and cum_5d <= -sp.get("dip_trend_cum_pct", 3.0)

    # 条件 B:接近或跌破 90 天低位
    low_90_tol = sp.get("dip_90d_low_tol", 1.02)
    cond_b = low_90 is not None and last_close <= low_90 * low_90_tol

    hit_reasons = []
    if cond_a:
        hit_reasons.append(
            f"近 5 天最低 + 累计跌 {cum_5d:+.2f}% ≤ -{sp.get('dip_trend_cum_pct', 3.0)}%")
    if cond_b:
        hit_reasons.append(
            f"接近/跌破 90 天低位 {low_90:.2f} × {low_90_tol}")

    # 建议买入价 —— 两档
    # 激进档:现价(信号已触发就买)
    entry_aggressive = last_close
    # 稳健档:90 天最低 × 1.01(等再探底一次确认)
    entry_conservative = low_90 * sp.get("dip_conservative_entry_mult", 1.01) \
                         if low_90 else None

    # 等级判定
    if cond_a and cond_b:
        tier = "STRONG"
        tier_cn = "强抄底候选"
        confidence = "高"
    elif cond_a:
        tier = "LIGHT"
        tier_cn = "初级抄底候选"
        confidence = "中"
    else:
        tier = "NONE"
        tier_cn = "观望"
        confidence = "未触发"

    # 止损参考(抄底失败需要明确退出点)
    stop_loss_price = entry_aggressive * (1 - sp.get("dip_stop_loss_pct", 5.0) / 100)

    return {
        "triggered": cond_a,   # 仅 A 即触发,B 决定档位
        "tier": tier,
        "tier_cn": tier_cn,
        "confidence": confidence,
        "conditions": {
            "new_low_5d_with_drop": cond_a,
            "below_90d_low": cond_b,
        },
        "reasons": hit_reasons,
        "suggested_entry_price_aggressive": _safe(entry_aggressive, 2),
        "suggested_entry_price_conservative": _safe(entry_conservative, 2) \
                                              if entry_conservative else None,
        "suggested_stop_loss": _safe(stop_loss_price, 2),
        "price_reference": {
            "range_90d_low": low_90,
            "cum_pct_5d": _safe(cum_5d),
        },
    }


# ============================== 策略 3:激进波段(独立) ==============================
def evaluate_swing(close_series: pd.Series, detail_df: pd.DataFrame,
                   context: dict, sp: dict) -> dict:
    """
    短期上涨预兆波段策略(独立信号,持仓/未持仓都可能触发)。

    候选信号:
        1. MACD 金叉(DIF 上穿 DEA,最近 3 日内)
        2. KDJ 金叉(K 上穿 D,且 K<50 说明从低位启动)
        3. 量能放大(当日量/20日均量 > 1.5)
        4. 突破 10 日高点

    命中 >= swing_min_signals(默认 2)才输出,否则不触发。
    """
    last_close = context["last_close"]
    signals: list[str] = []
    signal_details = {}

    # --- 1. MACD 金叉 ---
    dif, dea, hist = macd(close_series)
    macd_cross = False
    if len(dif) >= 4:
        # 最近 3 天有过 DIF 上穿 DEA(即 DIF - DEA 从负变正)
        recent_diff = (dif - dea).iloc[-4:]
        for i in range(1, len(recent_diff)):
            if recent_diff.iloc[i - 1] < 0 and recent_diff.iloc[i] > 0:
                macd_cross = True
                break
    signal_details["macd_cross"] = macd_cross
    signal_details["macd_hist"] = _safe(hist.iloc[-1], 3)
    if macd_cross:
        signals.append(f"MACD 金叉(近 3 日)")

    # --- 2. KDJ 金叉 (低位) ---
    if len(detail_df) >= 10:
        h = pd.to_numeric(detail_df["high"], errors="coerce")
        l = pd.to_numeric(detail_df["low"], errors="coerce")
        c = pd.to_numeric(detail_df["close"], errors="coerce")
        k, d, j = kdj(h, l, c)
        kdj_cross = False
        if len(k) >= 3:
            recent_kd = (k - d).iloc[-3:]
            for i in range(1, len(recent_kd)):
                if recent_kd.iloc[i - 1] < 0 and recent_kd.iloc[i] > 0:
                    # 且 K < 50 表示从相对低位金叉
                    if k.iloc[i] < 50:
                        kdj_cross = True
                        break
        signal_details["kdj_cross"] = kdj_cross
        signal_details["kdj_k"] = _safe(k.iloc[-1])
        signal_details["kdj_d"] = _safe(d.iloc[-1])
        if kdj_cross:
            signals.append(f"KDJ 低位金叉(K={_safe(k.iloc[-1])})")

    # --- 3. 量能放大 ---
    vol_ratio = None
    if len(detail_df) >= 5:
        vol = pd.to_numeric(detail_df["volume"], errors="coerce")
        window = min(20, len(vol))
        if window >= 5:
            vol_ratio = float(vol.iloc[-1] / vol.iloc[-window:].mean())
    signal_details["volume_ratio"] = _safe(vol_ratio) if vol_ratio else None
    volume_surge = vol_ratio is not None and vol_ratio > sp.get("swing_vol_mult", 1.5)
    if volume_surge:
        signals.append(f"量能放大:量比 {vol_ratio:.2f}")

    # --- 4. 突破 10 日高点 ---
    breakout_10d = False
    if len(close_series) >= 11:
        prev_10d_high = float(close_series.iloc[-11:-1].max())  # 不含今日
        if last_close > prev_10d_high:
            breakout_10d = True
            signal_details["prev_10d_high"] = _safe(prev_10d_high, 3)
    if breakout_10d:
        signals.append(f"突破 10 日高点")

    # --- 汇总判定 ---
    min_signals = sp.get("swing_min_signals", 2)
    triggered = len(signals) >= min_signals

    if not triggered:
        return {
            "triggered": False,
            "hit_count": len(signals),
            "total": 4,
            "signals": signals,
            "signal_details": signal_details,
            "confidence": "未触发",
            "note": "波段信号预测能力有限,仅用于候选筛选",
        }

    # 建议进出价位
    swing_target_pct = sp.get("swing_target_pct", 6.0) / 100  # +5-8%
    swing_stop_pct = sp.get("swing_stop_pct", 3.0) / 100      # -3%
    target_price = last_close * (1 + swing_target_pct)
    stop_price = last_close * (1 - swing_stop_pct)

    # 置信度
    if len(signals) >= 4:
        confidence = "较高"  # 仍用"较高"而非"高",提醒不确定性
    elif len(signals) == 3:
        confidence = "中"
    else:
        confidence = "低"

    return {
        "triggered": True,
        "hit_count": len(signals),
        "total": 4,
        "signals": signals,
        "signal_details": signal_details,
        "confidence": confidence,
        "suggested_entry": _safe(last_close, 2),
        "suggested_target": _safe(target_price, 2),
        "suggested_stop": _safe(stop_price, 2),
        "expected_move_pct": _safe(swing_target_pct * 100),
        "note": "波段信号仅作候选参考,历史命中率有限,务必设止损",
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
    sp = cfg["strategy"]

    # 持仓(可选。方案 A 在 GitHub Actions 里跑时不提供持仓 → 只输出抄底/波段信号)
    positions: dict[str, dict] = {}
    if args.positions and os.path.exists(args.positions) and os.path.isfile(args.positions):
        try:
            with open(args.positions, "r", encoding="utf-8") as f:
                pos_data = json.load(f)
            for p in pos_data.get("positions", []):
                if p.get("cost_price", 0) > 0:
                    positions[str(p["code"]).zfill(6)] = p
            if positions:
                print(f"[info] 加载 {len(positions)} 只持仓")
            else:
                print(f"[info] positions 文件存在但为空")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[warn] positions 文件解析失败 ({e}),按无持仓处理", file=sys.stderr)
    else:
        print(f"[info] 无 positions 文件,只输出抄底/波段信号(不含止盈分析)")

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

        close_history = raw.get("close_history", [])
        kline_30d = raw.get("kline_30d", [])
        # 向后兼容老格式
        if not close_history and not kline_30d:
            old_kline = raw.get("kline", [])
            if old_kline:
                close_history = [{"date": k["date"], "close": k["close"]} for k in old_kline]
                kline_30d = old_kline[-30:]

        if not close_history or len(close_history) < 20:
            print(f"[skip] insufficient close_history for {code}")
            continue
        if not kline_30d or len(kline_30d) < 10:
            print(f"[skip] insufficient kline_30d for {code}")
            continue

        # 数据序列
        close_series = pd.Series(
            pd.to_numeric([k["close"] for k in close_history], errors="coerce")
        ).dropna().reset_index(drop=True)
        detail_df = pd.DataFrame(kline_30d)
        for col in ["open", "close", "high", "low", "volume"]:
            if col in detail_df.columns:
                detail_df[col] = pd.to_numeric(detail_df[col], errors="coerce")
        detail_df = detail_df.dropna(subset=["close"]).reset_index(drop=True)

        # 共用上下文
        ctx = compute_context(close_series, detail_df)

        item: dict[str, Any] = {
            "code": code,
            "name": s.get("name", raw.get("name", "")),
            "quote": raw.get("quote", {}),
            "context": ctx,
            "money_flow_5d": raw.get("money_flow", []),
        }

        # 三策略独立评估
        pos = positions.get(code)
        if pos is not None:
            # 持仓 → 止盈分析
            item["strategy_take_profit"] = evaluate_take_profit(ctx, pos, sp["take_profit"])
            item["position"] = pos
        else:
            # 未持仓 → 抄底分析
            item["strategy_dip_buy"] = evaluate_dip_buy(ctx, sp["dip_buy"])

        # 波段 → 无论持仓都评估
        item["strategy_swing"] = evaluate_swing(close_series, detail_df, ctx, sp["swing"])

        results.append(item)

    out_path = args.output or os.path.join(data_dir, "signals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"date": args.date, "results": results}, f,
                  ensure_ascii=False, indent=2)
    print(f"[done] signals -> {out_path}\n")

    # --- 终端摘要 ---
    print(f"{'代码':<8}{'名称':<10}{'5d趋势':<12}{'90d位置':<10}"
          f"{'止盈':<12}{'抄底':<12}{'波段'}")
    print("-" * 85)
    for r in results:
        c = r["context"]
        trend_str = f"{c['trend_5d']['cum_pct']:+.2f}%"
        pos_90 = c['range_90d']['position_pct']
        pos_str = f"{pos_90:.0f}%" if pos_90 is not None else "-"

        if "strategy_take_profit" in r:
            tp = r["strategy_take_profit"]
            tp_str = f"{tp['action_cn']}({tp['confidence']})"
        else:
            tp_str = "-"

        if "strategy_dip_buy" in r:
            db = r["strategy_dip_buy"]
            db_str = f"{db['tier_cn']}" if db["triggered"] else "-"
        else:
            db_str = "-"

        sw = r["strategy_swing"]
        sw_str = f"✓{sw['hit_count']}/4({sw['confidence']})" if sw["triggered"] \
                 else f"{sw['hit_count']}/4"

        print(f"{r['code']:<8}{r['name']:<10}{trend_str:<12}{pos_str:<10}"
              f"{tp_str:<12}{db_str:<12}{sw_str}")


if __name__ == "__main__":
    main()
