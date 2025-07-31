import os
import yaml
from dotenv import load_dotenv

def load_config():
    load_dotenv()
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    testnet = config.get('testnet', False)
    # Escolhe as chaves e URLs corretas conforme o ambiente
    if testnet:
        config['binance_api_key'] = os.getenv("BINANCE_API_KEY_TEST")
        config['binance_api_secret'] = os.getenv("BINANCE_API_SECRET_TEST")
        config['binance_api_url'] = os.getenv("BINANCE_API_URL_TEST", "https://testnet.binance.vision/api")
    else:
        config['binance_api_key'] = os.getenv("BINANCE_API_KEY_PROD")
        config['binance_api_secret'] = os.getenv("BINANCE_API_SECRET_PROD")
        config['binance_api_url'] = os.getenv("BINANCE_API_URL_PROD", "https://api.binance.com")
    config['telegram_bot_token'] = os.getenv("TELEGRAM_BOT_TOKEN")
    config['telegram_chat_id'] = os.getenv("TELEGRAM_CHAT_ID")
    return config
