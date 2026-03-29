import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.bybit.com"
CATEGORY = "linear"
SYMBOL = "BTCUSDT"
INTERVAL = "1"
OUTFILE = os.path.expanduser("~/ict-trading-bot/data/bybit_btcusdt_1m.csv")

DAYS_BACK = 30
LIMIT = 1000

def to_ms(dt):
    return int(dt.timestamp() * 1000)

def fetch_chunk(start_ms, end_ms):
    url = f"{BASE_URL}/v5/market/kline"
    params = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "start": start_ms,
        "end": end_ms,
        "limit": LIMIT
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {data}")
    return data["result"]["list"]

def main():
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=DAYS_BACK)

    current_start = start_dt
    all_rows = []

    while current_start < end_dt:
        current_end = min(current_start + timedelta(minutes=LIMIT), end_dt)
        print(f"Fetching {current_start} -> {current_end}")

        rows = fetch_chunk(to_ms(current_start), to_ms(current_end))
        if rows:
            all_rows.extend(rows)

        current_start = current_end
        time.sleep(0.15)

    if not all_rows:
        raise RuntimeError("No data returned from Bybit")

    df = pd.DataFrame(all_rows, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "turnover"
    ])

    df["timestamp"] = pd.to_numeric(df["timestamp"])
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["timestamp"] = df["timestamp_dt"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]

    os.makedirs(os.path.dirname(OUTFILE), exist_ok=True)
    df.to_csv(OUTFILE, index=False)

    print(f"\nSaved {len(df)} rows to {OUTFILE}")
    print(f"Start: {df['timestamp'].min()}")
    print(f"End:   {df['timestamp'].max()}")

if __name__ == "__main__":
    main()
