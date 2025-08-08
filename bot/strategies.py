# bot/strategies.py
import ta
from bot.market_data import MarketDataService

class EMACrossStrategy:
    def __init__(self, symbol, fast_period, slow_period, mds: MarketDataService, interval="1m"):
        self.symbol = symbol
        self.fast = fast_period
        self.slow = slow_period
        self.mds = mds
        self.interval = interval

    def generate_signal(self):
        df = self.mds.get_df(self.symbol, self.interval)
        if df.empty or len(df) < (self.slow + 2):
            return "hold"
        df = df.copy()
        df['ema_fast'] = ta.trend.ema_indicator(df['close'], self.fast)
        df['ema_slow'] = ta.trend.ema_indicator(df['close'], self.slow)

        cruzou_pra_cima = df['ema_fast'].iloc[-2] < df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1]
        if cruzou_pra_cima:
            return "buy"

        cruzou_pra_baixo = df['ema_fast'].iloc[-2] > df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1]
        if cruzou_pra_baixo:
            return "sell"

        return "hold"

class RSIStrategy:
    def __init__(self, symbol, period, overbought, oversold, mds: MarketDataService, interval="1m"):
        self.symbol = symbol
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.mds = mds
        self.interval = interval

    def generate_signal(self):
        df = self.mds.get_df(self.symbol, self.interval)
        if df.empty or len(df) < (self.period + 2):
            return "hold"
        df = df.copy()
        df['rsi'] = ta.momentum.rsi(df['close'], self.period)
        last_rsi = float(df['rsi'].iloc[-1])

        if last_rsi > self.overbought:
            return "sell"
        if last_rsi < self.oversold:
            return "buy"
        return "hold"

class StrategyManager:
    def __init__(self, config, market_data: MarketDataService):
        """
        Lê o timeframe do config e injeta o MarketDataService para todas as estratégias.
        """
        self.config = config
        self.mds = market_data
        self.interval = (config.get("timeframe") or "1m").strip()
        self._strategies = {}

    def get_strategy(self, name, symbol):
        # Singleton por (estratégia, símbolo)
        key = (name, symbol, self.interval)
        if key not in self._strategies:
            params = self.config.get(name, {})

            if name == "ema_cross":
                self._strategies[key] = EMACrossStrategy(
                    symbol,
                    params['fast_period'],
                    params['slow_period'],
                    self.mds,
                    interval=self.interval
                )

            elif name == "rsi":
                self._strategies[key] = RSIStrategy(
                    symbol,
                    params['period'],
                    params['overbought'],
                    params['oversold'],
                    self.mds,
                    interval=self.interval
                )

            # Adicione novas estratégias aqui, sempre usando self.mds e self.interval

        return self._strategies[key]
