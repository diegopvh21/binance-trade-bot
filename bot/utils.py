import datetime
import math
import string
import time
import random
from loguru import logger

def timestamp():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def normalize_symbol(sym: str) -> str:
    # Binance symbols são MAIÚSCULOS (ex.: BTCUSDT)
    return (sym or "").upper()

def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step

def round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return math.floor(price / tick) * tick

def gen_client_order_id(prefix="bot"):
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}_{int(datetime.datetime.now().timestamp())}_{rand}"

def normalize_symbol(symbol: str) -> str:
    """Normaliza símbolo para padrão Binance (sem separadores)."""
    return symbol.replace("/", "").upper()

def binance_request_with_retry(func, *args, max_retries=5, **kwargs):
    """
    Executa uma função de requisição à Binance com retry e backoff exponencial.
    Trata erros de rate limit (HTTP 429) e -1003 (binance limit).
    """
    delay = 1
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "-1003" in msg:
                logger.warning(f"🚦 Rate limit atingido ({msg}). Tentando novamente em {delay}s...")
                time.sleep(delay + random.random())
                delay = min(delay * 2, 30)
            else:
                logger.error(f"Erro na requisição Binance: {e}")
                raise
    raise RuntimeError("Limite de tentativas atingido.")
