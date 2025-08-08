import datetime
import math
import random
import string

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
