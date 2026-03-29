import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def get_bybit_client():
    from pybit.unified_trading import HTTP
    return HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)

def is_authorised(update: Update) -> bool:
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    text = (
        "👋 *ICT Trading Bot*\n\n"
        "Available commands:\n"
        "/status  — Is the bot running?\n"
        "/balance — Current account balance\n"
        "/trades  — Open positions right now\n"
        "/log     — Last 20 log lines\n"
        "/price   — Current BTC price\n"
        "/help    — Show this menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    await update.message.reply_text(f"✅ ICT Trading Bot is LIVE on Oracle Cloud!\nMonitoring kill zones...\n🕐 {now}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit_client()
        resp = client.get_wallet_balance(accountType="UNIFIED")
        coins = resp["result"]["list"][0]["coin"]
        lines = [f"  {c['coin']}: {float(c['walletBalance']):.4f} (≈ ${float(c.get('usdValue','0')):.2f})"
                 for c in coins if float(c.get("walletBalance", 0)) > 0]
        text = "\n".join(lines) if lines else "No balance found."
        await update.message.reply_text(f"💰 *Account Balance:*\n{text}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        resp = requests.get("https://api.bybit.com/v5/market/tickers",
                            params={"category": "linear", "symbol": "BTCUSDT"}, timeout=10)
        price = float(resp.json()["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit_client()
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
        if not positions:
            await update.message.reply_text("📊 No open positions right now.")
            return
        lines = [f"  {p['symbol']} {p['side']} | Size: {p['size']} | Entry: ${float(p['avgPrice']):,.2f} | PnL: ${float(p['unrealisedPnl']):+.2f}"
                 for p in positions]
        await update.message.reply_text("📊 *Open Positions:*\n" + "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch trades: {e}")

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        log_file = os.path.join(os.path.dirname(__file__), "bot.log")
        if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
            await update.message.reply_text("📋 Log file is empty.")
            return
        with open(log_file, "r") as f:
            lines = f.readlines()
        last_lines = "".join(lines[-20:])[-3000:]
        await update.message.reply_text(f"📋 *Last 20 log lines:*\n```\n{last_lines}\n```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read log: {e}")

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",   "Show this menu"),
        BotCommand("status",  "Is the bot running?"),
        BotCommand("balance", "Current account balance"),
        BotCommand("trades",  "Open positions right now"),
        BotCommand("log",     "Last 20 log lines"),
        BotCommand("price",   "Current BTC price"),
        BotCommand("help",    "Show this menu"),
    ])

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("price",   cmd_price))
    app.add_handler(CommandHandler("trades",  cmd_trades))
    app.add_handler(CommandHandler("log",     cmd_log))
    print("Telegram Query Bot running. Send /start to begin.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
