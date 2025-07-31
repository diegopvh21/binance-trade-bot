from flask import Flask, render_template_string
from bot.config import load_config
import os

app = Flask(__name__)

@app.route("/")
def index():
    config = load_config()
    # Exemplo básico: você pode adicionar leitura de logs, ordens, saldo, etc.
    return render_template_string("""
    <h1>Binance Trade Bot Dashboard</h1>
    <p><b>Pares:</b> {{ config['symbols'] }}</p>
    <p><b>Modo:</b> {{ config['mode'] }}</p>
    <p><b>Estratégias:</b> {{ config['strategies'] }}</p>
    <hr>
    <p><a href="/reload">Recarregar bot</a></p>
    """, config=config)

@app.route("/reload")
def reload():
    # Placeholder para comandos remotos: reiniciar, pausar, etc.
    os.system("touch reload.flag")
    return "<p>Bot será recarregado no próximo ciclo.</p>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
