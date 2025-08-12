import os
import time
from collections import defaultdict
from typing import Dict, List, Optional

import requests
import pandas as pd
import matplotlib.pyplot as plt
from loguru import logger

from bot.config import load_config
from bot.market_data import MarketDataService
from bot.strategies import StrategyManager
from bot.state import set_initial, set_last_tick_now, append_trade, is_recent_signal, add_recent_signal
from bot.utils import normalize_symbol
from bot.risk import RiskManager

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
        raise RuntimeError("CSV não possui colunas obrigatórias.")

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

# ----- Downloader público -----
def fetch_public_klines(symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> List[List]:
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
        time.sleep(0.15)
    return out

def klines_raw_to_closed_dicts(rows: List[List]) -> List[dict]:
    return [
        {"t": int(r[0]), "T": int(r[6]), "o": f"{float(r[1]):.8f}", "h": f"{float(r[2]):.8f}",
         "l": f"{float(r[3]):.8f}", "c": f"{float(r[4]):.8f}", "v": f"{float(r[5]):.8f}", "x": True}
        for r in rows
    ]

def save_rows_as_csv(rows: List[List], csv_path: str):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    cols = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    pd.DataFrame(rows, columns=cols[:len(rows[0])]).to_csv(csv_path, index=False)

# ----- Engine -----
class BacktestEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.symbols = [normalize_symbol(s) for s in cfg["symbols"]]
        self.interval = cfg.get("timeframe", "1m").strip()
        self.bt = cfg.get("backtest", {})
        self.cps = float(self.bt.get("candles_per_second", 20.0))
        self.warmup = int(self.bt.get("warmup", 200))
        self.auto_save_csv = bool(self.bt.get("auto_save_csv", True))

        self.mds = MarketDataService(maxlen=10_000)
        self.strat_mgr = StrategyManager(cfg, self.mds)
        self.risk = RiskManager(cfg["risk"])

        self.positions = defaultdict(lambda: {"qty": 0.0, "price": 0.0})
        self.hist = {}
        self.pnl_daily = 0.0
        self.capital_inicial = float(self.bt.get("capital_inicial", 1000.0))
        self.trades = []

    def _csv_path_for(self, symbol):
        folder = self.bt.get("csv", {}).get("folder", "data/backtest")
        pattern = self.bt.get("csv", {}).get("pattern", "{symbol}_{interval}.csv")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, pattern.format(symbol=symbol, interval=self.interval))

    def _api_window_ms(self):
        api = self.bt.get("api", {})
        if "start_ms" in api and "end_ms" in api:
            return int(api["start_ms"]), int(api["end_ms"])
        days = int(api.get("last_days", 5))
        end_ms = int(time.time() * 1000)
        return end_ms - days * 86400000, end_ms

    def _ensure_csv_or_download(self, symbol, path):
        if os.path.exists(path):
            return
        logger.info(f"[{symbol}] CSV não encontrado, baixando...")
        start_ms, end_ms = self._api_window_ms()
        rows = fetch_public_klines(symbol, self.interval, start_ms, end_ms)
        if not rows:
            raise RuntimeError(f"[{symbol}] Sem dados na API pública.")
        save_rows_as_csv(rows, path)
        logger.info(f"[{symbol}] CSV salvo: {path} ({len(rows)} candles).")

    def load_data(self):
        source = (self.bt.get("source") or "csv").lower()
        logger.info(f"📦 Carregando dados p/ backtest (source={source}) | tf={self.interval} | symbols={self.symbols}")
        for s in self.symbols:
            if source == "csv":
                p = self._csv_path_for(s)
                if self.bt.get("auto_download_if_missing", True):
                    self._ensure_csv_or_download(s, p)
                self.hist[s] = load_csv_klines(p)
            elif source == "api":
                rows = fetch_public_klines(s, self.interval, *self._api_window_ms())
                self.hist[s] = klines_raw_to_closed_dicts(rows)
                if self.auto_save_csv:
                    save_rows_as_csv(rows, self._csv_path_for(s))
            else:
                raise RuntimeError("backtest.source deve ser 'csv' ou 'api'.")
            logger.info(f"[{s}] Dados carregados ({len(self.hist[s])} candles)")
            if len(self.hist[s]) <= self.warmup + 2:
                raise RuntimeError(f"[{s}] Histórico insuficiente para warmup={self.warmup}.")

    def _bootstrap_warmup(self):
        for s in self.symbols:
            for k in self.hist[s][:self.warmup]:
                self.mds.on_kline_closed(s, self.interval, k)

    def _record_trade(self, symbol, side, qty, price, pnl, reason):
        self.trades.append({
            "symbol": symbol, "side": side, "qty": qty,
            "price": price, "pnl": pnl, "reason": reason
        })
        append_trade(symbol, side, qty, price, pnl=pnl)

    def _step_symbol(self, symbol, idx):
        k = self.hist[symbol][idx]
        self.mds.on_kline_closed(symbol, self.interval, k)
        price = float(k["c"])
        set_last_tick_now()

        risk_cfg = self.cfg.get("risk", {})
        stop_loss_pct = risk_cfg.get("stop_loss_pct", 0)
        take_profit_pct = risk_cfg.get("take_profit_pct", 0)
        max_daily_loss_pct = risk_cfg.get("max_daily_loss_pct", 0)

        pos = self.positions[symbol]
        if pos["qty"] > 0:
            sl_price = pos["price"] * (1 - stop_loss_pct / 100)
            tp_price = pos["price"] * (1 + take_profit_pct / 100)
            if price <= sl_price:
                pnl = (price - pos["price"]) * pos["qty"]
                self.positions[symbol] = {"qty": 0.0, "price": 0.0}
                self.pnl_daily += pnl
                self._record_trade(symbol, "sell", pos["qty"], price, pnl, "SL")
                return
            elif price >= tp_price:
                pnl = (price - pos["price"]) * pos["qty"]
                self.positions[symbol] = {"qty": 0.0, "price": 0.0}
                self.pnl_daily += pnl
                self._record_trade(symbol, "sell", pos["qty"], price, pnl, "TP")
                return

        for strat_name in self.cfg["strategies"].get(symbol, []):
            signal = self.strat_mgr.get_strategy(strat_name, symbol).generate_signal()
            if signal in ["buy", "sell"]:
                if is_recent_signal(symbol, signal, idx):
                    continue
                add_recent_signal(symbol, signal, idx)
                if abs(self.pnl_daily) >= (max_daily_loss_pct / 100) * self.capital_inicial:
                    continue

                if signal == "buy" and pos["qty"] <= 0:
                    qty_usdt = self.risk.position_size_from_balance(self.capital_inicial)
                    qty = qty_usdt / price
                    self.positions[symbol] = {"qty": qty, "price": price}
                    self._record_trade(symbol, "buy", qty, price, 0.0, "ENTRY")
                elif signal == "sell" and pos["qty"] > 0:
                    pnl = (price - pos["price"]) * pos["qty"]
                    self.positions[symbol] = {"qty": 0.0, "price": 0.0}
                    self.pnl_daily += pnl
                    self._record_trade(symbol, "sell", pos["qty"], price, pnl, "EXIT")

    def _generate_report(self):
        if not self.trades:
            logger.info("Nenhum trade para reportar.")
            return

        df = pd.DataFrame(self.trades)
        os.makedirs("data", exist_ok=True)
        csv_path = "data/backtest_report.csv"
        df.to_csv(csv_path, index=False)

        total_trades = len(df)
        wins = df[df["pnl"] > 0]
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = df["pnl"].sum()
        avg_pnl = df["pnl"].mean()
        drawdown = df["pnl"].cumsum().min()

        logger.info(f"📊 Relatório salvo em {csv_path}")
        logger.info(f"Total Trades: {total_trades} | Win Rate: {win_rate:.2f}% | PnL Total: {total_pnl:.2f}")
        logger.info(f"PnL Médio: {avg_pnl:.2f} | Máx. Drawdown: {drawdown:.2f}")

        # --- Gráficos ---
        equity = df["pnl"].cumsum()
        plt.figure(figsize=(10, 6))
        plt.plot(equity, label="Equity Curve", color="blue")
        plt.title("Evolução do Capital (Equity Curve)")
        plt.xlabel("Trade #")
        plt.ylabel("Capital acumulado (USDT)")
        plt.legend()
        plt.grid(True)
        plt.savefig("data/equity_curve.png")
        plt.close()

        plt.figure(figsize=(10, 6))
        plt.bar(range(len(df)), df["pnl"], color=["green" if x > 0 else "red" for x in df["pnl"]])
        plt.title("PnL por Trade")
        plt.xlabel("Trade #")
        plt.ylabel("PnL (USDT)")
        plt.grid(True)
        plt.savefig("data/pnl_per_trade.png")
        plt.close()

        plt.figure(figsize=(10, 6))
        plt.hist(df["pnl"], bins=20, color="purple", alpha=0.7)
        plt.title("Distribuição de PnL")
        plt.xlabel("PnL")
        plt.ylabel("Frequência")
        plt.grid(True)
        plt.savefig("data/pnl_distribution.png")
        plt.close()

        self._generate_price_chart(df)
        logger.info("📈 Gráficos salvos em data/")

    def _generate_price_chart(self, df_trades):
        symbol = self.symbols[0]
        prices = [float(k["c"]) for k in self.hist[symbol]]
        times = [pd.to_datetime(k["t"], unit="ms") for k in self.hist[symbol]]

        plt.figure(figsize=(14, 7))
        plt.plot(times, prices, label=f"Preço {symbol}", color="blue", alpha=0.6)

        for _, row in df_trades.iterrows():
            trade_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - row["price"]))
            color = "green" if row["side"] == "buy" else "red"
            marker = "^" if row["side"] == "buy" else "v"
            plt.scatter(times[trade_idx], row["price"], color=color, marker=marker, s=120)
            plt.text(times[trade_idx], row["price"], row["reason"], fontsize=8, ha="center",
                     va="bottom" if row["side"] == "buy" else "top")

        plt.title(f"Trades no par {symbol}")
        plt.xlabel("Tempo")
        plt.ylabel("Preço (USDT)")
        plt.legend()
        plt.grid(True)
        plt.savefig("data/trades_chart.png")
        plt.close()

    def run(self):
        set_initial("backtest", self.symbols)
        self.load_data()
        self._bootstrap_warmup()

        ptr = {s: self.warmup for s in self.symbols}
        cps_sleep = (1.0 / self.cps) if self.cps > 0 else 0.0
        logger.info("🚀 Iniciando backtest acelerado.")
        steps = 0
        while True:
            active = False
            for s in self.symbols:
                if ptr[s] < len(self.hist[s]):
                    self._step_symbol(s, ptr[s])
                    ptr[s] += 1
                    active = True
            if not active:
                break
            steps += 1
            if cps_sleep > 0:
                time.sleep(cps_sleep)
            elif steps % 5000 == 0:
                time.sleep(0.001)

        logger.info(f"✅ Backtest finalizado | PnL diário: {self.pnl_daily:.2f} USDT")
        self._generate_report()

def main():
    cfg = load_config()
    BacktestEngine(cfg).run()

if __name__ == "__main__":
    main()
