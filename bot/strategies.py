import pandas as pd
import ta

from bot.binance_client import BinanceClient
from bot.config import load_config

class EMACrossStrategy:
    def __init__(self, symbol, fast_period, slow_period):
        self.symbol = symbol
        self.fast = fast_period
        self.slow = slow_period
        self.client = BinanceClient()

    def generate_signal(self):
        df = self.client.get_ohlcv(self.symbol, interval="1m", limit=self.slow+10)
        df['ema_fast'] = ta.trend.ema_indicator(df['close'], self.fast)
        df['ema_slow'] = ta.trend.ema_indicator(df['close'], self.slow)
        if df['ema_fast'].iloc[-2] < df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1]:
            return "buy"
        if df['ema_fast'].iloc[-2] > df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1]:
            return "sell"
        return "hold"

class RSIStrategy:
    def __init__(self, symbol, period, overbought, oversold):
        self.symbol = symbol
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.client = BinanceClient()

    def generate_signal(self):
        df = self.client.get_ohlcv(self.symbol, interval="1m", limit=self.period+10)
        df['rsi'] = ta.momentum.rsi(df['close'], self.period)
        last_rsi = df['rsi'].iloc[-1]
        if last_rsi > self.overbought:
            return "sell"
        if last_rsi < self.oversold:
            return "buy"
        return "hold"

class StrategyManager:
    def __init__(self, config):
        self.config = config
        self._strategies = {}

    def get_strategy(self, name, symbol):
        # Singleton para reusar instâncias
        key = (name, symbol)
        if key not in self._strategies:
            params = self.config.get(name, {})
            if name == "ema_cross":
                self._strategies[key] = EMACrossStrategy(symbol, params['fast_period'], params['slow_period'])
            elif name == "rsi":
                self._strategies[key] = RSIStrategy(symbol, params['period'], params['overbought'], params['oversold'])
            # Aqui pode adicionar outras estratégias!
        return self._strategies[key]
