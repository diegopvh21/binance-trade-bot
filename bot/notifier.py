from loguru import logger
from telegram import Bot
from bot.config import load_config

class Notifier:
    def __init__(self):
        config = load_config()
        self.token = config.get('telegram_bot_token')
        self.chat_id = config.get('telegram_chat_id')
        self.bot = Bot(token=self.token) if self.token else None

    def send(self, msg):
        if not self.bot or not self.chat_id:
            logger.warning("Telegram não configurado!")
            return
        try:
            self.bot.send_message(chat_id=self.chat_id, text=msg)
        except Exception as e:
            logger.error(f"Erro ao enviar Telegram: {e}")
