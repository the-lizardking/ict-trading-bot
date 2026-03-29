import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

class AlertManager:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            print("⚠️ Telegram credentials missing. Alerts disabled.")

    def send_alert(self, message: str):
        if not self.enabled:
            return
        try:
            from telegram import Bot
            async def _send():
                async with Bot(token=self.token) as bot:
                    await bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode="Markdown"
                    )
            asyncio.run(_send())
        except Exception as e:
            print(f"⚠️ Alert failed: {e}")
