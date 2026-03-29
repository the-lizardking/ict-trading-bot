import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
from typing import Optional, List, Dict
DEFAULT_CONFIG = {
    "initial_capital": 10000.0,
    "risk_per_trade_pct": 1.0,
    "reward_to_risk": 2.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 3,
    "min_fvg_size_pct": 0.05,
    "swing_lookback": 5,
    "ob_lookback": 10,
    "session_start_hour": 2,
    "session_end_hour": 12,
    "sl_buffer_pct": 0.05,
    "taker_fee_pct": 0.055,
    "maker_fee_pct": 0.02,
    "slippage_pct": 0.02,
}
class ICTBacktester:
    def __init__(self, df, config=None):
        self.df = df.copy().reset_index(drop=True)
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.trades = []
        self.capital = self.cfg["initial_capital"]
        self.equity_curve = [self.capital]
    def detect_swing_highs_lows(self):
        lb = self.cfg["swing_lookback"]
        df = self.df
        sh, sl = [], []
        for i in range(lb, len(df) - lb):
            if df["high"].iloc[i] == df["high"].iloc[i-lb:i+lb+1].max():
                sh.append(i)
            if df["low"].iloc[i] == df["low"].iloc[i-lb:i+lb+1].min():
                sl.append(i)
        return sh, sl
    def detect_fvgs(self):
        df = self.df
        min_sz = self.cfg["min_fvg_size_pct"] / 100
        fvgs = []
        for i in range(2, len(df)):
            if df["low"].iloc[i] > df["high"].iloc[i-2]:
                sz = (df["low"].iloc[i] - df["high"].iloc[i-2]) / df["close"].iloc[i]
                if sz >= min_sz:
                    fvgs.append({"type": "bullish", "index": i,
                        "top": df["low"].iloc[i], "bottom": df["high"].iloc[i-2],
                        "filled": False, "ts": df["timestamp"].iloc[i]})
            if df["high"].iloc[i] < df["low"].iloc[i-2]:
                sz = (df["low"].iloc[i-2] - df["high"].iloc[i]) / df["close"].iloc[i]
                if sz >= min_sz:
                    fvgs.append({"type": "bearish", "index": i,
                        "top": df["low"].iloc[i-2], "bottom": df["high"].iloc[i],
                        "filled": False, "ts": df["timestamp"].iloc[i]})
        return fvgs
    def detect_order_blocks(self, swing_highs, swing_lows):
        df = self.df
        lb = self.cfg["ob_lookback"]
        obs = []
        for sh in swing_highs:
            for i in range(max(0, sh - lb), sh):
                if df["close"].iloc[i] < df["open"].iloc[i]:
                    obs.append({"type": "bullish", "index": i,
                        "top": df["open"].iloc[i], "bottom": df["low"].iloc[i]})
                    break
        for sl in swing_lows:
            for i in range(max(0, sl - lb), sl):
                if df["close"].iloc[i] > df["open"].iloc[i]:
                    obs.append({"type": "bearish", "index": i,
                        "top": df["high"].iloc[i], "bottom": df["close"].iloc[i]})
                    break
        return obs
    def market_structure(self, sh, sl):
        df = self.df
        if len(sh) < 2 or len(sl) < 2:
            return "ranging"
        rh = [df["high"].iloc[i] for i in sh[-3:]]
        rl = [df["low"].iloc[i] for i in sl[-3:]]
        hh = all(rh[i] > rh[i-1] for i in range(1, len(rh)))
        hl = all(rl[i] > rl[i-1] for i in range(1, len(rl)))
        lh = all(rh[i] < rh[i-1] for i in range(1, len(rh)))
        ll = all(rl[i] < rl[i-1] for i in range(1, len(rl)))
        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"
        return "ranging"
    def in_session(self, ts):
        if isinstance(ts, (int, float)):
            ts = datetime.utcfromtimestamp(ts / 1000)
        return self.cfg["session_start_hour"] <= ts.hour < self.cfg["session_end_hour"]
    def fmt_ts(self, ts):
        if isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
        return str(ts)[:16]
    def simulate_trade(self, signal, capital):
        df = self.df
        cfg = self.cfg
        i = signal["index"]
        direction = signal["direction"]
        entry = signal["entry_price"]
        lb = cfg["swing_lookback"] * 2
        buf = entry * cfg["sl_buffer_pct"] / 100
        if direction == "long":
            lows = df["low"].iloc[max(0, i-lb):i]
            sl = (lows.min() if len(lows) > 0 else entry * 0.99) - buf
            tp = entry + (entry - sl) * cfg["reward_to_risk"]
        else:
            highs = df["high"].iloc[max(0, i-lb):i]
            sl = (highs.max() if len(highs) > 0 else entry * 1.01) + buf
            tp = entry - (sl - entry) * cfg["reward_to_risk"]
        sl_dist = abs(entry - sl)
        if sl_dist < entry * 0.0005:
            return None
        risk_amt = capital * cfg["risk_per_trade_pct"] / 100
        size = risk_amt / sl_dist
        slip = entry * cfg["slippage_pct"] / 100
        act_entry = entry + (slip if direction == "long" else -slip)
        entry_fee = act_entry * size * cfg["taker_fee_pct"] / 100
        exit_price = None
        exit_reason = "timeout"
        exit_idx = min(i + 200, len(df) - 1)
        for j in range(i + 1, min(i + 201, len(df))):
            h = df["high"].iloc[j]
            lo = df["low"].iloc[j]
            if direction == "long":
                if lo <= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    exit_idx = j
                    break
                if h >= tp:
                    exit_price = tp
                    exit_reason = "take_profit"
                    exit_idx = j
                    break
            else:
                if h >= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    exit_idx = j
                    break
                if lo <= tp:
                    exit_price = tp
                    exit_reason = "take_profit"
                    exit_idx = j
                    break
        if exit_price is None:
            exit_price = df["close"].iloc[exit_idx]
        exit_fee = exit_price * size * cfg["taker_fee_pct"] / 100
        total_fees = entry_fee + exit_fee
        gross_pnl = (exit_price - act_entry) * size if direction == "long" else (act_entry - exit_price) * size
        net_pnl = gross_pnl - total_fees
        pnl_pct = net_pnl / capital * 100
        r_multiple = gross_pnl / risk_amt if risk_amt > 0 else 0
        return {
            "entry_time":    self.fmt_ts(df["timestamp"].iloc[i]),
            "exit_time":     self.fmt_ts(df["timestamp"].iloc[exit_idx]),
            "direction":     direction,
            "entry_price":   round(act_entry, 2),
            "stop_loss":     round(sl, 2),
            "take_profit":   round(tp, 2),
            "exit_price":    round(exit_price, 2),
            "exit_reason":   exit_reason,
            "size":          round(size, 6),
            "gross_pnl":     round(gross_pnl, 2),
            "fees":          round(total_fees, 2),
            "net_pnl":       round(net_pnl, 2),
            "pnl_pct":       round(pnl_pct, 4),
            "r_multiple":    round(r_multiple, 2),
            "duration_bars": exit_idx - i,
            "ob_confluence": signal.get("ob_confluence", False),
            "structure":     signal.get("structure", "unknown"),
            "sl_distance":   round(sl_dist, 2),
        }
    def run(self):
        cfg = self.cfg
        sh, sl_idx = self.detect_swing_highs_lows()
        fvgs = self.detect_fvgs()
        obs = self.detect_order_blocks(sh, sl_idx)
        structure = self.market_structure(sh, sl_idx)
        df = self.df
        active_fvgs = list(fvgs)
        daily_trades = {}
        daily_loss = {}
        for i in range(cfg["ob_lookback"] + 5, len(df)):
            if not self.in_session(df["timestamp"].iloc[i]):
                continue
            day = self.fmt_ts(df["timestamp"].iloc[i])[:10]
            daily_trades.setdefault(day, 0)
            daily_loss.setdefault(day, 0.0)
            if daily_trades[day] >= cfg["max_trades_per_day"]:
                continue
            if daily_loss[day] <= -(cfg["max_daily_loss_pct"] / 100 * cfg["initial_capital"]):
                continue
            price = df["close"].iloc[i]
            signal = None
            for fvg in active_fvgs:
                if fvg["filled"] or fvg["index"] >= i:
                    continue
                if fvg["type"] == "bullish" and structure in ("bullish", "ranging"):
                    if fvg["bottom"] <= price <= fvg["top"]:
                        ob_conf = any(
                            o["type"] == "bullish" and o["index"] < i
                            and o["bottom"] <= price <= o["top"] * 1.002
                            for o in obs
                        )
                        signal = {"index": i, "direction": "long",
                            "entry_price": price, "ob_confluence": ob_conf,
                            "structure": structure}
                        fvg["filled"] = True
                        break
                if fvg["type"] == "bearish" and structure in ("bearish", "ranging"):
                    if fvg["bottom"] <= price <= fvg["top"]:
                        ob_conf = any(
                            o["type"] == "bearish" and o["index"] < i
                            and o["bottom"] * 0.998 <= price <= o["top"]
                            for o in obs
                        )
                        signal = {"index": i, "direction": "short",
                            "entry_price": price, "ob_confluence": ob_conf,
                            "structure": structure}
                        fvg["filled"] = True
                        break
            if signal:
                trade = self.simulate_trade(signal, self.capital)
                if trade:
                    self.capital += trade["net_pnl"]
                    trade["capital_after"] = round(self.capital, 2)
                    self.trades.append(trade)
                    self.equity_curve.append(self.capital)
                    daily_trades[day] += 1
                    if trade["net_pnl"] < 0:
                        daily_loss[day] += trade["net_pnl"]
        return self.trades
    def summary(self):
        if not self.trades:
            return {"error": "No trades executed"}
        df_t = pd.DataFrame(self.trades)
        wins = df_t[df_t["net_pnl"] > 0]
        loss = df_t[df_t["net_pnl"] < 0]
        total = len(df_t)
        initial = self.cfg["initial_capital"]
        final = self.capital
        peak = max(self.equity_curve)
        valley_after_peak = self.equity_curve[self.equity_curve.index(peak):]
        trough = min(valley_after_peak) if valley_after_peak else final
        max_dd = (peak - trough) / peak * 100 if peak > 0 else 0
        profit_factor = abs(wins["gross_pnl"].sum() / loss["gross_pnl"].sum()) if len(loss) > 0 and loss["gross_pnl"].sum() != 0 else float("inf")
        return {
            "total_trades":      total,
            "winners":           len(wins),
            "losers":            len(loss),
            "win_rate_pct":      round(len(wins) / total * 100, 1),
            "initial_capital":   initial,
            "final_capital":     round(final, 2),
            "total_pnl":         round(final - initial, 2),
            "total_return_pct":  round((final - initial) / initial * 100, 2),
            "avg_win":           round(wins["net_pnl"].mean(), 2) if len(wins) > 0 else 0,
            "avg_loss":          round(loss["net_pnl"].mean(), 2) if len(loss) > 0 else 0,
            "avg_r_multiple":    round(df_t["r_multiple"].mean(), 2),
            "profit_factor":     round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "trades":           df_t.to_dict(orient="records") if len(df_t) > 0 else [],
        }
