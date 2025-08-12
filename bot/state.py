import json
import os
import threading
import time
from typing import Any, Dict, List

STATE_PATH = os.getenv("BOT_STATE_PATH", "state.json")
_lock = threading.Lock()

_DEFAULT: Dict[str, Any] = {
    "ws_uptime_start": 0,     # epoch
    "last_tick_ts": 0,        # epoch
    "pnl_daily": 0.0,         # USDT (estimado)
    "trades": [],             # [{ts, symbol, side, qty, price, pnl}]
    "mode": "trade",          # "trade" | "backtest" (informativo)
    "symbols": [],            # lista de pares
    "recent_signals": [],     # antirrepique: [{ts, symbol, side, candle_close}]
}

def _read() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return dict(_DEFAULT)
    try:
        with open(STATE_PATH, "r") as f:
            data = json.load(f)
        # sanity defaults
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return dict(_DEFAULT)

def _write(data: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)

def get() -> Dict[str, Any]:
    with _lock:
        return _read()

def set_initial(mode: str, symbols: List[str]) -> None:
    with _lock:
        st = _read()
        st["ws_uptime_start"] = int(time.time())
        st["last_tick_ts"] = 0
        st["pnl_daily"] = 0.0
        st["trades"] = []
        st["mode"] = mode
        st["symbols"] = symbols
        st["recent_signals"] = []
        _write(st)

def set_last_tick_now() -> None:
    with _lock:
        st = _read()
        st["last_tick_ts"] = int(time.time())
        _write(st)

def append_trade(symbol: str, side: str, qty: float, price: float, pnl: float = 0.0) -> None:
    with _lock:
        st = _read()
        st["trades"].append({
            "ts": int(time.time()),
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "price": float(price),
            "pnl": float(pnl),
        })
        st["trades"] = st["trades"][-200:]  # mantém só os últimos 200
        st["pnl_daily"] = float(st.get("pnl_daily", 0.0)) + float(pnl or 0.0)
        _write(st)

# --------- antirrepique / idempotência de sinais ---------
def add_recent_signal(symbol: str, side: str, candle_close: int, ttl_sec: int = 30) -> None:
    now = int(time.time())
    with _lock:
        st = _read()
        arr = st.get("recent_signals", [])
        arr.append({"ts": now, "symbol": symbol, "side": side, "candle_close": int(candle_close)})
        # prune
        arr = [x for x in arr if (now - int(x.get("ts", now))) <= ttl_sec]
        st["recent_signals"] = arr[-500:]
        _write(st)

def is_recent_signal(symbol: str, side: str, candle_close: int, ttl_sec: int = 30) -> bool:
    now = int(time.time())
    with _lock:
        st = _read()
        arr = st.get("recent_signals", [])
        # prune enquanto verifica
        kept = []
        hit = False
        for x in arr:
            if (now - int(x.get("ts", now))) <= ttl_sec:
                kept.append(x)
                if (x.get("symbol") == symbol and
                    x.get("side") == side and
                    int(x.get("candle_close", 0)) == int(candle_close)):
                    hit = True
        st["recent_signals"] = kept[-500:]
        _write(st)
        return hit
