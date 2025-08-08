from typing import Optional, Dict, Any
from loguru import logger
from binance.client import Client
from bot.binance_client import BinanceClient
from bot.risk import RiskManager
from bot.utils import normalize_symbol
from bot.state import append_trade

class ExecutionService:
    """
    Execução com validação de filtros Binance:
      - MIN_NOTIONAL (valor mínimo da ordem)
      - LOT_SIZE (stepSize/minQty)
      - PRICE_FILTER (tickSize)
    BUY usa quoteOrderQty quando possível (mais estável).
    SELL usa qty (base), respeitando stepSize e minNotional.
    """
    def __init__(self, risk: RiskManager, notifier=None):
        self.client = BinanceClient()
        self.risk = risk
        self.notifier = notifier
        self.last_buy_price: Dict[str, float] = {}
        self.last_buy_qty: Dict[str, float] = {}

    # ---------- helpers ----------
    def _notify(self, text: str):
        if self.notifier:
            try: self.notifier.send(text)
            except Exception: logger.warning("Falha ao notificar Telegram")

    def _base_asset(self, symbol: str) -> str:
        info = self.client.client.get_symbol_info(symbol)
        return info["baseAsset"]

    def _last_price(self, symbol: str) -> float:
        return float(self.client.client.get_ticker(symbol=symbol)["lastPrice"])

    # ---------- entrada ----------
    def _execute_buy(self, symbol: str, desired_quote_usdt: float) -> Optional[Dict[str, Any]]:
        flt = self.client.get_symbol_filters(symbol)
        last_price = self._last_price(symbol)

        # precisa atender notional mínimo
        if desired_quote_usdt < flt["minNotional"]:
            msg = (f"[{symbol}] Tamanho pedido ({desired_quote_usdt:.2f} USDT) "
                   f"< MIN_NOTIONAL ({flt['minNotional']:.2f}).")
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        # tenta BUY por quoteOrderQty (melhor caminho)
        try:
            order = self.client.create_market_order_quote(symbol, "buy", desired_quote_usdt)
        except Exception as e:
            logger.warning(f"[{symbol}] quoteOrderQty falhou ({e}). Fazendo fallback por qty...")
            # fallback: converte quote->qty respeitando stepSize
            qty_raw = desired_quote_usdt / last_price
            qty_adj, _ = self.client.conform_qty_price(symbol, qty_raw, last_price)
            if qty_adj * last_price < flt["minNotional"]:
                msg = (f"[{symbol}] Mesmo no fallback, qty*preço ({qty_adj*last_price:.4f}) "
                       f"< MIN_NOTIONAL ({flt['minNotional']:.4f}). Abortando BUY.")
                logger.warning(msg); self._notify("⚠️ " + msg)
                return None
            order = self.client.create_market_order_qty(symbol, "buy", qty_adj)

        # calcular média de execução
        fills = order.get("fills", [])
        total_qty = sum(float(f["qty"]) for f in fills) if fills else 0.0
        avg_price = (sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty) if fills and total_qty>0 else last_price

        self.last_buy_price[symbol] = avg_price
        self.last_buy_qty[symbol] = total_qty
        self.risk.register_trade(0.0)
        append_trade(symbol, "buy", total_qty, avg_price, pnl=0.0)
        self._notify(f"🟢 [{symbol}] BUY ~{desired_quote_usdt:.2f} USDT @ ~{avg_price:.6f} (qty ~ {total_qty:.6f})")

        return {"symbol": symbol, "side": "buy", "executed": True, "qty": total_qty, "avg_price": avg_price, "pnl": 0.0}

    # ---------- saída ----------
    def _execute_sell(self, symbol: str) -> Optional[Dict[str, Any]]:
        flt = self.client.get_symbol_filters(symbol)
        last_price = self._last_price(symbol)

        # quantidade alvo: a comprada anteriormente (flat/swing simples)
        target_qty = float(self.last_buy_qty.get(symbol, 0.0))
        # por segurança, não venda mais do que tem na conta
        base = self._base_asset(symbol)
        wallet_qty = float(self.client.get_balance(base))
        qty_raw = min(target_qty, wallet_qty)

        if qty_raw <= 0:
            msg = f"[{symbol}] Sem posição para vender (qty=0)."
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        # conforma stepSize/minQty
        qty_adj, _ = self.client.conform_qty_price(symbol, qty_raw, last_price)
        if qty_adj < flt["minQty"]:
            msg = f"[{symbol}] Qty ajustada {qty_adj} < minQty {flt['minQty']}. Abortando SELL."
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        # precisa atender notional mínimo
        notional = qty_adj * last_price
        if notional < flt["minNotional"]:
            # tenta aumentar levemente dentro do que temos em carteira
            needed_qty = flt["minNotional"] / last_price
            qty_try, _ = self.client.conform_qty_price(symbol, min(wallet_qty, needed_qty), last_price)
            if qty_try * last_price < flt["minNotional"]:
                msg = (f"[{symbol}] SELL abaixo do MIN_NOTIONAL "
                       f"({qty_adj*last_price:.4f} < {flt['minNotional']:.4f}). Abortando.")
                logger.warning(msg); self._notify("⚠️ " + msg)
                return None
            qty_adj = qty_try

        # envia SELL
        order = self.client.create_market_order_qty(symbol, "sell", qty_adj)

        avg_buy = float(self.last_buy_price.get(symbol, last_price))
        pnl = (last_price - avg_buy) * qty_adj
        self.risk.register_trade(pnl)
        self.last_buy_qty[symbol] = max(0.0, target_qty - qty_adj)  # zera ou reduz posição

        append_trade(symbol, "sell", qty_adj, last_price, pnl=pnl)
        self._notify(f"🔴 [{symbol}] SELL {qty_adj:.6f} @ ~{last_price:.6f} | PnL ~ {pnl:.4f} USDT")

        return {"symbol": symbol, "side": "sell", "executed": True, "qty": qty_adj, "avg_price": last_price, "pnl": pnl}

    # ---------- API externa ----------
    def place_signal(self, symbol: str, signal: str, usdt_balance: float) -> Optional[Dict[str, Any]]:
        symbol = normalize_symbol(symbol)
        if signal not in ("buy", "sell"):
            return None

        if not self.risk.can_trade():
            self._notify(f"⛔ Limites de risco atingidos. Ignorando {signal} em {symbol}.")
            return None

        try:
            if signal == "buy":
                quote_size = self.risk.position_size_from_balance(usdt_balance)
                if quote_size <= 0:
                    logger.warning(f"[{symbol}] Sem USDT livre para BUY."); return None
                return self._execute_buy(symbol, quote_size)
            else:
                return self._execute_sell(symbol)
        except Exception as e:
            logger.exception(f"[{symbol}] Falha ao executar {signal}: {e}")
            self._notify(f"❌ [{symbol}] Falha ao executar {signal}: {e}")
            return None
