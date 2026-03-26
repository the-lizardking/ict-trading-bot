import sqlite3
from datetime import datetime, timedelta
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from bybit_connector import BybitConnector
from bybit_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

ALLOWED_CHAT_ID = int(TELEGRAM_CHAT_ID)

def auth(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ALLOWED_CHAT_ID:
            return
        await func(update, ctx)
    return wrapper

_bot_instance = None
def set_bot_instance(bot):
    global _bot_instance
    _bot_instance = bot

bybit = BybitConnector(BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET, testnet=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = ("*ICT Bot Control Panel*\n\n"
            "/status    - Is the bot running?\n"
            "/balance   - Current account balance\n"
            "/trades    - Open positions right now\n"
            "/log       - Trade log (last 24 hours)\n"
            "/price     - Current BTC price\n"
            "/help      - Show this menu")
    await update.message.reply_text(menu, parse_mode='Markdown')

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if _bot_instance is not None:
        running = _bot_instance.running
        dd_hit = _bot_instance.daily_limit_hit
        daily_pnl = _bot_instance.daily_realized_pnl
        open_trades = sum(1 for t in _bot_instance.trade_log if t.get('status') == 'open')
        msg = (f"*Bot Status*\n"
               f"Running: {'YES' if running else 'NO'}\n"
               f"Open trades: {open_trades}\n"
               f"Daily drawdown: {'HIT' if dd_hit else 'OK'}\n"
               f"Daily P&L: ${daily_pnl:+,.2f}\n{now_utc}")
    else:
        try:
            price = bybit.get_price()
            conn_ok = price is not None
        except Exception:
            conn_ok = False
        msg = (f"*Bot Status*\n"
               f"Query bot: Running\n"
               f"Trading loop: Not attached\n"
               f"Bybit connectivity: {'OK' if conn_ok else 'FAILED'}\n"
               f"{now_utc}")
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching balance...")
    try:
        data = bybit.get_balance()
        usdt = float(data['total'].get('USDT', 0))
        free = float(data['free'].get('USDT', 0))
        used = float(data['used'].get('USDT', 0))
        msg = f"*Account Balance*\nTotal: ${usdt:,.2f}\nAvailable: ${free:,.2f}\nIn margin: ${used:,.2f}"
    except Exception as e:
        msg = f"Failed to fetch balance: {e}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        price = bybit.get_price()
        msg = f"*BTC/USDT* ${price:,.2f}" if price else "Could not fetch price"
    except Exception as e:
        msg = f"Price fetch failed: {e}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _bot_instance is not None:
        open_trades = [t for t in _bot_instance.trade_log if t.get('status') == 'open']
        if not open_trades:
            await update.message.reply_text("No open trades right now.")
            return
        lines = [f"Open Trades ({len(open_trades)})"]
        for i, t in enumerate(open_trades, 1):
            lines.append(f"#{i} {t['side'].upper()} {t['qty']} BTC @ ${t['entry']:,.2f} SL:${t['sl']:,.2f} TP:${t['tp']:,.2f}")
        await update.message.reply_text("\n".join(lines))
        return
    await update.message.reply_text("Fetching live positions...")
    try:
        positions = bybit.exchange.fetch_positions(['BTC/USDT:USDT'])
        active = [p for p in positions if float(p.get('contracts', 0)) != 0]
        if not active:
            await update.message.reply_text("No open positions on Bybit.")
            return
        lines = [f"Live Positions ({len(active)})"]
        for p in active:
            pnl = float(p.get('unrealizedPnl', 0) or 0)
            lines.append(f"{p['side'].upper()} {p['contracts']} BTC @ ${float(p['entryPrice']):,.2f} uPnL:${pnl:+,.2f}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Failed to fetch positions: {e}")

async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    if _bot_instance is not None and _bot_instance.trade_log:
        recent = [t for t in _bot_instance.trade_log if t['timestamp'] >= cutoff]
        if not recent:
            await update.message.reply_text("No trades in the last 24 hours.")
            return
        lines = [f"Trade Log Last 24h ({len(recent)} trades)"]
        for t in recent[-10:]:
            pnl_str = f"${t['pnl']:+,.2f}" if 'pnl' in t else "open"
            lines.append(f"{t['timestamp'].strftime('%H:%M')} {t['side'].upper()} @ ${t['entry']:,.2f} {pnl_str} [{t['status']}]")
        await update.message.reply_text("\n".join(lines))
        return
    await update.message.reply_text("Reading trade journal...")
    try:
        conn = sqlite3.connect('trade_journal.db')
        cur = conn.cursor()
        cur.execute("SELECT timestamp, symbol, side, entry_price, exit_price, pnl, status FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 20",
                    (cutoff.strftime('%Y-%m-%d %H:%M:%S'),))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("No trades in last 24h.")
            return
        total_pnl = sum(float(r[5]) if r[5] else 0 for r in rows)
        lines = [f"Trade Log Last 24h ({len(rows)} trades)"]
        for r in rows:
            ts, sym, side, entry, exit_p, pnl, status = r
            lines.append(f"{ts[11:16]} {side.upper()} @ ${float(entry):,.2f} ${float(pnl or 0):+,.2f} [{status}]")
        lines.append(f"Total P&L: ${total_pnl:+,.2f}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Could not read trade journal: {e}")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("price",   cmd_price))
    app.add_handler(CommandHandler("trades",  cmd_trades))
    app.add_handler(CommandHandler("log",     cmd_log))
    print("Telegram Query Bot running. Send /start to begin.")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
