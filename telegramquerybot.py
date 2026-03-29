import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from pybit.unified_trading import HTTP

load_dotenv(dotenv_path=os.path.expanduser("~/.botenv"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def get_bybit():
    return HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)

def is_authorised(update: Update) -> bool:
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    await update.message.reply_text("✅ ICT Trading Bot online. Use /help to see commands.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    msg = (
        "📋 *Available Commands*\n\n"
        "/status — Bot status\n"
        "/balance — Wallet balance\n"
        "/trades — Open positions\n"
        "/price — Current BTC price\n"
        "/help — This menu"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    await update.message.reply_text(f"✅ Bot is running\n🕐 {now}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit()
        result = client.get_wallet_balance(accountType="UNIFIED")
        coins = result["result"]["list"][0]["coin"]
        usdt = next((c for c in coins if c["coin"] == "USDT"), None)
        if usdt:
            equity = float(usdt.get("equity", 0))
            pnl    = float(usdt.get("unrealisedPnl", 0))
            msg = f"💰 *Wallet Balance*\nUSDT Equity: `${equity:,.2f}`\nUnrealised PnL: `${pnl:,.2f}`"
        else:
            msg = "⚠️ No USDT balance found."
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit()
        result = client.get_positions(category="linear", settleCoin="USDT")
        positions = [p for p in result["result"]["list"] if float(p.get("size", 0)) > 0]
        if not positions:
            await update.message.reply_text("📭 No open positions.")
            return
        lines = ["📊 *Open Positions*\n"]
        for p in positions:
            lines.append(
                f"*{p['symbol']}* {p['side']}\n"
                f"  Size: `{p['size']}` | Entry: `{float(p['avgPrice']):.2f}`\n"
                f"  PnL: `${float(p.get('unrealisedPnl', 0)):.2f}`\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch trades: {e}")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit()
        result = client.get_tickers(category="linear", symbol="BTCUSDT")
        price = float(result["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT*: `${price:,.2f}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("status",  "Bot status"),
        BotCommand("balance", "Wallet balance"),
        BotCommand("trades",  "Open positions"),
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
    print("Telegram Query Bot running.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
