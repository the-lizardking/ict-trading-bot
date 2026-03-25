#!/usr/bin/env python3
'''ICT Automated Trading Bot - Kill Zone Scalper Strategy'''

import time
import requests
import pandas as pd
from datetime import datetime
from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

# Kill Zone windows (UTC)
KILL_ZONES = [
    {"name": "London Open",   "start": 7,  "end": 10},
    {"name": "NY Open",       "start": 13, "end": 16},
    {"name": "London Close",  "start": 15, "end": 17},
]

def fetch_ohlcv(symbol="BTCUSDT", interval="5", limit=200):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol,
              "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        candles = data.get("result", {}).get("list", [])
        if not candles:
            return None
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['timestamp', 'open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col])
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Data fetch error: {e}")
        return None


class KillZoneScalperBot:
    '''ICT Kill Zone Scalper - Refined Strategy'''

    def __init__(self, api_key, api_secret, testnet=True):
        self.bybit = BybitConnector(api_key, api_secret, testnet)
        self.running = False
        self.check_interval = 60
        self.trade_log = []

    def is_kill_zone(self):
        now = datetime.utcnow()
        for kz in KILL_ZONES:
            if kz["start"] <= now.hour < kz["end"]:
                return True, kz["name"]
        return False, None

    def get_trend(self, df_1h):
        if df_1h is None or len(df_1h) < 2:
            return "neutral"
        last = df_1h.iloc[-1]
        prev = df_1h.iloc[-2]
        if last['close'] > prev['high']:
            return "bullish"
        elif last['close'] < prev['low']:
            return "bearish"
        return "neutral"

    def detect_fvg(self, df, min_pct=0.01):
        fvgs = []
        for i in range(2, len(df)):
            gap_size_bull = (df.iloc[i-2]['low'] - df.iloc[i]['high']) / df.iloc[i]['close'] * 100
            if gap_size_bull >= min_pct:
                fvgs.append({'type': 'bullish', 'top': df.iloc[i-2]['low'],
                              'bottom': df.iloc[i]['high'], 'idx': i})
            gap_size_bear = (df.iloc[i]['low'] - df.iloc[i-2]['high']) / df.iloc[i]['close'] * 100
            if gap_size_bear >= min_pct:
                fvgs.append({'type': 'bearish', 'top': df.iloc[i]['low'],
                              'bottom': df.iloc[i-2]['high'], 'idx': i})
        return fvgs

    def analyze_market(self):
        print(f"\n{' '*2}{'=' * 68}")
        print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  {'=' * 68}")

        in_kz, kz_name = self.is_kill_zone()
        if not in_kz:
            print("  Outside Kill Zone - standby")
            return None, None
        print(f"  Kill Zone: {kz_name}")

        df_1h = fetch_ohlcv(interval="60", limit=10)
        trend = self.get_trend(df_1h)
        print(f"  1H Trend: {trend.upper()}")
        if trend == "neutral":
            print("  Neutral trend - no trade")
            return None, None

        df_5m = fetch_ohlcv(interval="5", limit=200)
        if df_5m is None:
            print("  Failed to fetch 5m data")
            return None, None

        price = df_5m.iloc[-1]['close']
        print(f"  BTC Price: ${price:,.2f}")

        fvgs = self.detect_fvg(df_5m)
        recent_fvgs = [f for f in fvgs if len(df_5m) - f['idx'] <= 3]
        print(f"  Recent FVGs (last 3 candles): {len(recent_fvgs)}")

        for fvg in recent_fvgs:
            if trend == "bullish" and fvg['type'] == 'bullish':
                if fvg['bottom'] <= price <= fvg['top']:
                    print(f"  LONG SIGNAL in Kill Zone {kz_name}")
                    print(f"    Entry: ${price:,.2f}  Zone: ${fvg['bottom']:,.2f}-${fvg['top']:,.2f}")
                    return 'long', fvg
            if trend == "bearish" and fvg['type'] == 'bearish':
                if fvg['bottom'] <= price <= fvg['top']:
                    print(f"  SHORT SIGNAL in Kill Zone {kz_name}")
                    print(f"    Entry: ${price:,.2f}  Zone: ${fvg['bottom']:,.2f}-${fvg['top']:,.2f}")
                    return 'short', fvg

        print("  No aligned setups")
        return None, None

    def run(self, iterations=None):
        print("\n" + "="*70)
        print("  ICT Kill Zone Scalper - STARTED")
        print("="*70)
        print(f"  Symbol:    BTC/USDT (5m exec / 1H trend)")
        print(f"  Kill Zones: London Open, NY Open, London Close")
        print(f"  Mode:      TESTNET")
        print("="*70)

        self.running = True
        count = 0
        try:
            while self.running:
                count += 1
                if iterations and count > iterations:
                    break
                signal, data = self.analyze_market()
                if signal:
                    print(f"\n  Trade signal detected: {signal.upper()}")
                    print("  (Manual execution required for safety)")
                if self.running and (not iterations or count < iterations):
                    print(f"\n  Sleeping {self.check_interval}s...")
                    time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.running = False
            print("\nBot stopped.")

        print("\nBot shutdown complete.")


if __name__ == "__main__":
    bot = KillZoneScalperBot(
        api_key=BYBIT_TESTNET_API_KEY,
        api_secret=BYBIT_TESTNET_API_SECRET,
        testnet=True
    )
    bot.run(iterations=3)