#!/usr/bin/env python3
'''ICT Automated Trading Bot - Main Loop'''

import time
import pandas as pd
from datetime import datetime
from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET


class SimplifiedICTBot:
    '''Simplified automated trading bot with ICT strategy'''
    
    def __init__(self, api_key, api_secret, testnet=True):
        self.bybit = BybitConnector(api_key, api_secret, testnet)
        self.running = False
        self.check_interval = 60  # 1 minute for testing
        
    def detect_fvg(self, df):
        '''Detect Fair Value Gaps'''
        fvgs = []
        for i in range(2, len(df)):
            # Bullish FVG
            if df.iloc[i-2]['low'] > df.iloc[i]['high']:
                fvgs.append({
                    'type': 'bullish',
                    'top': df.iloc[i-2]['low'],
                    'bottom': df.iloc[i]['high'],
                    'idx': i
                })
            # Bearish FVG
            if df.iloc[i]['low'] > df.iloc[i-2]['high']:
                fvgs.append({
                    'type': 'bearish',
                    'top': df.iloc[i]['low'],
                    'bottom': df.iloc[i-2]['high'],
                    'idx': i
                })
        return fvgs
    
    def analyze_market(self):
        '''Fetch and analyze market'''
        print(f"\n{'='*70}")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}")
        
        df = self.bybit.get_ohlcv('BTC/USDT:USDT', '15m', 500)
        if df is None:
            print("❌ Failed to fetch data")
            return None, None
        
        price = df.iloc[-1]['close']
        print(f"✅ Current BTC Price: ${price:,.2f}")
        
        fvgs = self.detect_fvg(df)
        print(f"📊 Found {len(fvgs)} Fair Value Gaps")
        
        # Check if price is in any recent FVG
        recent_fvgs = [f for f in fvgs if len(df) - f['idx'] < 50]
        
        for fvg in recent_fvgs[-5:]:
            if fvg['bottom'] <= price <= fvg['top']:
                print(f"\n🎯 SIGNAL: Price in {fvg['type'].upper()} FVG!")
                print(f"   Entry: ${price:,.2f}")
                print(f"   Zone: ${fvg['bottom']:,.2f} - ${fvg['top']:,.2f}")
                return 'signal', fvg
        
        print("⚪ No signals - waiting...")
        return None, None
    
    def run(self, iterations=None):
        '''Run the bot'''
        print("\n" + "="*70)
        print("🤖 ICT AUTOMATED TRADING BOT STARTED")
        print("="*70)
        print(f"📊 Monitoring: BTC/USDT:USDT (15m timeframe)")
        print(f"⏰ Check interval: {self.check_interval} seconds")
        print(f"🧪 Mode: TESTNET (Paper Trading)")
        print("="*70)
        
        self.running = True
        count = 0
        
        try:
            while self.running:
                count += 1
                
                if iterations and count > iterations:
                    break
                
                # Analyze market
                signal, data = self.analyze_market()
                
                if signal:
                    print("\n⚠️  Trade signal detected!")
                    print("   (Manual execution required for safety)")
                
                # Wait before next check
                if self.running and (not iterations or count < iterations):
                    print(f"\n💤 Sleeping for {self.check_interval} seconds...")
                    print(f"   Press Ctrl+C to stop")
                    time.sleep(self.check_interval)
                    
        except KeyboardInterrupt:
            print("\n\n🛑 Bot stopped by user")
            self.running = False
        
        print("\n" + "="*70)
        print("✅ Bot shutdown complete")
        print("="*70)


if __name__ == "__main__":
    # Initialize bot
    bot = SimplifiedICTBot(
        api_key=BYBIT_TESTNET_API_KEY,
        api_secret=BYBIT_TESTNET_API_SECRET,
        testnet=True
    )
    
    # Run bot (3 iterations for testing)
    print("\n🚀 Starting bot in TEST MODE (3 iterations)...")
    print("   For continuous operation, change iterations=None in code")
    
    bot.run(iterations=3)
