from binance.client import Client
from binance.enums import *
from loguru import logger
from typing import Dict, Any
from bot.config import load_config
from bot.utils import round_step, round_tick, gen_client_order_id, normalize_symbol

class BinanceClient:
    """
    Camada fina sobre python-binance com:
    - cache de exchangeInfo por símbolo
    - helpers para arredondamento conforme filtros
    - helpers de ordem MARKET por qty ou quoteOrderQty
    """
    _filters_cache: Dict[str, Dict[str, float]] = {}

    def __init__(self):
        config = load_config()
        self.client = Client(config['binance_api_key'], config['binance_api_secret'])
        self.client.API_URL = config['binance_api_url']
        self.testnet = bool(config.get('testnet'))
        ambiente = 'TESTNET' if self.testnet else 'PRODUÇÃO'
        logger.info(f"🔧 Conectando na Binance SPOT {ambiente}")

    # ========= Market data =========
    def get_balance(self, asset: str) -> float:
        info = self.client.get_asset_balance(asset=asset)
        if info:
            return float(info.get('free', 0))
        return 0.0

    def get_klines(self, symbol: str, interval="1m", limit=100):
        symbol = normalize_symbol(symbol)
        return self.client.get_klines(symbol=symbol, interval=interval, limit=limit)

    # ========= Exchange filters =========
    def get_symbol_filters(self, symbol: str) -> Dict[str, float]:
        symbol = normalize_symbol(symbol)
        if symbol in self._filters_cache:
            return self._filters_cache[symbol]
        info = self.client.get_symbol_info(symbol)
        if not info:
            raise RuntimeError(f"symbol info not found: {symbol}")
        f = {i['filterType']: i for i in info['filters']}
        data = {
            'stepSize': float(f['LOT_SIZE']['stepSize']),
            'minQty': float(f['LOT_SIZE']['minQty']),
            'tickSize': float(f['PRICE_FILTER']['tickSize']),
            'minNotional': float(f.get('MIN_NOTIONAL', {}).get('minNotional', 0.0)),
        }
        self._filters_cache[symbol] = data
        return data

    def conform_qty_price(self, symbol: str, qty: float, price: float) -> (float, float):
        flt = self.get_symbol_filters(symbol)
        qty2 = max(round_step(float(qty), flt['stepSize']), flt['minQty'])
        price2 = round_tick(float(price), flt['tickSize'])
        return qty2, price2

    # ========= Orders =========
    def create_market_order_qty(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """
        Cria ordem MARKET por quantidade (base asset). Faz arredondamento + valida notional.
        """
        symbol = normalize_symbol(symbol)
        side_binance = SIDE_BUY if side == "buy" else SIDE_SELL
        # Checa notional com último preço (estimativa)
        book = self.client.get_ticker(symbol=symbol)
        last_price = float(book['lastPrice'])
        qty_adj, _ = self.conform_qty_price(symbol, quantity, last_price)
        if qty_adj * last_price < self.get_symbol_filters(symbol)['minNotional']:
            raise RuntimeError(f"MIN_NOTIONAL não atendido para {symbol}. qty={qty_adj}, price={last_price}")
        order = self.client.create_order(
            symbol=symbol,
            side=side_binance,
            type=ORDER_TYPE_MARKET,
            quantity=qty_adj,
            newClientOrderId=gen_client_order_id("mkt_qty")
        )
        return order

    def create_market_order_quote(self, symbol: str, side: str, quote_qty_usdt: float) -> Dict[str, Any]:
        """
        Usa quoteOrderQty (comprar/vender ~USDT). Nem todos pares permitem no SELL.
        Funciona muito bem para BUY (ex.: gastar 50 USDT).
        """
        symbol = normalize_symbol(symbol)
        side_binance = SIDE_BUY if side == "buy" else SIDE_SELL
        # quoteOrderQty precisa respeitar MIN_NOTIONAL
        flt = self.get_symbol_filters(symbol)
        if quote_qty_usdt < flt['minNotional']:
            raise RuntimeError(f"quoteOrderQty < MIN_NOTIONAL para {symbol}: {quote_qty_usdt} < {flt['minNotional']}")
        order = self.client.create_order(
            symbol=symbol,
            side=side_binance,
            type=ORDER_TYPE_MARKET,
            quoteOrderQty=str(quote_qty_usdt),
            newClientOrderId=gen_client_order_id("mkt_quote")
        )
        return order
