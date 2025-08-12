# bot/backtest.py
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional

import requests
import pandas as pd
from loguru import logger

from bot.config import load_config
from bot.market_data import MarketDataService
from bot.strategies import StrategyManager
from bot.state import set_initial, set_last_tick_now, append_trade
from bot.utils import normalize_symbol

BINANCE_REST = "https://api.binance.com/api/v3/klines"

# ----- Suporte a CSV Binance -----
BINANCE_CSV_HEADERS = {
    "open_time": ["open_time", "Open time", "t"],
    "open":      ["open", "Open", "o"],
    "high":      ["high", "High", "h"],
    "low":       ["low", "Low", "l"],
    "close":     ["close", "Close", "c"],
    "volume":    ["volume", "Volume", "v"],
    "close_time":["close_time", "Close time", "T"],
}

def _col(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    for n in names:
        if n in df.columns:
            return n
    return None

def _df_to_kline_dicts(df: pd.DataFrame) -> List[dict]:
    c_open_time  = _col(df, BINANCE_CSV_HEADERS["open_time"])
    c_open       = _col(df, BINANCE_CSV_HEADERS["open"])
    c_high       = _col(df, BINANCE_CSV_HEADERS["high"])
    c_low        = _col(df, BINANCE_CSV_HEADERS["low"])
    c_close      = _col(df, BINANCE_CSV_HEADERS["close"])
    c_volume     = _col(df, BINANCE_CSV_HEADERS["volume"])
    c_close_time = _col(df, BINANCE_CSV_HEADERS["close_time"])

    if not all([c_open_time, c_open, c_high, c_low, c_close, c_volume]):
        raise RuntimeError("CSV não possui colunas obrigatórias (open_time/open/high/low/close/volume).")

    has_close_time = c_close_time is not None
    out: List[dict] = []
    for _, r in df.iterrows():
        t_open = int(r[c_open_time])
        o = float(r[c_open]); h = float(r[c_high]); l = float(r[c_low]); c = float(r[c_close]); v = float(r[c_volume])
        T = int(r[c_close_time]) if has_close_time else (t_open + 60_000 - 1)
        out.append({"t": t_open, "T": T, "o": f"{o:.8f}", "h": f"{h:.8f}", "l": f"{l:.8f}", "c": f"{c:.8f}", "v": f"{v:.8f}", "x": True})
    return out

def load_csv_klines(path: str) -> List[dict]:
    df = pd.read_csv(path)
    return _df_to_kline_dicts(df)

# ----- Downloader público (produção) -----
def fetch_public_klines(symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> List[List]:
    """
    Baixa klines da API pública de PRODUÇÃO (sem API key) paginando por startTime.
    Retorna lista crua (o mesmo array da API).
    """
    out: List[List] = []
    cur = int(start_ms)
    sym = normalize_symbol(symbol)

    while True:
        params = {"symbol": sym, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": limit}
        r = requests.get(BINANCE_REST, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break

        out.extend(batch)
        last_close = int(batch[-1][6])
        cur = last_close + 1
        if cur >= end_ms:
            break
        time.sleep(0.15)  # gentileza com a API
    return out

def klines_raw_to_closed_dicts(rows: List[List]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        out.append({
            "t": int(row[0]), "T": int(row[6]),
            "o": f"{float(row[1]):.8f}",
            "h": f"{float(row[2]):.8f}",
            "l": f"{float(row[3]):.8f}",
            "c": f"{float(row[4]):.8f}",
            "v": f"{float(row[5]):.8f}",
            "x": True
        })
    return out

def save_rows_as_csv(rows: List[List], csv_path: str):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    cols = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])])
    df.to_csv(csv_path, index=False)

# ----- Engine -----
class BacktestEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.symbols = [normalize_symbol(s) for s in cfg["symbols"]]
        self.interval = (cfg.get("timeframe") or "1m").strip()
        self.bt: Dict = cfg.get("backtest", {})
        self.cps: float = float(self.bt.get("candles_per_second", 20.0))
        self.warmup: int = int(self.bt.get("warmup", 200))
        self.quote_per_trade: float = float(self.bt.get("quote_per_trade_usdt", 50.0))
        self.auto_save_csv: bool = bool(self.bt.get("auto_save_csv", True))

        self.mds = MarketDataService(maxlen=10_000)
        self.strat_mgr = StrategyManager(cfg, self.mds)
        self.positions: Dict[str, Dict[str, float]] = defaultdict(lambda: {"qty": 0.0, "price": 0.0})
        self.hist: Dict[str, List[dict]] = {}

    def _csv_path_for(self, symbol: str) -> str:
        csv_cfg = self.bt.get("csv", {})
        folder = csv_cfg.get("folder", "data/backtest")
        pattern = csv_cfg.get("pattern", "{symbol}_{interval}.csv")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, pattern.format(symbol=symbol, interval=self.interval))

    def _api_window_ms(self) -> (int, int):
        api = self.bt.get("api", {})
        if "start_ms" in api and "end_ms" in api:
            return int(api["start_ms"]), int(api["end_ms"])
        # fallback: últimos N dias
        days = int(api.get("last_days", 5))
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 24 * 60 * 60 * 1000
        return start_ms, end_ms

    def _ensure_csv_or_download(self, symbol: str, path: str):
        if os.path.exists(path):
            return
        logger.info(f"[{symbol}] CSV não encontrado. Baixando da API pública e salvando em {path}...")
        start_ms, end_ms = self._api_window_ms()
        rows = fetch_public_klines(symbol, self.interval, start_ms, end_ms)
        if not rows:
            raise RuntimeError(f"[{symbol}] API pública não retornou candles no período.")
        save_rows_as_csv(rows, path)
        logger.info(f"[{symbol}] CSV salvo: {path} ({len(rows)} candles).")

    def load_data(self):
        source = (self.bt.get("source") or "csv").lower()
        logger.info(f"📦 Carregando dados p/ backtest (source={source}) | tf={self.interval} | symbols={self.symbols}")

        if source == "csv":
            for s in self.symbols:
                p = self._csv_path_for(s)
                if self.bt.get("auto_download_if_missing", True):
                    self._ensure_csv_or_download(s, p)
                self.hist[s] = load_csv_klines(p)
                logger.info(f"[{s}] CSV carregado: {p} ({len(self.hist[s])} candles)")
        elif source == "api":
            start_ms, end_ms = self._api_window_ms()
            for s in self.symbols:
                rows = fetch_public_klines(s, self.interval, start_ms, end_ms)
                self.hist[s] = klines_raw_to_closed_dicts(rows)
                logger.info(f"[{s}] API pública carregada: {len(self.hist[s])} candles")
                if self.auto_save_csv:
                    p = self._csv_path_for(s)
                    save_rows_as_csv(rows, p)
        else:
            raise RuntimeError("backtest.source deve ser 'csv' ou 'api'.")

        for s in self.symbols:
            if len(self.hist.get(s, [])) <= self.warmup + 2:
                raise RuntimeError(f"[{s}] Histórico insuficiente para warmup={self.warmup}.")

    def _bootstrap_warmup(self):
        for s in self.symbols:
            for k in self.hist[s][:self.warmup]:
                self.mds.on_kline_closed(s, self.interval, k)

    def _step_symbol(self, symbol: str, idx: int):
        k = self.hist[symbol][idx]
        self.mds.on_kline_closed(symbol, self.interval, k)
        price = float(k["c"])
        set_last_tick_now()

        for strat_name in self.cfg["strategies"].get(symbol, []):
            signal = self.strat_mgr.get_strategy(strat_name, symbol).generate_signal()
            if signal == "buy" and self.positions[symbol]["qty"] <= 0:
                qty = self.quote_per_trade / price
                self.positions[symbol] = {"qty": qty, "price": price}
                append_trade(symbol, "buy", qty, price, pnl=0.0)
            elif signal == "sell" and self.positions[symbol]["qty"] > 0:
                qty = self.positions[symbol]["qty"]
                entry = self.positions[symbol]["price"]
                pnl = (price - entry) * qty
                self.positions[symbol] = {"qty": 0.0, "price": 0.0}
                append_trade(symbol, "sell", qty, price, pnl=pnl)

    def run(self):
        set_initial("backtest", self.symbols)
        self.load_data()
        self._bootstrap_warmup()

        ptr = {s: self.warmup for s in self.symbols}
        n_total = max(len(self.hist[s]) for s in self.symbols)
        cps_sleep = (1.0 / self.cps) if self.cps > 0 else 0.0
        logger.info("🚀 Iniciando backtest acelerado.")
        steps = 0

        while True:
            active = False
            for s in self.symbols:
                i = ptr[s]
                if i < len(self.hist[s]):
                    self._step_symbol(s, i)
                    ptr[s] = i + 1
                    active = True
            if not active:
                break
            steps += 1
            if cps_sleep > 0:
                time.sleep(cps_sleep)
            elif steps % 5000 == 0:
                time.sleep(0.001)

        logger.info("✅ Backtest finalizado.")

def main():
    cfg = load_config()
    BacktestEngine(cfg).run()

if __name__ == "__main__":
    main()
