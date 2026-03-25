from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

bybit = BybitConnector(
    api_key=BYBIT_TESTNET_API_KEY,
    api_secret=BYBIT_TESTNET_API_SECRET,
    testnet=True
)

# Place a tiny test order (testnet - fake money!)
print("Placing test order...")
order = bybit.place_market_order('BTC/USDT:USDT', 'buy', 0.001)
if order:
    print(f"✅ Order placed! ID: {order['id']}")

