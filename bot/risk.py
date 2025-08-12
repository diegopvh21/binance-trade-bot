from dataclasses import dataclass
from loguru import logger
from typing import Dict, Optional
from bot.state import get as get_state

@dataclass
class RiskConfig:
    capital_per_trade_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    max_daily_loss_pct: float
    max_trades_per_day: int
    capital_base_usdt: float = 0.0          # NOVO: base para corte diário
    protective_orders_enabled: bool = True  # NOVO: liga/desliga SL/TP

class RiskState:
    # Estado diário simples (reset manual/externo ou quando virar o dia)
    trades_today: int = 0
    realized_pnl_usdt: float = 0.0

    def __init__(self):
        self.trades_today = 0
        self.realized_pnl_usdt = 0.0

class RiskManager:
    def __init__(self, risk_cfg: Dict):
        # compat: preencher defaults se não existirem
        risk_cfg = dict(risk_cfg or {})
        risk_cfg.setdefault("capital_base_usdt", 0.0)
        risk_cfg.setdefault("protective_orders_enabled", True)
        self.cfg = RiskConfig(**risk_cfg)
        self.state = RiskState()

    def can_trade(self) -> bool:
        # 1) limite por nº de trades
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            logger.error("⛔ Limite de trades diários atingido.")
            return False

        # 2) corte por perda diária (usa pnl_daily do state.json)
        st = get_state()
        pnl_daily = float(st.get("pnl_daily", 0.0))
        cap = float(self.cfg.capital_base_usdt or 0.0)
        if cap > 0 and self.cfg.max_daily_loss_pct > 0:
            loss_limit = -abs(cap) * (abs(self.cfg.max_daily_loss_pct) / 100.0)
            if pnl_daily <= loss_limit:
                logger.error(f"⛔ Corte diário atingido: pnl={pnl_daily:.2f} USDT ≤ limite {loss_limit:.2f} USDT.")
                return False

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

    # helpers p/ SL/TP usados pela execução
    def is_protective_enabled(self) -> bool:
        return bool(self.cfg.protective_orders_enabled)

    def sl_pct(self) -> float:
        return max(0.0, float(self.cfg.stop_loss_pct or 0.0))

    def tp_pct(self) -> float:
        return max(0.0, float(self.cfg.take_profit_pct or 0.0))
