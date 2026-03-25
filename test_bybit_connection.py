from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

print('='*70)
print('🚀 TESTING BYBIT TESTNET CONNECTION')
print('='*70)
print()

bybit = BybitConnector(
    api_key=BYBIT_TESTNET_API_KEY,
    api_secret=BYBIT_TESTNET_API_SECRET,
    testnet=True
)
print()

print('📈 Test 1: Fetching BTC price...')
price = bybit.get_price('BTC/USDT:USDT')
if price:
    print(f'   ✅ Current BTC Price: ${price:,.2f}')
print()

print('📊 Test 2: Fetching 15-minute candles...')
df = bybit.get_ohlcv('BTC/USDT:USDT', '15m', 100)
if df is not None:
    print(f'   ✅ Loaded {len(df)} candles')
    print(f'   Time range: {df.timestamp.min()} to {df.timestamp.max()}')
    print(f'   Price range: ${df.low.min():,.2f} - ${df.high.max():,.2f}')
print()

print('💰 Test 3: Checking account balance...')
balance = bybit.get_balance()
if balance:
    print('   ✅ Balance retrieved successfully!')
    if 'USDT' in balance:
        usdt_balance = balance['USDT']
        print(f"   Free USDT: {usdt_balance.get('free', 0)}")
        print(f"   Total USDT: {usdt_balance.get('total', 0)}")
print()

print('='*70)
print('✅ ALL TESTS COMPLETE!')
print('='*70)
