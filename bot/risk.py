class RiskManager:
    def __init__(self, risk_cfg):
        self.capital_per_trade_pct = risk_cfg['capital_per_trade_pct']
        self.stop_loss_pct = risk_cfg['stop_loss_pct']
        self.take_profit_pct = risk_cfg['take_profit_pct']
        self.max_daily_loss_pct = risk_cfg['max_daily_loss_pct']
        self.max_trades_per_day = risk_cfg['max_trades_per_day']

    def calculate_position_size(self, symbol, usdt_balance):
        size = usdt_balance * self.capital_per_trade_pct / 100
        # Binance pode exigir min/max de qty. Aqui você pode melhorar usando exchange info.
        # Exemplo simples: converte valor em USDT para quantidade em token.
        # Assumindo preço do último close:
        from bot.binance_client import BinanceClient
        client = BinanceClient()
        df = client.get_ohlcv(symbol, interval="1m", limit=2)
        last_close = df['close'].iloc[-1]
        qty = round(size / last_close, 6)
        return qty if qty > 0 else 0
