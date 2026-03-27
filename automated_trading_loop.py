#!/usr/bin/env python3
'''ICT Automated Trading Bot - Milestone 6: Daily Max Drawdown Protection'''
import time
import pandas as pd
from datetime import datetime, date
from bybit_connector import BybitConnector
import os

# Kill Zone windows (UTC)
KILL_ZONES = [
    {"name": "London Open", "start": 7, "end": 10},
    {"name": "NY Open", "start": 13, "end": 16},
    {"name": "London Close", "start": 15, "end": 17},
]

class KillZoneScalperBot:
    '''ICT Kill Zone Scalper - Automated Paper Trading Version'''
    def __init__(self, api_key, api_secret, testnet=True):
        self.bybit = BybitConnector(api_key, api_secret, testnet)
        self.running = False
        self.check_interval = 60
        self.trade_log = []
        self.risk_pct = 0.025        # 2.5% risk per trade
        self.rr_ratio = 2.0          # 2:1 Reward-to-Risk

        # --- Daily Max Drawdown Protection ---
        self.daily_loss_limit_pct = 0.05   # 5% max daily loss
        self.daily_start_balance = None    # Balance at start of trading day
        self.daily_realized_pnl = 0.0      # Cumulative P&L for the day (USD)
        self.daily_date = None             # Tracks current trading date
        self.daily_limit_hit = False       # Flag: halts trading if True

    def _reset_daily_stats(self, balance):
        """Reset daily tracking at start of new UTC day."""
        self.daily_date = date.today()
        self.daily_start_balance = balance
        self.daily_realized_pnl = 0.0
        self.daily_limit_hit = False
        print(f"  [Day Reset] New day {self.daily_date} | Start balance: ${balance:,.2f}")

    def _check_daily_drawdown(self):
        """
        Fetch current balance, compute daily P&L, and check if max drawdown
        limit has been breached.  Returns True if trading is ALLOWED.
        """
        try:
            balance_data = self.bybit.get_balance()
            current_balance = float(balance_data['total'].get('USDT', 0))
        except Exception as e:
            print(f"  [Drawdown Check] Could not fetch balance: {e}")
            return True  # Allow trading if balance unavailable (fail-open)

        today = date.today()

        # New day -> reset baseline
        if self.daily_date != today or self.daily_start_balance is None:
            self._reset_daily_stats(current_balance)
            return True

        # Compute unrealised + realised drawdown from start-of-day balance
        daily_pnl = current_balance - self.daily_start_balance
        daily_loss_limit = self.daily_start_balance * self.daily_loss_limit_pct

        if daily_pnl < 0 and abs(daily_pnl) >= daily_loss_limit:
            self.daily_limit_hit = True
            print(
                f"  ⛔ DAILY MAX DRAWDOWN HIT! "
                f"Loss: ${abs(daily_pnl):,.2f} / Limit: ${daily_loss_limit:,.2f} "
                f"({self.daily_loss_limit_pct*100:.1f}% of ${self.daily_start_balance:,.2f}) "
                f"-- Trading HALTED for today."
            )
            return False

        # Update running P&L display
        self.daily_realized_pnl = daily_pnl
        remaining = daily_loss_limit - abs(min(daily_pnl, 0))
        print(
            f"  [Drawdown] Daily P&L: ${daily_pnl:+,.2f} | "
            f"Buffer remaining: ${remaining:,.2f} / ${daily_loss_limit:,.2f}"
        )
        return True

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
            if df.iloc[i-2]['low'] > df.iloc[i]['high']:
                gap_size = (df.iloc[i-2]['low'] - df.iloc[i]['high']) / df.iloc[i]['close'] * 100
                if gap_size >= min_pct:
                    fvgs.append({
                        'type': 'bullish',
                        'top': df.iloc[i-2]['low'],
                        'bottom': df.iloc[i]['high'],
                        'idx': i
                    })
            elif df.iloc[i-2]['high'] < df.iloc[i]['low']:
                gap_size = (df.iloc[i]['low'] - df.iloc[i-2]['high']) / df.iloc[i]['close'] * 100
                if gap_size >= min_pct:
                    fvgs.append({
                        'type': 'bearish',
                        'top': df.iloc[i]['low'],
                        'bottom': df.iloc[i-2]['high'],
                        'idx': i
                    })
        return fvgs

    def calculate_position_size(self, price, stop_loss):
        """Calculate quantity based on account balance and risk %"""
        try:
            balance_data = self.bybit.get_balance()
            usdt_balance = float(balance_data['total'].get('USDT', 0))
            if usdt_balance <= 0:
                return 0
            risk_amount = usdt_balance * self.risk_pct
            price_risk = abs(price - stop_loss)
            if price_risk == 0:
                return 0
            qty = risk_amount / price_risk
            return round(qty, 3)
        except Exception as e:
            print(f"Error calculating size: {e}")
            return 0

    def execute_trade(self, signal, price, fvg):
        """Execute market order with SL and TP (only if within daily drawdown limit)"""
        # --- Drawdown gate ---
        if not self._check_daily_drawdown():
            print("  ❌ Trade blocked: daily max drawdown limit reached.")
            return

        print(f"🚀 Executing {signal.upper()} trade...")

        if signal == 'long':
            sl = fvg['bottom'] * 0.999
            tp = price + (price - sl) * self.rr_ratio
            side = 'buy'
        else:
            sl = fvg['top'] * 1.001
            tp = price - (sl - price) * self.rr_ratio
            side = 'sell'

        qty = self.calculate_position_size(price, sl)
        if qty <= 0:
            print("❌ Invalid quantity (check balance/risk)")
            return

        print(f"   📊 Qty: {qty} | SL: {sl:,.2f} | TP: {tp:,.2f}")

        order = self.bybit.place_market_order('BTC/USDT:USDT', side, qty)

        if order:
            trade_info = {
                'timestamp': datetime.utcnow(),
                'symbol': 'BTC/USDT',
                'side': side,
                'entry': price,
                'qty': qty,
                'sl': sl,
                'tp': tp,
                'status': 'open'
            }
            self.trade_log.append(trade_info)
            print(f"✅ Trade successfully placed and logged.")
        else:
            print("❌ Failed to place order.")

    def analyze_market(self):
        print(f"\n{'=' * 68}")
        print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  {'=' * 68}")

        # Check daily drawdown before any analysis
        if self.daily_limit_hit:
            print("  ⛔ Daily max drawdown limit already hit - no trading today.")
            return None, None, None

        in_kz, kz_name = self.is_kill_zone()
        if not in_kz:
            print("  Outside Kill Zone - standby")
            return None, None, None
        print(f"  Kill Zone: {kz_name}")

        df_1h = self.bybit.get_ohlcv(symbol='BTC/USDT:USDT', timeframe='1h', limit=10)
        trend = self.get_trend(df_1h)
        print(f"  1H Trend: {trend.upper()}")

        if trend == "neutral":
            print("  Neutral trend - no trade")
            return None, None, None

        df_5m = self.bybit.get_ohlcv(symbol='BTC/USDT:USDT', timeframe='5m', limit=200)
        if df_5m is None:
            print("  Failed to fetch 5m data")
            return None, None, None

        price = self.bybit.get_price(symbol='BTC/USDT:USDT')
        print(f"  BTC Price: ${price:,.2f}")

        fvgs = self.detect_fvg(df_5m)
        recent_fvgs = [f for f in fvgs if len(df_5m) - f['idx'] <= 3]

        for fvg in recent_fvgs:
            if trend == "bullish" and fvg['type'] == 'bullish':
                if fvg['bottom'] <= price <= fvg['top']:
                    print(f"  LONG SIGNAL in Kill Zone {kz_name}")
                    return 'long', price, fvg

            if trend == "bearish" and fvg['type'] == 'bearish':
                if fvg['bottom'] <= price <= fvg['top']:
                    print(f"  SHORT SIGNAL in Kill Zone {kz_name}")
                    return 'short', price, fvg

        print("  No aligned setups")
        return None, None, None

    def run(self, iterations=None):
        print("\n" + "="*70)
        print("  ICT Kill Zone Scalper - MILESTONE 6 (Daily Drawdown Protection)")
        print("  Max Daily Loss: 5% of start-of-day balance")
        print("="*70)
        self.running = True
        count = 0
        try:
            while self.running:
                count += 1
                if iterations and count > iterations:
                    break

                signal, price, fvg_data = self.analyze_market()
                if signal:
                    self.execute_trade(signal, price, fvg_data)

                if self.running and (not iterations or count < iterations):
                    time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.running = False
            print("\nBot shutdown complete.")

if __name__ == "__main__":
    bot = KillZoneScalperBot(
                api_key=os.environ.get('BYBIT_API_KEY', ''),
                api_secret=os.environ.get('BYBIT_API_SECRET', ''),
        testnet=True
    )
    bot.run()
