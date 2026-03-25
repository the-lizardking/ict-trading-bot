import ccxt
import pandas as pd

# Your Bybit testnet API keys
API_KEY = "RSjAyduYLxbWF592Rx"
API_SECRET = "YzWFJVg3JoHYfxeGjJiww9MXRc8L12LMNIz2"

# Initialize Bybit testnet
bybit = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'},
    'urls': {
        'api': {
            'public': 'https://api-testnet.bybit.com',
            'private': 'https://api-testnet.bybit.com'
        }
    }
})

print("🔗 Testing Bybit Testnet Connection...")
print()

# Test 1: Get balance
try:
    balance = bybit.fetch_balance()
    print("✅ Balance check successful!")
    if 'USDT' in balance:
        print(f"   Free USDT: {balance['USDT']['free']}")
        print(f"   Total USDT: {balance['USDT']['total']}")
except Exception as e:
    print(f"❌ Balance error: {e}")

print()

# Test 2: Get BTC price
try:
    ticker = bybit.fetch_ticker('BTC/USDT')
    print(f"✅ BTC Price: ${ticker['last']:,.2f}")
except Exception as e:
    print(f"❌ Price error: {e}")

print()

# Test 3: Get market data
try:
    ohlcv = bybit.fetch_ohlcv('BTC/USDT', '15m', limit=10)
    print(f"✅ Fetched {len(ohlcv)} candles")
    print(f"   Latest close: ${ohlcv[-1][4]:,.2f}")
except Exception as e:
    print(f"❌ OHLCV error: {e}")

print()
print("🎉 All tests complete!")


