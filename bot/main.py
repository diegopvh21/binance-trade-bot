import time
from loguru import logger

from bot.config import load_config
from bot.binance_client import BinanceClient
from bot.strategies import StrategyManager
from bot.risk import RiskManager
from bot.notifier import Notifier
from bot.ia import IAManager
from bot.market_data import MarketDataService

def main():
    config = load_config()
    logger.add("trade.log", rotation="10 MB", level=config['log_level'])

    binance = BinanceClient()
    notifier = Notifier()
    risk = RiskManager(config['risk'])
    mds = MarketDataService(maxlen=2000)
    strategies = StrategyManager(config, mds)
    ia_manager = IAManager(config) if config.get('ia', {}).get('enabled') else None

    # Bootstrap do cache via REST para cada símbolo
    for symbol in config['symbols']:
        mds.bootstrap_from_rest(binance.client, symbol, "1m", limit=200)

    logger.info("Bot iniciado no modo: {}", config['mode'])
    notifier.send(f"🚀 Bot iniciado no modo: {config['mode']}")

    while True:
        for symbol in config['symbols']:
            try:
                if ia_manager:
                    ia_manager.check_and_update_params(symbol, strategies)

                strats = config['strategies'][symbol]
                for strat_name in strats:
                    strategy = strategies.get_strategy(strat_name, symbol)
                    signal = strategy.generate_signal()
                    logger.info(f"[{symbol}] Estratégia {strat_name} => Sinal: {signal}")

                    if config['mode'] != "trade":
                        continue

                    if signal in ["buy", "sell"]:
                        qty_usdt = risk.position_size_from_balance(binance.get_balance('USDT'))
                        # Para manter consistência com a execução centralizada, favor rodar ws_manager.py
                        logger.info(f"[{symbol}] Sinal {signal} (modo polling). Tamanho alvo ~ {qty_usdt:.2f} USDT")
            except Exception as e:
                logger.exception(f"Erro no loop principal para {symbol}: {e}")
                notifier.send(f"❌ Erro ao operar {symbol}: {e}")

        time.sleep(60)

if __name__ == "__main__":
    main()
