import asyncio
import os
import time
from binance.client import Client
from binance.streams import BinanceSocketManager
from loguru import logger

from bot.config import load_config
from bot.utils import normalize_symbol
from bot.market_data import MarketDataService
from bot.strategies import StrategyManager
from bot.risk import RiskManager
from bot.notifier import Notifier
from bot.ia import IAManager
from bot.execution import ExecutionService
from bot.binance_client import BinanceClient
from bot.state import set_initial, set_last_tick_now, is_recent_signal, add_recent_signal

PAUSE_FLAG = os.getenv("BOT_PAUSE_FLAG", "pause.flag")
TF_FLAG = os.getenv("BOT_TIMEFRAME_FLAG", "timeframe.flag")

VALID_TIMEFRAMES = {
    "1s","1m","3m","5m","15m","30m",
    "1h","2h","4h","6h","8h","12h",
    "1d","3d","1w","1M"
}

class WebSocketBot:
    def __init__(self):
        self.config = load_config()
        self.client = Client(self.config['binance_api_key'], self.config['binance_api_secret'])
        self.client.API_URL = self.config['binance_api_url']
        ambiente = 'TESTNET' if self.config.get('testnet') else 'PRODUÇÃO'
        logger.info(f"🔧 WebSocket conectando na Binance SPOT {ambiente}")

        self.symbols_lower = [s.lower() for s in self.config['symbols']]
        self.symbols_upper = [normalize_symbol(s) for s in self.config['symbols']]

        self.bsm = BinanceSocketManager(self.client)
        self.mds = MarketDataService(maxlen=2000)

        # timeframe inicial: flag > config.yaml > 1m
        self.timeframe = self._read_timeframe()
        logger.info(f"🕒 Timeframe inicial: {self.timeframe}")
        self._tf_last_mtime = self._flag_mtime()

        # Bootstrap REST
        rest = BinanceClient()
        for sym in self.symbols_upper:
            self.mds.bootstrap_from_rest(rest.client, sym, self.timeframe, limit=200)

        self.risk = RiskManager(self.config['risk'])
        self.notifier = Notifier()
        self.exec = ExecutionService(self.risk, notifier=self.notifier)
        self.strategies = StrategyManager(self.config, self.mds)
        self.ia_manager = IAManager(self.config) if self.config.get('ia', {}).get('enabled') else None

        set_initial(self.config.get("mode", "trade"), self.symbols_upper)
        self._last_msg_ts = time.time()
        self._heartbeat_timeout = 15

        # Reconcilia posições/PM no start
        try:
            self.exec.reconcile_all(self.symbols_upper)
        except Exception as e:
            logger.warning(f"Reconciliação inicial falhou: {e}")

    def _read_timeframe(self) -> str:
        if os.path.exists(TF_FLAG):
            try:
                tf = open(TF_FLAG).read().strip()
                if tf in VALID_TIMEFRAMES:
                    return tf
            except Exception:
                pass
        tf = (self.config.get("timeframe") or "1m").strip()
        return tf if tf in VALID_TIMEFRAMES else "1m"

    def _flag_mtime(self) -> float:
        try:
            return os.path.getmtime(TF_FLAG)
        except Exception:
            return 0.0

    def _is_paused(self) -> bool:
        return os.path.exists(PAUSE_FLAG)

    async def handle_candles(self, msg):
        if msg.get('e') != 'kline':
            return
        self._last_msg_ts = time.time()
        set_last_tick_now()

        k = msg['k']
        symbol = normalize_symbol(k['s'])
        is_closed = bool(k['x'])

        if is_closed:
            self.mds.on_kline_closed(symbol, self.timeframe, k)
            close_price = float(k['c'])
            candle_close_ts = int(k['T'])
            logger.info(f"[{symbol}] Candle {self.timeframe} fechado - Close: {close_price}")

            # Proteção por software (SL/TP) — se OCO não foi possível:
            try:
                self.exec.check_protective_exit(symbol, close_price)
            except Exception as e:
                logger.warning(f"[{symbol}] watchdog proteção falhou: {e}")

            if self.ia_manager:
                try:
                    self.ia_manager.check_and_update_params(symbol, self.strategies)
                except Exception as e:
                    logger.warning(f"[IA] Falha no ajuste {symbol}: {e}")

            strats = self.config['strategies'].get(symbol, [])
            for strat_name in strats:
                try:
                    strategy = self.strategies.get_strategy(strat_name, symbol)
                    signal = strategy.generate_signal()
                    logger.info(f"[{symbol}] Estratégia {strat_name} => Sinal: {signal}")

                    if self.config['mode'] == "trade" and signal in ["buy", "sell"]:
                        # idempotência: evita duplicar sinal por candle
                        if is_recent_signal(symbol, signal, candle_close_ts, ttl_sec=30):
                            logger.warning(f"[{symbol}] Sinal {signal} ignorado (antirrepique).")
                            continue
                        add_recent_signal(symbol, signal, candle_close_ts, ttl_sec=30)

                        if self._is_paused():
                            logger.warning("⏸️  Trading pausado (pause.flag). Ignorando sinais.")
                            continue

                        usdt = self.client.get_asset_balance(asset='USDT')
                        usdt_free = float(usdt['free']) if usdt else 0.0
                        await asyncio.to_thread(self.exec.place_signal, symbol, signal, usdt_free)
                except Exception as e:
                    logger.exception(f"[{symbol}] Erro na estratégia {strat_name}: {e}")
                    self.notifier.send(f"❌ [{symbol}] Erro estratégia {strat_name}: {e}")

    async def watchdog(self):
        """Reconecta se faltar mensagens OU se a timeframe.flag mudar."""
        while True:
            await asyncio.sleep(self._heartbeat_timeout)
            delta = time.time() - self._last_msg_ts
            if delta > self._heartbeat_timeout:
                logger.warning(f"⚠️  Sem mensagens há {int(delta)}s. Reconectando WS...")
                raise ConnectionError("Heartbeat timeout")

            # detecta troca de timeframe via flag
            mtime = self._flag_mtime()
            if mtime and mtime != self._tf_last_mtime:
                new_tf = self._read_timeframe()
                if new_tf != self.timeframe:
                    logger.info(f"🔄 Timeframe alterado: {self.timeframe} → {new_tf}. Forçando reconexão...")
                    self.timeframe = new_tf
                    self._tf_last_mtime = mtime
                    raise ConnectionError("Timeframe changed")

    async def start(self):
        backoff = 1
        while True:
            try:
                streams = [f"{s}@kline_{self.timeframe}" for s in self.symbols_lower]
                async with self.bsm.multiplex_socket(streams) as stream:
                    logger.info(f"▶️  Streams ativos: {streams}")
                    task_watchdog = asyncio.create_task(self.watchdog())
                    while True:
                        msg = await stream.recv()
                        await self.handle_candles(msg)
                        backoff = 1
            except Exception as e:
                logger.error(f"WS caiu: {e}. Reconnect em {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                # rebootstrap rápido no novo TF (últimos 200 candles)
                try:
                    rest = BinanceClient()
                    for sym in self.symbols_upper:
                        self.mds.bootstrap_from_rest(rest.client, sym, self.timeframe, limit=200)
                except Exception as be:
                    logger.warning(f"Falha ao rebootstrap no novo TF: {be}")
            finally:
                # cancela watchdog
                for t in asyncio.all_tasks():
                    if t is not asyncio.current_task() and getattr(t.get_coro(), "__name__", "") == "watchdog":
                        t.cancel()

if __name__ == "__main__":
    bot = WebSocketBot()
    asyncio.run(bot.start())
