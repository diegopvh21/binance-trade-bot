from typing import Optional, Dict, Any, Tuple
from loguru import logger
from bot.binance_client import BinanceClient
from bot.risk import RiskManager
from bot.utils import normalize_symbol
from bot.state import append_trade

class ExecutionService:
    """
    Execução com validação de filtros Binance e proteções:
      - MIN_NOTIONAL / LOT_SIZE / PRICE_FILTER
      - BUY usa quoteOrderQty quando possível
      - SELL por qty (base), respeitando stepSize e minNotional
      - Proteções P0: SL/TP via OCO quando possível; fallback watchdog por preço
      - Reconciliação inicial de posição/PM
    """
    def __init__(self, risk: RiskManager, notifier=None):
        self.client = BinanceClient()
        self.risk = risk
        self.notifier = notifier

        # posição/PM + proteções atuais por símbolo
        self.last_buy_price: Dict[str, float] = {}
        self.last_buy_qty: Dict[str, float] = {}
        self.stop_price: Dict[str, float] = {}
        self.take_price: Dict[str, float] = {}

    # ---------- helpers ----------
    def _notify(self, text: str):
        if self.notifier:
            try:
                self.notifier.send(text)
            except Exception:
                logger.warning("Falha ao notificar Telegram")

    def _base_asset(self, symbol: str) -> str:
        info = self.client.get_symbol_info(symbol)
        return info["baseAsset"]

    def _last_price(self, symbol: str) -> float:
        return float(self.client.get_ticker(symbol)["lastPrice"])

    # ---------- reconciliação ----------
    def reconcile_for_symbol(self, symbol: str) -> None:
        """
        Recupera posição (qty) e PM aproximado a partir do saldo e trades recentes.
        """
        base = self._base_asset(symbol)
        wallet_qty = float(self.client.get_balance(base))
        if wallet_qty <= 0:
            self.last_buy_qty[symbol] = 0.0
            self.last_buy_price[symbol] = 0.0
            return

        # heurística: média ponderada de BUYS recentes limitada ao saldo atual
        trades = self.client.get_my_trades(symbol, limit=200)
        rem = wallet_qty
        cost = 0.0
        for tr in reversed(trades):
            qty = float(tr["qty"])
            price = float(tr["price"])
            is_buy = bool(tr.get("isBuyer", False))
            if not is_buy:
                # vendeu; ignora na reconstrução de PM de carteira atual
                continue
            take = min(rem, qty)
            cost += take * price
            rem -= take
            if rem <= 0:
                break

        pm = (cost / wallet_qty) if wallet_qty > 0 else 0.0
        self.last_buy_qty[symbol] = wallet_qty
        self.last_buy_price[symbol] = pm
        logger.info(f"[{symbol}] Reconciliação: qty={wallet_qty:.6f}, pm={pm:.6f}")

    def reconcile_all(self, symbols):
        for s in symbols:
            try:
                self.reconcile_for_symbol(s)
            except Exception as e:
                logger.warning(f"[{s}] Falha ao reconciliar posição: {e}")

    # ---------- proteções ----------
    def _apply_protective_levels(self, symbol: str, avg_price: float):
        if not self.risk.is_protective_enabled():
            self.stop_price.pop(symbol, None)
            self.take_price.pop(symbol, None)
            return
        sl_pct = self.risk.sl_pct()
        tp_pct = self.risk.tp_pct()
        stop = avg_price * (1.0 - sl_pct / 100.0) if sl_pct > 0 else None
        take = avg_price * (1.0 + tp_pct / 100.0) if tp_pct > 0 else None
        self.stop_price[symbol] = float(stop) if stop else None
        self.take_price[symbol] = float(take) if take else None
        logger.info(f"[{symbol}] Proteções definidas: SL={self.stop_price[symbol]} | TP={self.take_price[symbol]}")

        # tentativa de OCO (se ambos definidos)
        try:
            if stop and take and self.last_buy_qty.get(symbol, 0.0) > 0:
                qty = float(self.last_buy_qty[symbol])
                self.client.create_oco_sell(symbol, qty, stop_price=stop, limit_price=take)
                self._notify(f"🛡️ [{symbol}] OCO enviado (SL {stop:.6f} / TP {take:.6f})")
        except Exception as e:
            logger.warning(f"[{symbol}] OCO indisponível/falhou ({e}). Usará watchdog de preço.")

    def check_protective_exit(self, symbol: str, last_price: float) -> Optional[Dict[str, Any]]:
        """
        Fallback de proteção por software: se preço cruzar SL/TP e não houver OCO, envia SELL market.
        Chamado a cada candle fechado.
        """
        qty = float(self.last_buy_qty.get(symbol, 0.0))
        if qty <= 0:
            return None
        sl = self.stop_price.get(symbol)
        tp = self.take_price.get(symbol)
        try_sell = False
        reason = ""
        if sl and last_price <= float(sl):
            try_sell = True
            reason = f"STOP {sl:.6f}"
        elif tp and last_price >= float(tp):
            try_sell = True
            reason = f"TAKE {tp:.6f}"

        if try_sell:
            self._notify(f"🛑 [{symbol}] Proteção acionada ({reason}). Enviando SELL market…")
            return self._execute_sell(symbol)
        return None

    # ---------- entrada ----------
    def _execute_buy(self, symbol: str, desired_quote_usdt: float) -> Optional[Dict[str, Any]]:
        flt = self.client.get_symbol_filters(symbol)
        last_price = self._last_price(symbol)

        if desired_quote_usdt < flt["minNotional"]:
            msg = (f"[{symbol}] Tamanho pedido ({desired_quote_usdt:.2f} USDT) "
                   f"< MIN_NOTIONAL ({flt['minNotional']:.2f}).")
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        # tenta BUY por quoteOrderQty (melhor caminho)
        try:
            order = self.client.create_market_order_quote(symbol, "buy", desired_quote_usdt)
        except Exception as e:
            logger.warning(f"[{symbol}] quoteOrderQty falhou ({e}). Fallback por qty…")
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

        # define proteções
        self._apply_protective_levels(symbol, avg_price)

        return {"symbol": symbol, "side": "buy", "executed": True, "qty": total_qty, "avg_price": avg_price, "pnl": 0.0}

    # ---------- saída ----------
    def _execute_sell(self, symbol: str) -> Optional[Dict[str, Any]]:
        flt = self.client.get_symbol_filters(symbol)
        last_price = self._last_price(symbol)

        target_qty = float(self.last_buy_qty.get(symbol, 0.0))
        base = self._base_asset(symbol)
        wallet_qty = float(self.client.get_balance(base))
        qty_raw = min(target_qty, wallet_qty)

        if qty_raw <= 0:
            msg = f"[{symbol}] Sem posição para vender (qty=0)."
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        qty_adj, _ = self.client.conform_qty_price(symbol, qty_raw, last_price)
        if qty_adj < flt["minQty"]:
            msg = f"[{symbol}] Qty ajustada {qty_adj} < minQty {flt['minQty']}. Abortando SELL."
            logger.warning(msg); self._notify("⚠️ " + msg)
            return None

        notional = qty_adj * last_price
        if notional < flt["minNotional"]:
            needed_qty = flt["minNotional"] / last_price
            qty_try, _ = self.client.conform_qty_price(symbol, min(wallet_qty, needed_qty), last_price)
            if qty_try * last_price < flt["minNotional"]:
                msg = (f"[{symbol}] SELL abaixo do MIN_NOTIONAL "
                       f"({qty_adj*last_price:.4f} < {flt['minNotional']:.4f}). Abortando.")
                logger.warning(msg); self._notify("⚠️ " + msg)
                return None
            qty_adj = qty_try

        order = self.client.create_market_order_qty(symbol, "sell", qty_adj)

        avg_buy = float(self.last_buy_price.get(symbol, last_price))
        pnl = (last_price - avg_buy) * qty_adj
        self.risk.register_trade(pnl)
        # zera proteção e posição
        self.last_buy_qty[symbol] = max(0.0, float(self.last_buy_qty.get(symbol, 0.0)) - qty_adj)
        if self.last_buy_qty[symbol] <= 0:
            self.stop_price.pop(symbol, None)
            self.take_price.pop(symbol, None)

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
