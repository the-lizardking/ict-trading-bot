#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "$HOME/.bot_env" ]; then
    source "$HOME/.bot_env"
else
    echo "ERROR: ~/.bot_env not found. Please run setup.sh first."
    exit 1
fi

if [ -z "$BYBIT_API_KEY" ] || [ -z "$BYBIT_API_SECRET" ]; then
    echo "ERROR: BYBIT_API_KEY or BYBIT_API_SECRET is not set in ~/.bot_env"
    exit 1
fi

# Kill existing instances
OLD_PID=$(pgrep -f "automated_trading_loop.py")
if [ -n "$OLD_PID" ]; then
    echo "Stopping existing bot instance (PID $OLD_PID)..."
    kill "$OLD_PID" && sleep 2
fi

OLD_QUERY_PID=$(pgrep -f "telegram_query_bot.py")
if [ -n "$OLD_QUERY_PID" ]; then
    echo "Stopping existing query bot (PID $OLD_QUERY_PID)..."
    kill "$OLD_QUERY_PID" && sleep 1
fi

echo "Starting ICT Trading Bot at $(date)..."
nohup python3 -B automated_trading_loop.py >> bot.log 2>&1 &
echo $! > bot.pid
echo "Bot started with PID $(cat bot.pid)"

echo "Starting Telegram Query Bot..."
nohup env TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
          TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID" \
          BYBIT_API_KEY="$BYBIT_API_KEY" \
          BYBIT_API_SECRET="$BYBIT_API_SECRET" \
          python3 -B telegram_query_bot.py >> bot_query.log 2>&1 &
echo "Query bot started with PID $!"

echo "Logs: tail -f $SCRIPT_DIR/bot.log"
echo "Query logs: tail -f $SCRIPT_DIR/bot_query.log"
