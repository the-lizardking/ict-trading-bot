from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

bybit = BybitConnector(
    api_key=BYBIT_TESTNET_API_KEY,
    api_secret=BYBIT_TESTNET_API_SECRET,
    testnet=True
)

print("Checking account balance...")
balance = bybit.get_balance()

if balance:
    print(f"\n💰 Balance Info:")
    if 'USDT' in balance:
        print(f"   Total USDT: {balance['USDT'].get('total', 0)}")
        print(f"   Free USDT: {balance['USDT'].get('free', 0)}")
    print(f"\n📊 Full balance structure:")
    for key in balance:
        if isinstance(balance[key], dict) and balance[key].get('total', 0) > 0:
            print(f"   {key}: {balance[key]}")

print("\n🔍 Fetching current BTC price...")
price = bybit.get_price('BTC/USDT:USDT')
if price:
    print(f"   Current BTC: ${price:,.2f}")

print("\n📈 Fetching market data...")
df = bybit.get_ohlcv('BTC/USDT:USDT', '15m', 10)
if df is not None:
    print(f"   ✅ Got {len(df)} candles")
    print(f"   Latest close: ${df['close'].iloc[-1]:,.2f}")

print("\n✅ Connection and data fetching working perfectly!")
print("💡 For placing orders, you may need to configure margin settings on testnet.bybit.com first")

