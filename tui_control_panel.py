#!/usr/bin/env python3
"""
ICT Kill Zone Scalper - Terminal Control Panel
Run: python tui_control_panel.py
Requires: pip install rich
"""

import time
import threading
import requests
import pandas as pd
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.live import Live
from rich.text import Text
from rich import box

console = Console()

KILL_ZONES = [
    {"name": "London Open",  "start": 7,  "end": 10},
    {"name": "NY Open",      "start": 13, "end": 16},
    {"name": "London Close", "start": 15, "end": 17},
]

BOT_STATE = {
    "running": False,
    "mode": "PAPER",
    "price": 0.0,
    "trend": "neutral",
    "kill_zone": "None",
    "signal": "none",
    "fvg_count": 0,
    "trades": [],
    "pnl": 0.0,
    "win_rate": 0.0,
    "last_update": "--",
    "alerts": [],
}


def fetch_price():
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": "BTCUSDT", "interval": "5", "limit": 3},
            timeout=5
        )
        candles = r.json().get("result", {}).get("list", [])
        if candles:
            return float(candles[0][4])  # most recent close
    except:
        pass
    return 0.0


def get_trend():
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": "BTCUSDT", "interval": "60", "limit": 3},
            timeout=5
        )
        candles = r.json().get("result", {}).get("list", [])
        if len(candles) >= 2:
            last_close = float(candles[0][4])
            prev_high = float(candles[1][2])
            prev_low = float(candles[1][3])
            if last_close > prev_high:
                return "bullish"
            elif last_close < prev_low:
                return "bearish"
    except:
        pass
    return "neutral"


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
    mode_color = "green" if state["mode"] == "PAPER" else "red"
    running_str = "[green]RUNNING[/green]" if state["running"] else "[red]STOPPED[/red]"
    kz = state["kill_zone"]
    kz_color = "yellow" if kz != "None" else "dim"
    trend = state["trend"]
    trend_color = "green" if trend == "bullish" else "red" if trend == "bearish" else "dim"
    signal = state["signal"]
    signal_color = "bright_green" if signal == "long" else "bright_red" if signal == "short" else "dim"

    status_text = Text()
    status_text.append(f"Bot Status:   ", style="bold")
    status_text.append(f"{running_str}
")
    status_text.append(f"Mode:         ", style="bold")
    status_text.append(f"[{mode_color}]{state['mode']}[/{mode_color}]
")
    status_text.append(f"BTC Price:    ", style="bold")
    status_text.append(f"${state['price']:,.2f}
")
    status_text.append(f"1H Trend:     ", style="bold")
    status_text.append(f"[{trend_color}]{trend.upper()}[/{trend_color}]
")
    status_text.append(f"Kill Zone:    ", style="bold")
    status_text.append(f"[{kz_color}]{kz}[/{kz_color}]
")
    status_text.append(f"Signal:       ", style="bold")
    status_text.append(f"[{signal_color}]{signal.upper()}[/{signal_color}]
")
    status_text.append(f"FVGs Found:   ", style="bold")
    status_text.append(f"{state['fvg_count']}
")
    status_text.append(f"Updated:      ", style="bold")
    status_text.append(f"{state['last_update']}")

    status_panel = Panel(status_text, title="[bold cyan]Market Status[/bold cyan]",
                         border_style="cyan", width=40)

    # Performance panel
    perf_text = Text()
    pnl_color = "green" if state["pnl"] >= 0 else "red"
    perf_text.append(f"Total Trades: ", style="bold")
    perf_text.append(f"{len(state['trades'])}
")
    perf_text.append(f"Win Rate:     ", style="bold")
    perf_text.append(f"{state['win_rate']:.1f}%
")
    perf_text.append(f"Total P&L:    ", style="bold")
    perf_text.append(f"[{pnl_color}]${state['pnl']:,.2f}[/{pnl_color}]
")

    perf_panel = Panel(perf_text, title="[bold magenta]Performance[/bold magenta]",
                       border_style="magenta", width=40)

    # Trade log table
    table = Table(title="Recent Trades", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Time", style="dim", width=18)
    table.add_column("Dir", width=6)
    table.add_column("Entry", width=12)
    table.add_column("P&L", width=10)
    table.add_column("Result", width=8)

    for trade in state["trades"][-8:]:
        dir_color = "green" if trade.get("dir") == "LONG" else "red"
        pnl_val = trade.get("pnl", 0)
        pnl_c = "green" if pnl_val >= 0 else "red"
        res = "WIN" if pnl_val > 0 else "LOSS"
        res_c = "green" if pnl_val > 0 else "red"
        table.add_row(
            trade.get("time", ""),
            f"[{dir_color}]{trade.get('dir', '-')}[/{dir_color}]",
            f"${trade.get('entry', 0):,.0f}",
            f"[{pnl_c}]${pnl_val:,.2f}[/{pnl_c}]",
            f"[{res_c}]{res}[/{res_c}]"
        )

    if not state["trades"]:
        table.add_row("--", "--", "--", "--", "--")

    # Alerts
    alerts_text = "\n".join(state["alerts"][-5:]) if state["alerts"] else "No alerts"
    alerts_panel = Panel(alerts_text, title="[bold yellow]Alerts / Log[/bold yellow]",
                         border_style="yellow")

    header = Panel(
        f"[bold white]ICT Kill Zone Scalper — Terminal Control Panel[/bold white]
"
        f"[dim]{now_str}[/dim]",
        style="bold blue", box=box.DOUBLE_EDGE
    )

    from rich.layout import Layout
    layout = Layout()
    layout.split_column(
        Layout(header, size=5),
        Layout(name="middle", size=12),
        Layout(table, size=12),
        Layout(alerts_panel, size=8),
    )
    layout["middle"].split_row(
        Layout(status_panel),
        Layout(perf_panel),
    )
    return layout


def bot_worker():
    import requests
    while BOT_STATE["running"]:
        try:
            BOT_STATE["price"] = fetch_price()
            BOT_STATE["trend"] = get_trend()
            BOT_STATE["kill_zone"] = is_kill_zone()
            BOT_STATE["last_update"] = datetime.utcnow().strftime("%H:%M:%S")

            if BOT_STATE["kill_zone"] != "None" and BOT_STATE["trend"] != "neutral":
                BOT_STATE["alerts"].append(
                    f"{BOT_STATE['last_update']} | KZ: {BOT_STATE['kill_zone']} | Trend: {BOT_STATE['trend']}")

        except Exception as e:
            BOT_STATE["alerts"].append(f"Error: {e}")
        time.sleep(30)


def main():
    console.print(Panel("[bold cyan]ICT Kill Zone Scalper TUI[/bold cyan]",
                         subtitle="Press Ctrl+C to exit"))
    console.print()
    console.print("[bold]Commands:[/bold]")
    console.print("  [green]s[/green] - Start bot   [red]x[/red] - Stop bot   [yellow]q[/yellow] - Quit")
    console.print()

    cmd = input("Press Enter to start live dashboard, or 'q' to quit: ").strip().lower()
    if cmd == "q":
        return

    BOT_STATE["running"] = True
    worker = threading.Thread(target=bot_worker, daemon=True)
    worker.start()

    try:
        with Live(build_dashboard(), refresh_per_second=0.5, screen=True) as live:
            while BOT_STATE["running"]:
                time.sleep(2)
                live.update(build_dashboard())
    except KeyboardInterrupt:
        BOT_STATE["running"] = False
        console.print("\n[bold red]Bot stopped.[/bold red]")


if __name__ == "__main__":
    main()