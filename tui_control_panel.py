#!/usr/bin/env python3
"""
ICT Kill Zone Scalper - Terminal Control Panel (Milestone 5)
Run: python tui_control_panel.py
Requires: pip install rich ccxt
"""
import time
import threading
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich import box
from rich.layout import Layout

# Bot Modules
from bybit_connector import BybitConnector
from bybit_config import BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

console = Console()

KILL_ZONES = [
    {"name": "London Open", "start": 7, "end": 10},
    {"name": "NY Open", "start": 13, "end": 16},
    {"name": "London Close", "start": 15, "end": 17},
]

BOT_STATE = {
    "running": False,
    "mode": "PAPER",
    "price": 0.0,
    "trend": "neutral",
    "kill_zone": "None",
    "signal": "none",
    "balance": 0.0,
    "equity": 0.0,
    "trades": [],
    "pnl": 0.0,
    "win_rate": 0.0,
    "last_update": "--",
    "alerts": [],
}

# Initialize Connector
bybit = BybitConnector(BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET, testnet=True)

def is_kill_zone():
    now = datetime.utcnow()
    for kz in KILL_ZONES:
        if kz["start"] <= now.hour < kz["end"]:
            return kz["name"]
    return "None"

def build_dashboard():
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    state = BOT_STATE
    
    # Status panel
    running_str = "[green]RUNNING[/green]" if state["running"] else "[red]STOPPED[/red]"
    kz = state["kill_zone"]
    kz_color = "yellow" if kz != "None" else "dim"
    trend = state["trend"]
    trend_color = "green" if trend == "bullish" else "red" if trend == "bearish" else "dim"
    
    status_text = Text()
    status_text.append(f"Bot Status: ", style="bold")
    status_text.append(f"{running_str}
")
    status_text.append(f"Mode: ", style="bold")
    status_text.append(f"[green]TESTNET[/green]
")
    status_text.append(f"BTC Price: ", style="bold")
    status_text.append(f"${state['price']:,.2f}
")
    status_text.append(f"1H Trend: ", style="bold")
    status_text.append(f"[{trend_color}]{trend.upper()}[/{trend_color}]
")
    status_text.append(f"Kill Zone: ", style="bold")
    status_text.append(f"[{kz_color}]{kz}[/{kz_color}]
")
    status_text.append(f"Last Sync: ", style="bold")
    status_text.append(f"{state['last_update']}")
    
    status_panel = Panel(status_text, title="[bold cyan]Market Status[/bold cyan]", border_style="cyan", width=40)

    # Performance panel
    perf_text = Text()
    pnl_color = "green" if state["pnl"] >= 0 else "red"
    perf_text.append(f"Wallet Bal: ", style="bold")
    perf_text.append(f"${state['balance']:,.2f}
")
    perf_text.append(f"Total Equity: ", style="bold")
    perf_text.append(f"${state['equity']:,.2f}
")
    perf_text.append(f"Total P&L: ", style="bold")
    perf_text.append(f"[{pnl_color}]${state['pnl']:,.2f}[/{pnl_color}]
")
    perf_text.append(f"Win Rate: ", style="bold")
    perf_text.append(f"{state['win_rate']:.1f}%
")
    
    perf_panel = Panel(perf_text, title="[bold magenta]Account info[/bold magenta]", border_style="magenta", width=40)

    # Trade log table
    table = Table(title="Live Trades & History", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Time", style="dim")
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Price")
    table.add_column("Qty")
    table.add_column("Status")
    
    for trade in state["trades"][-10:]:
        side_color = "green" if trade.get("side") == "buy" else "red"
        table.add_row(
            trade.get("time", ""),
            trade.get("symbol", ""),
            f"[{side_color}]{trade.get('side', '').upper()}[/{side_color}]",
            f"${trade.get('price', 0):,.2f}",
            str(trade.get("qty", "")),
            trade.get("status", "")
        )
    
    if not state["trades"]:
        table.add_row("--", "--", "--", "--", "--", "--")

    alerts_text = "
".join(state["alerts"][-6:]) if state["alerts"] else "System initialization complete."
    alerts_panel = Panel(alerts_text, title="[bold yellow]System Log[/bold yellow]", border_style="yellow")

    header = Panel(
        f"[bold white]ICT Kill Zone Scalper — Terminal Control Panel[/bold white]
"
        f"[dim]{now_str}[/dim]",
        style="bold blue", box=box.DOUBLE_EDGE
    )

    layout = Layout()
    layout.split_column(
        Layout(header, size=4),
        Layout(name="middle", size=9),
        Layout(table, name="bottom", size=15),
        Layout(alerts_panel, size=8),
    )
    layout["middle"].split_row(
        Layout(status_panel),
        Layout(perf_panel),
    )
    return layout

def bot_worker():
    while BOT_STATE["running"]:
        try:
            # Update Price & Trend
            df_1h = bybit.get_ohlcv(symbol='BTC/USDT:USDT', timeframe='1h', limit=5)
            if df_1h is not None and len(df_1h) >= 2:
                last_close = df_1h.iloc[-1]['close']
                prev_high = df_1h.iloc[-2]['high']
                prev_low = df_1h.iloc[-2]['low']
                BOT_STATE["price"] = last_close
                if last_close > prev_high: BOT_STATE["trend"] = "bullish"
                elif last_close < prev_low: BOT_STATE["trend"] = "bearish"
                else: BOT_STATE["trend"] = "neutral"
            
            # Update Account Info
            balance = bybit.get_balance()
            if balance:
                usdt_data = balance['total'].get('USDT', 0)
                BOT_STATE["balance"] = float(usdt_data)
                # Equity calculation
                try:
                    BOT_STATE["equity"] = float(balance['info'].get('result', {}).get('list', [{}])[0].get('totalEquity', BOT_STATE["balance"]))
                except:
                    BOT_STATE["equity"] = BOT_STATE["balance"]
                BOT_STATE["pnl"] = BOT_STATE["equity"] - 100000.0

            # Update Orders/Trades
            try:
                orders = bybit.exchange.fetch_orders('BTC/USDT:USDT', limit=10)
                formatted_trades = []
                for o in orders:
                    formatted_trades.append({
                        "time": o['datetime'].split('T')[1][:8] if 'T' in o['datetime'] else o['datetime'],
                        "symbol": o['symbol'],
                        "side": o['side'],
                        "price": o['price'] or o['average'] or 0,
                        "qty": o['amount'],
                        "status": o['status']
                    })
                BOT_STATE["trades"] = formatted_trades
            except:
                pass

            BOT_STATE["kill_zone"] = is_kill_zone()
            BOT_STATE["last_update"] = datetime.utcnow().strftime("%H:%M:%S")
            
        except Exception as e:
            BOT_STATE["alerts"].append(f"{datetime.utcnow().strftime('%H:%M:%S')} | Error: {str(e)[:50]}")
        
        time.sleep(15)

def main():
    BOT_STATE["running"] = True
    worker = threading.Thread(target=bot_worker, daemon=True)
    worker.start()
    
    try:
        with Live(build_dashboard(), refresh_per_second=1, screen=True) as live:
            while BOT_STATE["running"]:
                time.sleep(1)
                live.update(build_dashboard())
    except KeyboardInterrupt:
        BOT_STATE["running"] = False
        console.print("
[bold red]Dashboard closed.[/bold red]")

if __name__ == "__main__":
    main()
