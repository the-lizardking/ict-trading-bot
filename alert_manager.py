#!/usr/bin/env python3
'''
ICT Trading Bot - Alert Manager (Milestone 7: Telegram Push Notifications)
Requires: pip install python-telegram-bot matplotlib mplfinance
'''
import io
import os
from datetime import datetime, time as dt_time
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from telegram import Bot
from telegram.error import TelegramError

# Import config
try:
    from bybit_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None

class AlertManager:
    '''
    Manages Telegram push notifications for the ICT trading bot.
    Sends entry/exit alerts with charts + daily summary reports.
    '''
    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = False
        self.last_daily_summary_date = None
        
        if self.bot_token and self.chat_id:
            try:
                self.bot = Bot(token=self.bot_token)
                self.enabled = True
                print("✅ Telegram AlertManager initialized.")
            except Exception as e:
                print(f"⚠️ Telegram bot init failed: {e}")
                self.enabled = False
        else:
            print("⚠️ Telegram credentials missing. Alerts disabled.")
    
    def _send_message(self, text, photo=None):
        """Helper: sends text + optional photo to Telegram."""
        if not self.enabled:
            return False
        try:
            if photo:
                self.bot.send_photo(chat_id=self.chat_id, photo=photo, caption=text, parse_mode='Markdown')
            else:
                self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='Markdown')
            return True
        except TelegramError as e:
            print(f"❌ Telegram send error: {e}")
            return False
    
    def _generate_entry_chart(self, df_5m, entry_price, sl_price, tp_price, fvg_zone, signal_type):
        """
        Generates a 5m candlestick chart with:
        - FVG zone shaded (green/red)
        - Entry/SL/TP horizontal lines
        Returns BytesIO object with PNG image.
        """
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Plot candlesticks
            mpf.plot(df_5m[-50:], type='candle', ax=ax, style='charles', volume=False)
            
            # Shade FVG zone
            fvg_color = 'green' if signal_type == 'long' else 'red'
            ax.axhspan(fvg_zone['bottom'], fvg_zone['top'], alpha=0.3, color=fvg_color, label='FVG Zone')
            
            # Plot entry/SL/TP lines
            ax.axhline(entry_price, color='blue', linestyle='--', linewidth=2, label=f'Entry: ${entry_price:,.2f}')
            ax.axhline(sl_price, color='red', linestyle='--', linewidth=2, label=f'SL: ${sl_price:,.2f}')
            ax.axhline(tp_price, color='green', linestyle='--', linewidth=2, label=f'TP: ${tp_price:,.2f}')
            
            ax.set_title(f'{signal_type.upper()} Setup - {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}', fontsize=14, fontweight='bold')
            ax.set_ylabel('Price (USD)', fontsize=12)
            ax.legend(loc='upper left')
            ax.grid(alpha=0.3)
            
            # Save to BytesIO
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            plt.close(fig)
            return buf
        except Exception as e:
            print(f"❌ Chart generation error: {e}")
            return None
    
    def send_entry_alert(self, signal_type, entry_price, sl_price, tp_price, qty, risk_amount, kill_zone, trend, fvg_zone, df_5m=None):
        """
        Sends ENTRY alert with:
        - Trade signal details (side, prices, qty, risk)
        - Kill zone, trend confirmation
        - FVG zone info
        - Chart with setup plotted (if df provided)
        """
        risk_pct = (abs(entry_price - sl_price) / entry_price) * 100
        rr_ratio = abs(tp_price - entry_price) / abs(entry_price - sl_price)
        
        message = f"""🚀 *TRADE ENTRY SIGNAL*

*Setup:* {signal_type.upper()} in {kill_zone}
*Trend:* {trend.upper()} (1H)

📊 *Trade Details:*
• Entry: ${entry_price:,.2f}
• Stop Loss: ${sl_price:,.2f}
• Take Profit: ${tp_price:,.2f}
• Quantity: {qty} BTC
• Risk: ${risk_amount:,.2f} ({risk_pct:.2f}%)
• R:R Ratio: {rr_ratio:.2f}:1

📍 *FVG Zone:*
• Type: {fvg_zone['type'].capitalize()}
• Top: ${fvg_zone['top']:,.2f}
• Bottom: ${fvg_zone['bottom']:,.2f}

⏰ {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
"""
        
        # Generate chart if data provided
        chart_image = None
        if df_5m is not None and len(df_5m) > 0:
            chart_image = self._generate_entry_chart(df_5m, entry_price, sl_price, tp_price, fvg_zone, signal_type)
        
        success = self._send_message(message, photo=chart_image)
        if success:
            print("✅ Entry alert sent to Telegram.")
        return success
    
    def send_exit_alert(self, entry_price, exit_price, side, qty, sl_price, tp_price, close_reason, entry_time, exit_time, fees_estimate=0.0):
        """
        Sends EXIT alert with:
        - Entry/exit prices
        - Gross/Net P&L
        - Close reason (TP / SL / Manual)
        - Fees, hold duration, RR achieved
        """
        if side == 'buy':
            gross_pnl = (exit_price - entry_price) * qty
        else:  # sell/short
            gross_pnl = (entry_price - exit_price) * qty
        
        net_pnl = gross_pnl - fees_estimate
        hold_duration = (exit_time - entry_time).total_seconds() / 60  # minutes
        
        risk = abs(entry_price - sl_price) * qty
        reward_achieved = gross_pnl
        rr_achieved = reward_achieved / risk if risk > 0 else 0
        
        outcome_emoji = "✅" if net_pnl >= 0 else "❌"
        pnl_sign = "+" if net_pnl >= 0 else ""
        
        message = f"""{outcome_emoji} *TRADE CLOSED*

*Side:* {side.upper()}
*Closed by:* {close_reason.upper()}

💰 *P&L Summary:*
• Entry: ${entry_price:,.2f}
• Exit: ${exit_price:,.2f}
• Gross P&L: {pnl_sign}${gross_pnl:,.2f}
• Fees: ${fees_estimate:,.2f}
• *Net P&L:* *{pnl_sign}${net_pnl:,.2f}*

📈 *Performance:*
• Quantity: {qty} BTC
• R:R Achieved: {rr_achieved:.2f}:1
• Hold Time: {hold_duration:.1f} min

⏰ Entry: {entry_time.strftime("%H:%M:%S")} UTC
⏰ Exit: {exit_time.strftime("%H:%M:%S")} UTC
"""
        
        success = self._send_message(message)
        if success:
            print("✅ Exit alert sent to Telegram.")
        return success
    
    def send_daily_summary(self, total_trades, wins, losses, win_rate, total_pnl_gross, total_pnl_net, largest_win, largest_loss, daily_dd_used_pct, kill_zones_active):
        """
        Sends DAILY SUMMARY at end of day:
        - Total trades, win rate
        - Gross/net P&L
        - Largest win/loss
        - Daily drawdown usage
        - Kill zones active
        """
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        
        message = f"""📊 *DAILY TRADING SUMMARY*
_{today_str}_

*Trade Statistics:*
• Total Trades: {total_trades}
• Wins: {wins} | Losses: {losses}
• Win Rate: {win_rate:.1f}%

💰 *P&L:*
• Gross: ${total_pnl_gross:+,.2f}
• Net: ${total_pnl_net:+,.2f}
• Largest Win: ${largest_win:,.2f}
• Largest Loss: ${largest_loss:,.2f}

⚠️ *Risk Management:*
• Daily Drawdown Used: {daily_dd_used_pct:.1f}% / 100%

⏰ *Kill Zones Active:*
{kill_zones_active}

───────────────────
_End of trading day report_
"""
        
        success = self._send_message(message)
        if success:
            print("✅ Daily summary sent to Telegram.")
            self.last_daily_summary_date = datetime.utcnow().date()
        return success
    
    def should_send_daily_summary(self, summary_hour_utc=21):
        """
        Returns True if it's past the summary hour and we haven't sent today's summary yet.
        Default: 21:00 UTC (end of trading day)
        """
        now = datetime.utcnow()
        today = now.date()
        
        # Check if we've already sent today
        if self.last_daily_summary_date == today:
            return False
        
        # Check if it's past the summary hour
        if now.hour >= summary_hour_utc:
            return True
        
        return False
