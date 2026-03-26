#!/usr/bin/env python3
'''
Bybit API Configuration Template

INSTRUCTIONS:
1. Copy this file to: bybit_config.py (DO NOT commit bybit_config.py to git!)
2. Fill in your actual API credentials below
3. Add bybit_config.py to .gitignore to keep secrets safe

For Telegram bot setup:
1. Open Telegram, search @BotFather
2. Send /newbot, follow prompts to get BOT_TOKEN
3. Send a message to your bot, then visit:
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   to find your CHAT_ID in the JSON response
'''

# ========== BYBIT API CREDENTIALS ==========
# Get from: https://testnet.bybit.com/app/user/api-management (Testnet)
# Or: https://www.bybit.com/app/user/api-management (Live)

BYBIT_TESTNET_API_KEY = "YOUR_TESTNET_API_KEY_HERE"
BYBIT_TESTNET_API_SECRET = "YOUR_TESTNET_API_SECRET_HERE"

BYBIT_LIVE_API_KEY = "YOUR_LIVE_API_KEY_HERE"  # Use with caution!
BYBIT_LIVE_API_SECRET = "YOUR_LIVE_API_SECRET_HERE"  # Use with caution!

# ========== TELEGRAM PUSH NOTIFICATIONS ==========
# Required for Milestone 7 alert system

TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # From @BotFather
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"      # Your personal chat ID (numeric)

# ========== ALERT SETTINGS ==========

DAILY_SUMMARY_HOUR_UTC = 21  # Send daily summary at 21:00 UTC (end of trading day)
ENABLE_ENTRY_CHARTS = True   # Include matplotlib charts in entry alerts
ENABLE_ALERTS = True          # Master switch for all Telegram alerts
