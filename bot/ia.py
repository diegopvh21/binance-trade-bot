import pandas as pd
import numpy as np
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from bot.binance_client import BinanceClient

class IAManager:
    def __init__(self, config):
        self.config = config
        self.last_retrain = None

    def check_and_update_params(self, symbol, strategies_manager):
        # Reajusta parâmetros a cada X dias (pode sofisticar usando schedule ou threads)
        import datetime
        now = datetime.datetime.now()
        if self.last_retrain and (now - self.last_retrain).days < self.config['ia']['retrain_every']:
            return
        self.last_retrain = now

        # Exemplo para RSI: encontra o melhor overbought/oversold via backtest e IA
        logger.info(f"[IA] Otimizando parâmetros RSI para {symbol}...")
        client = BinanceClient()
        df = client.get_ohlcv(symbol, interval="1m", limit=500)
        closes = df['close']

        # Parâmetros a testar
        best_profit = -np.inf
        best_overbought = None
        best_oversold = None
        for overbought in range(60, 85, 5):
            for oversold in range(15, 40, 5):
                profit = self._simulate_rsi(closes, 14, overbought, oversold)
                if profit > best_profit:
                    best_profit = profit
                    best_overbought = overbought
                    best_oversold = oversold

        # Atualiza config em runtime (pode salvar ou sugerir ao usuário via painel)
        logger.info(f"[IA] Novo RSI {symbol}: overbought={best_overbought}, oversold={best_oversold}")
        self.config['rsi']['overbought'] = best_overbought
        self.config['rsi']['oversold'] = best_oversold

    def _simulate_rsi(self, closes, period, overbought, oversold):
        import ta
        rsi = ta.momentum.rsi(pd.Series(closes), period)
        in_trade = False
        balance = 100
        for i in range(period+1, len(rsi)):
            if not in_trade and rsi[i] < oversold:
                buy_price = closes.iloc[i]
                in_trade = True
            elif in_trade and rsi[i] > overbought:
                balance *= closes.iloc[i] / buy_price
                in_trade = False
        return balance - 100
