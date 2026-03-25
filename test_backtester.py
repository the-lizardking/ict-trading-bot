import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from backtester import ICTBacktester

def make_data():
    rows = []
    ts = datetime(2026, 3, 22, 1, 0)
    price = 69500.0
    for _ in range(4320):
        o = price
        h = o + abs(float(np.random.normal(0, 5)))
        l = o - abs(float(np.random.normal(0, 5)))
        c = min(max(o + float(np.random.normal(-0.03, 8)), l), h)
        rows.append({
            'timestamp': int(ts.timestamp() * 1000),
            'open': round(o, 2),
            'high': round(h, 2),
            'low': round(l, 2),
            'close': round(c, 2),
            'volume': 5.0
        })
        price = c
        ts += timedelta(minutes=1)
    return pd.DataFrame(rows)

df = make_data()
bt = ICTBacktester(df)
trades = bt.run()
s = bt.summary()

print('\n' + '='*40 + ' SUMMARY ' + '='*40)
for k, v in s.items():
    print(f'  {k}: {v}')

print('\n' + '='*40 + ' TRADES ' + '='*40)
for i, t in enumerate(trades):
    sign = '+' if t['net_pnl'] >= 0 else ''
    print(f"  [{i+1}] {t['entry_time']} {t['direction'].upper():5s} "
          f"Entry:{t['entry_price']:>10.2f} SL:{t['stop_loss']:>10.2f} "
          f"TP:{t['take_profit']:>10.2f} Exit:{t['exit_price']:>10.2f} "
          f"({t['exit_reason']:12s}) R:{t['r_multiple']:>5.2f} "
          f"PnL:{sign}{t['net_pnl']:>8.2f} Bal:{t['capital_after']:>10.2f}")

if not trades:
    print('  No trades generated')

