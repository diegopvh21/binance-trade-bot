import time
from loguru import logger

from bot.config import load_config
from bot.binance_client import BinanceClient
from bot.strategies import StrategyManager
from bot.risk import RiskManager
from bot.notifier import Notifier
from bot.ia import IAManager

def main():
    config = load_config()
    logger.add("trade.log", rotation="10 MB", level=config['log_level'])

    # Inicialização
    binance = BinanceClient()
    notifier = Notifier()
    risk = RiskManager(config['risk'])
    strategies = StrategyManager(config)
    ia_manager = IAManager(config) if config['ia']['enabled'] else None

    logger.info("Bot iniciado no modo: {}", config['mode'])
    notifier.send(f"🚀 Bot iniciado no modo: {config['mode']}")

    # Loop principal
    while True:
        for symbol in config['symbols']:
            try:
                # Ajuste automático via IA (pode ser feito em background)
                if ia_manager:
                    ia_manager.check_and_update_params(symbol, strategies)

                # Escolhe estratégia(s) do config
                strats = config['strategies'][symbol]
                for strat_name in strats:
                    strategy = strategies.get_strategy(strat_name, symbol)
                    signal = strategy.generate_signal()
                    logger.info(f"[{symbol}] Estratégia {strat_name} => Sinal: {signal}")

                    # Se não for para operar, só loga
                    if config['mode'] == "backtest":
                        continue

                    # Valida e executa trade
                    if signal in ["buy", "sell"]:
                        qty = risk.calculate_position_size(symbol, binance.get_balance('USDT'))
                        if qty > 0:
                            order = binance.place_order(symbol, signal, qty)
                            logger.info(f"Ordem enviada: {order}")
                            notifier.send(f"🟢 [{symbol}] Ordem {signal} enviada. Detalhes: {order}")
                        else:
                            logger.warning(f"Qtd insuficiente para operar {symbol}")
            except Exception as e:
                logger.exception(f"Erro no loop principal para {symbol}: {e}")
                notifier.send(f"❌ Erro ao operar {symbol}: {e}")

        # Sleep de acordo com timeframe das estratégias, ex: 1 minuto
        time.sleep(60)

if __name__ == "__main__":
    main()
