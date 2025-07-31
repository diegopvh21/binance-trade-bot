
import asyncio
from binance.client import Client
from binance.streams import BinanceSocketManager
from loguru import logger
from bot.config import load_config
from bot.strategies import StrategyManager
from bot.risk import RiskManager
from bot.notifier import Notifier
from bot.ia import IAManager

class WebSocketBot:
    def __init__(self):
        self.config = load_config()
        self.client = Client(self.config['binance_api_key'], self.config['binance_api_secret'])
        self.client.API_URL = self.config['binance_api_url']
        ambiente = 'TESTNET' if self.config.get('testnet') else 'PRODUÇÃO'
        logger.info(f"🔧 WebSocket conectando na Binance SPOT {ambiente}")

        self.symbols = [s.lower() for s in self.config['symbols']]
        self.bsm = BinanceSocketManager(self.client)
        self.strategies = StrategyManager(self.config)
        self.risk = RiskManager(self.config['risk'])
        self.notifier = Notifier()
        self.ia_manager = IAManager(self.config) if self.config['ia']['enabled'] else None

    async def handle_candles(self, msg):
        if msg.get('e') != 'kline':
            return
        k = msg['k']
        symbol = k['s']
        is_closed = k['x']
        close_price = float(k['c'])

        if is_closed:
            logger.info(f"[{symbol}] Candle fechado - Close: {close_price}")
            # IA (ajuste de parâmetros dinâmico)
            if self.ia_manager:
                self.ia_manager.check_and_update_params(symbol, self.strategies)

            # Estratégias daquele par
            strats = self.config['strategies'][symbol]
            for strat_name in strats:
                strategy = self.strategies.get_strategy(strat_name, symbol)
                signal = strategy.generate_signal()
                logger.info(f"[{symbol}] Estratégia {strat_name} => Sinal: {signal}")

                if self.config['mode'] == "trade" and signal in ["buy", "sell"]:
                    balance = self.client.get_asset_balance(asset='USDT')
                    qty = self.risk.calculate_position_size(symbol, float(balance['free']))
                    if qty > 0:
                        order = self.client.create_order(
                            symbol=symbol,
                            side=Client.SIDE_BUY if signal == "buy" else Client.SIDE_SELL,
                            type=Client.ORDER_TYPE_MARKET,
                            quantity=qty
                        )
                        logger.info(f"Ordem enviada: {order}")
                        self.notifier.send(f"🟢 [{symbol}] Ordem {signal} enviada via WS. Detalhes: {order}")
                    else:
                        logger.warning(f"Qtd insuficiente para operar {symbol}")

    async def start(self):
        streams = [f"{symbol}@kline_1m" for symbol in self.symbols]
        async with self.bsm.multiplex_socket(streams) as stream:
            while True:
                msg = await stream.recv()
                await self.handle_candles(msg)

if __name__ == "__main__":
    bot = WebSocketBot()
    asyncio.run(bot.start())
