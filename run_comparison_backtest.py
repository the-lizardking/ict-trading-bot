import os
import random
import pandas as pd
from datetime import datetime, timedelta
from backtester import ICTBacktester
from alert_manager import AlertManager

CURRENT_VERSION_NAME = "v1_baseline"
CURRENT_CONFIG = {}

NEW_VERSION_NAME = "v2_test"
NEW_CONFIG = {}

NUM_PERIODS = 10
PERIOD_DAYS = 30

DATA_FILE = os.path.expanduser("~/ict-trading-bot/data/btc_1m_sample.csv")

MIN_DATE = datetime(2024, 6, 1)
MAX_DATE = datetime.utcnow() - timedelta(days=PERIOD_DAYS + 1)

alerter = AlertManager()

def pick_random_periods():
    periods = []
    attempts = 0
    while len(periods) < NUM_PERIODS and attempts < 2000:
        attempts += 1
        span_days = (MAX_DATE - MIN_DATE).days
        if span_days <= 0:
            break
        start = MIN_DATE + timedelta(days=random.randint(0, span_days))
        end = start + timedelta(days=PERIOD_DAYS)

        overlap = False
        for s, e in periods:
            if not (end <= s or start >= e):
                overlap = True
                break
        if not overlap:
            periods.append((start, end))
    return sorted(periods)

def load_period_df(start, end):
    df = pd.read_csv(DATA_FILE)
    if "timestamp" not in df.columns:
        raise ValueError("CSV must contain a timestamp column")
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
    mask = (df["timestamp_dt"] >= start) & (df["timestamp_dt"] < end)
    out = df.loc[mask].copy()
    out = out.drop(columns=["timestamp_dt"])
    return out

def run_one_version(df, config):
    bt = ICTBacktester(df, config=config)
    bt.run()
    return bt.summary()

def fmt_summary(name, s):
    return (
        f"{name}\n"
        f"Trades: {s.get('total_trades', 0)}\n"
        f"Win rate: {s.get('win_rate_pct', 0)}%\n"
        f"PnL: {s.get('total_pnl', 0)}\n"
        f"Return: {s.get('total_return_pct', 0)}%\n"
        f"Profit factor: {s.get('profit_factor', 0)}\n"
        f"Max DD: {s.get('max_drawdown_pct', 0)}%"
    )

def main():
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"Data file not found: {DATA_FILE}")

    periods = pick_random_periods()
    if not periods:
        raise ValueError("No valid random periods could be selected")

    current_results = []
    new_results = []
    current_wins = 0
    new_wins = 0

    for i, (start, end) in enumerate(periods, 1):
        print(f"Running period {i}/{NUM_PERIODS}: {start.date()} -> {end.date()}")

        df = load_period_df(start, end)
        if df.empty:
            print("  Skipping empty period")
            continue

        current_summary = run_one_version(df, CURRENT_CONFIG)
        new_summary = run_one_version(df, NEW_CONFIG)

        current_results.append(current_summary)
        new_results.append(new_summary)

        cur_pnl = current_summary.get("total_pnl", 0)
        new_pnl = new_summary.get("total_pnl", 0)

        if new_pnl > cur_pnl:
            new_wins += 1
            winner = NEW_VERSION_NAME
        elif cur_pnl > new_pnl:
            current_wins += 1
            winner = CURRENT_VERSION_NAME
        else:
            winner = "Tie"

        msg = (
            f"Backtest period {i}/{NUM_PERIODS}\n"
            f"{start.date()} -> {end.date()}\n\n"
            f"{fmt_summary(CURRENT_VERSION_NAME, current_summary)}\n\n"
            f"{fmt_summary(NEW_VERSION_NAME, new_summary)}\n\n"
            f"Winner: {winner}"
        )
        print(msg)
        try:
            alerter.send_alert(msg)
        except Exception as e:
            print(f"Telegram send failed: {e}")

    total_cur = sum(x.get("total_pnl", 0) for x in current_results)
    total_new = sum(x.get("total_pnl", 0) for x in new_results)

    recommendation = (
        f"Use {NEW_VERSION_NAME}"
        if new_wins >= 7 and total_new > total_cur
        else f"Keep {CURRENT_VERSION_NAME}"
    )

    final_msg = (
        f"FINAL SUMMARY\n\n"
        f"{CURRENT_VERSION_NAME} wins: {current_wins}\n"
        f"{NEW_VERSION_NAME} wins: {new_wins}\n\n"
        f"{CURRENT_VERSION_NAME} total PnL: {total_cur}\n"
        f"{NEW_VERSION_NAME} total PnL: {total_new}\n\n"
        f"Recommendation: {recommendation}"
    )
    print(final_msg)
    try:
        alerter.send_alert(final_msg)
    except Exception as e:
        print(f"Telegram send failed: {e}")

if __name__ == "__main__":
    main()
