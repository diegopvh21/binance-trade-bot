from collections import deque
from typing import Dict, Deque
import pandas as pd
from bot.utils import normalize_symbol

class MarketDataService:
    """
    Mantém buffers de candles por símbolo/timeframe alimentados pelo WS.
    Evita chamadas REST repetidas para estratégias.
    """
    def __init__(self, maxlen: int = 1000):
        self.maxlen = maxlen
        self.buffers: Dict[str, Deque[dict]] = {}  # chave: "SYMBOL|1m"

    def _key(self, symbol: str, interval: str) -> str:
        return f"{normalize_symbol(symbol)}|{interval}"

    def bootstrap_from_rest(self, client, symbol: str, interval: str = "1m", limit: int = 200):
        k = self._key(symbol, interval)
        if k in self.buffers:
            return
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        buf: Deque[dict] = deque(maxlen=self.maxlen)
        for row in klines:
            buf.append({
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]),
                "closed": True,
            })
        self.buffers[k] = buf

    def on_kline_closed(self, symbol: str, interval: str, k: dict):
        kname = self._key(symbol, interval)
        if kname not in self.buffers:
            self.buffers[kname] = deque(maxlen=self.maxlen)
        self.buffers[kname].append({
            "open_time": int(k["t"]),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "close_time": int(k["T"]),
            "closed": True,
        })

    def get_df(self, symbol: str, interval: str = "1m") -> pd.DataFrame:
        k = self._key(symbol, interval)
        buf = self.buffers.get(k, None)
        if not buf or len(buf) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(list(buf))
        return df
