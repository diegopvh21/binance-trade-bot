from binance.client import Client
from loguru import logger
from bot.config import load_config

class BinanceClient:
    def __init__(self):
        config = load_config()
        self.client = Client(config['binance_api_key'], config['binance_api_secret'])
        self.client.API_URL = config['binance_api_url']
        ambiente = 'TESTNET' if config.get('testnet') else 'PRODUÇÃO'
        logger.info(f"🔧 Conectando na Binance SPOT {ambiente}")

    def get_balance(self, asset):
        info = self.client.get_asset_balance(asset=asset)
        if info:
            return float(info['free'])
        return 0.0

    def get_ohlcv(self, symbol, interval="1m", limit=100):
        klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
        import pandas as pd
        df = pd.DataFrame(klines, columns=['open_time','open','high','low','close','volume','close_time','quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'])
        df['close'] = df['close'].astype(float)
        return df

    def place_order(self, symbol, side, quantity):
        side_binance = Client.SIDE_BUY if side == "buy" else Client.SIDE_SELL
        try:
            order = self.client.create_order(
                symbol=symbol,
                side=side_binance,
                type=Client.ORDER_TYPE_MARKET,
                quantity=quantity
            )
            logger.info(f"Ordem executada: {order}")
            return order
        except Exception as e:
            logger.error(f"Erro ao executar ordem: {e}")
            return None
