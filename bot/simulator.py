# simulator.py
import time, math, random
from collections import defaultdict
from loguru import logger

from bot.config import load_config
from bot.market_data import MarketDataService
from bot.strategies import StrategyManager
from bot.state import set_initial, set_last_tick_now, append_trade

def _mk_kline(t_open_ms, o, h, l, c, v):
    # formato semelhante ao do WS da Binance (kline fechado)
    return {
        "t": int(t_open_ms),
        "T": int(t_open_ms + 60_000 - 1),
        "o": f"{o:.8f}",
        "h": f"{h:.8f}",
        "l": f"{l:.8f}",
        "c": f"{c:.8f}",
        "v": f"{v:.8f}",
        "x": True,  # closed
    }

def _rand_candle(prev_close, vol=0.002):
    # random walk simples: retorno ~ N(0, vol)
    r = random.gauss(0, vol)
    c = max(0.00000001, prev_close * (1.0 + r))
    o = prev_close
    h = max(o, c) * (1.0 + abs(random.gauss(0, vol/2)))
    l = min(o, c) * (1.0 - abs(random.gauss(0, vol/2)))
    v = abs(random.gauss(100, 30))
    return o, h, l, c, v

def main():
    cfg = load_config()
    symbols = [s.upper() for s in cfg["symbols"]]
    interval = (cfg.get("timeframe") or "1m").strip()

    logger.info("🎛️  Iniciando simulador offline | símbolos=%s | timeframe=%s", symbols, interval)

    mds = MarketDataService(maxlen=2000)
    strat_mgr = StrategyManager(cfg, mds)

    # estado de posições de papel
    positions = defaultdict(lambda: {"qty": 0.0, "price": 0.0})

    # inicializa painel
    set_initial("simulate", symbols)

    # preços iniciais razoáveis
    seeds = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0, "BNBUSDT": 500.0}
    last_close = {s: seeds.get(s, 100.0 + 50.0*random.random()) for s in symbols}

    # bootstrap de histórico (200 candles)
    now_ms = int(time.time()) * 1000
    start_ms = now_ms - 200 * 60_000
    for s in symbols:
        t = start_ms
        pc = last_close[s]
        for _ in range(200):
            o,h,l,c,v = _rand_candle(pc)
            k = _mk_kline(t, o,h,l,c,v)
            mds.on_kline_closed(s, interval, k)
            pc = c
            t += 60_000
        last_close[s] = pc

    logger.info("📈 Histórico sintético preparado. Iniciando geração de candles ao vivo (offline).")
    t_open = now_ms

    # loop "ao vivo": a cada ~1s gera um novo candle de 1m
    # (ajuste o sleep se quiser acelerar: p/ 1m por segundo use sleep(1))
    while True:
        for s in symbols:
            # gera candle
            o,h,l,c,v = _rand_candle(last_close[s])
            k = _mk_kline(t_open, o,h,l,c,v)
            mds.on_kline_closed(s, interval, k)
            last_close[s] = c

            # update painel heartbeat
            set_last_tick_now()

            # executa estratégias configuradas p/ símbolo
            for strat_name in cfg["strategies"].get(s, []):
                signal = strat_mgr.get_strategy(strat_name, s).generate_signal()

                if signal == "buy" and positions[s]["qty"] <= 0:
                    # compra de papel: usa 50 USDT por trade (ajuste aqui se quiser)
                    quote_usdt = 50.0
                    qty = quote_usdt / c
                    positions[s] = {"qty": qty, "price": c}
                    append_trade(s, "buy", qty, c, pnl=0.0)
                    logger.info(f"🟢 [SIM] {s} BUY qty={qty:.6f} @ {c:.6f}")

                elif signal == "sell" and positions[s]["qty"] > 0:
                    # venda total da posição
                    qty = positions[s]["qty"]
                    entry = positions[s]["price"]
                    pnl = (c - entry) * qty
                    positions[s] = {"qty": 0.0, "price": 0.0}
                    append_trade(s, "sell", qty, c, pnl=pnl)
                    logger.info(f"🔴 [SIM] {s} SELL qty={qty:.6f} @ {c:.6f} | PnL={pnl:+.4f} USDT")

        # próximo “minuto” sintético
        t_open += 60_000
        time.sleep(1)  # 1s = 1 candle de 1m (acelera o teste). Troque para 0.2 p/ ficar mais rápido.

if __name__ == "__main__":
    main()
