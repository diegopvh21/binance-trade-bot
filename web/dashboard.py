from flask import Flask, render_template_string, redirect, request, url_for
import time
import os
from bot.state import get as get_state
from bot.config import load_config

PAUSE_FLAG = os.getenv("BOT_PAUSE_FLAG", "pause.flag")
TF_FLAG = os.getenv("BOT_TIMEFRAME_FLAG", "timeframe.flag")

VALID_TIMEFRAMES = [
    "1s","1m","3m","5m","15m","30m",
    "1h","2h","4h","6h","8h","12h",
    "1d","3d","1w","1M"
]

app = Flask(__name__)

TPL = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Binance Trade Bot - Dashboard</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; }
    .pill { display:inline-block; padding:4px 10px; border-radius:999px; background:#eee; }
    table { border-collapse: collapse; width: 100%; }
    th, td { text-align: left; border-bottom: 1px solid #eee; padding: 8px; }
    .ok { color: #0a0; } .warn { color:#a60; } .bad { color:#a00; }
    .btn { padding:8px 12px; border:1px solid #ccc; border-radius:6px; text-decoration:none; color:#000; }
    .btn:hover { background:#f2f2f2; }
    form { display:inline; }
    select { padding:6px; }
  </style>
</head>
<body>
  <h1>Binance Trade Bot</h1>

  <p>
    <b>Modo:</b> {{ st["mode"] }}
    &nbsp;|&nbsp; <b>Pares:</b> {{ ", ".join(st["symbols"]) }}
    &nbsp;|&nbsp; <b>Timeframe:</b> <span class="pill">{{ timeframe }}</span>
    &nbsp;|&nbsp;
    <form method="post" action="{{ url_for('set_timeframe') }}">
      <label for="tf">Alterar:</label>
      <select name="tf" id="tf">
        {% for tf in valid_tfs %}
          <option value="{{ tf }}" {% if tf==timeframe %}selected{% endif %}>{{ tf }}</option>
        {% endfor %}
      </select>
      <button class="btn" type="submit">Aplicar</button>
    </form>
  </p>

  <h3>Status</h3>
  <ul>
    <li>WS uptime: {{ uptime_str }}</li>
    <li>Último tick: <span class="{{ tick_cls }}">{{ last_tick_age }}s atrás</span></li>
    <li>Trading:
      {% if paused %}
        <span class="bad">PAUSADO</span> —
        <a class="btn" href="/resume">Retomar</a>
      {% else %}
        <span class="ok">ATIVO</span> —
        <a class="btn" href="/pause">Pausar</a>
      {% endif %}
    </li>
  </ul>

  <h3>PNL diário</h3>
  <p class="{{ pnl_cls }}"><b>{{ "{:+.4f}".format(st["pnl_daily"]) }} USDT</b></p>

  <h3>Últimos trades</h3>
  {% if st["trades"] %}
  <table>
    <thead><tr><th>Quando</th><th>Par</th><th>Lado</th><th>Qtd</th><th>Preço</th><th>PNL</th></tr></thead>
    <tbody>
      {% for t in st["trades"]|reverse|list[:30] %}
      <tr>
        <td>{{ time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t["ts"])) }}</td>
        <td>{{ t["symbol"] }}</td>
        <td>{{ t["side"] }}</td>
        <td>{{ "%.6f"|format(t["qty"]) }}</td>
        <td>{{ "%.6f"|format(t["price"]) }}</td>
        <td class="{{ 'ok' if t['pnl']>0 else ('bad' if t['pnl']<0 else '') }}">{{ "{:+.4f}".format(t["pnl"]) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>Nenhum trade ainda.</p>
  {% endif %}

  <hr>
  <p><a href="/metrics">/metrics</a> &middot; <a href="/">Atualizar</a></p>
</body>
</html>
"""

def _current_timeframe(cfg):
    # prioridade: flag > config.yaml
    if os.path.exists(TF_FLAG):
        try:
            with open(TF_FLAG, "r") as f:
                tf = f.read().strip()
            if tf:
                return tf
        except Exception:
            pass
    return (cfg.get("timeframe") or "1m").strip()

@app.route("/")
def index():
    st = get_state()
    cfg = load_config()
    timeframe = _current_timeframe(cfg)

    now = int(time.time())
    uptime = max(0, now - int(st.get("ws_uptime_start", 0)))
    uptime_str = f"{uptime//3600:02d}h{(uptime%3600)//60:02d}m{uptime%60:02d}s"

    last_tick_ts = int(st.get("last_tick_ts", 0))
    last_tick_age = (now - last_tick_ts) if last_tick_ts else 999999
    tick_cls = "ok" if last_tick_age <= 5 else ("warn" if last_tick_age <= 20 else "bad")

    pnl = float(st.get("pnl_daily", 0.0))
    pnl_cls = "ok" if pnl > 0 else ("bad" if pnl < 0 else "")

    paused = os.path.exists(PAUSE_FLAG)

    return render_template_string(
        TPL,
        st=st,
        time=time,
        uptime_str=uptime_str,
        last_tick_age=last_tick_age,
        tick_cls=tick_cls,
        pnl_cls=pnl_cls,
        paused=paused,
        timeframe=timeframe,
        valid_tfs=VALID_TIMEFRAMES
    )

@app.route("/pause")
def pause():
    with open(PAUSE_FLAG, "w") as f:
        f.write("1\n")
    return redirect("/")

@app.route("/resume")
def resume():
    try:
        if os.path.exists(PAUSE_FLAG):
            os.remove(PAUSE_FLAG)
    except Exception:
        pass
    return redirect("/")

@app.route("/timeframe", methods=["POST"])
def set_timeframe():
    tf = (request.form.get("tf") or "").strip()
    if tf not in VALID_TIMEFRAMES:
        return redirect(url_for("index"))
    with open(TF_FLAG, "w") as f:
        f.write(tf + "\n")
    # O WS vai detectar e reconectar no novo TF
    return redirect(url_for("index"))

@app.route("/metrics")
def metrics():
    st = get_state()
    now = int(time.time())
    last_tick_ts = int(st.get("last_tick_ts", 0))
    last_tick_age = (now - last_tick_ts) if last_tick_ts else 999999
    paused = 1 if os.path.exists(PAUSE_FLAG) else 0

    lines = []
    lines.append("# HELP bot_up 1 se o processo do painel está ativo")
    lines.append("# TYPE bot_up gauge")
    lines.append("bot_up 1")

    lines.append("# HELP bot_paused 1 se trading está pausado")
    lines.append("# TYPE bot_paused gauge")
    lines.append(f"bot_paused {paused}")

    lines.append("# HELP bot_last_tick_age_seconds Idade do último tick (segundos)")
    lines.append("# TYPE bot_last_tick_age_seconds gauge")
    lines.append(f"bot_last_tick_age_seconds {last_tick_age}")

    lines.append("# HELP bot_pnl_daily_usdt PnL diário em USDT (estimado)")
    lines.append("# TYPE bot_pnl_daily_usdt gauge")
    lines.append(f"bot_pnl_daily_usdt {float(st.get('pnl_daily', 0.0))}")

    return "\n".join(lines), 200, {"Content-Type": "text/plain; version=0.0.4"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
