#!/bin/bash
# =============================================================
# start.sh - ICT Trading Bot launcher for Oracle Cloud VM
# =============================================================
# Run this script to start the bot in the background.
# Logs are written to bot.log in the same directory.
# Usage: bash start.sh
# =============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment variables from ~/.bot_env if it exists
# (set up by setup.sh on first run)
if [ -f "$HOME/.bot_env" ]; then
    source "$HOME/.bot_env"
else
    echo "ERROR: ~/.bot_env not found. Please run setup.sh first."
    exit 1
fi

# Check that API keys are actually set
if [ -z "$BYBIT_API_KEY" ] || [ -z "$BYBIT_API_SECRET" ]; then
    echo "ERROR: BYBIT_API_KEY or BYBIT_API_SECRET is not set in ~/.bot_env"
    exit 1
fi

# Kill any previously running instance of the bot
OLD_PID=$(pgrep -f "automated_trading_loop.py")
if [ -n "$OLD_PID" ]; then
    echo "Stopping existing bot instance (PID $OLD_PID)..."
    kill "$OLD_PID"
    sleep 2
fi

# Start the bot in the background, append logs
echo "Starting ICT Trading Bot at $(date)..."
nohup python3 automated_trading_loop.py >> bot.log 2>&1 &

NEW_PID=$!
echo "Bot started with PID $NEW_PID"
echo "Logs: tail -f $SCRIPT_DIR/bot.log"
echo $NEW_PID > bot.pid
