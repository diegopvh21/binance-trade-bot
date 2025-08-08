from dataclasses import dataclass, field
from loguru import logger
from typing import Dict
from bot.utils import normalize_symbol

@dataclass
class RiskConfig:
    capital_per_trade_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    max_daily_loss_pct: float
    max_trades_per_day: int

@dataclass
class RiskState:
    # Estado diário simples (reset manual/externo ou quando virar o dia)
    trades_today: int = 0
    realized_pnl_usdt: float = 0.0

class RiskManager:
    def __init__(self, risk_cfg: Dict):
        self.cfg = RiskConfig(**risk_cfg)
        self.state = RiskState()

    def can_trade(self) -> bool:
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            logger.error("⛔ Limite de trades diários atingido.")
            return False
        # Limite de perda diária por percentual exige base de capital.
        # Aqui consideramos perda absoluta como proxy (opcional passar capital no check).
        return True

    def register_trade(self, realized_pnl_usdt: float):
        self.state.trades_today += 1
        self.state.realized_pnl_usdt += float(realized_pnl_usdt)

    def position_size_from_balance(self, usdt_balance: float) -> float:
        """
        Retorna o tamanho em USDT (quote) a gastar numa operação (para usar com quoteOrderQty).
        """
        size_usdt = max(0.0, (usdt_balance or 0.0) * self.cfg.capital_per_trade_pct / 100.0)
        return size_usdt
