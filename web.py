"""
Pagina web locale: carichi le foto delle schedine e vedi il confronto con i risultati.
Usa la stessa pipeline del bot, senza passare da Telegram.

Uso:
    python3 web.py
    poi apri http://localhost:5050
"""
import logging
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

from parser import parse_schedina
from results import get_results_for_matches

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = Flask(__name__)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/analizza", methods=["POST"])
def analizza():
    file = request.files.get("foto")
    if not file:
        return jsonify({"errore": "Nessuna immagine ricevuta"}), 400

    image_bytes = file.read()
    t0 = time.perf_counter()
    try:
        schedina = parse_schedina(image_bytes)
        if not schedina.get("is_schedina", True):
            return jsonify({"errore": "L'immagine non sembra una schedina"}), 422
        if not schedina.get("partite"):
            return jsonify({"errore": "Non sono riuscito a leggere le partite. Prova con una foto più nitida."}), 422

        results = get_results_for_matches(schedina["partite"])
    except Exception as e:
        logger.exception("Errore nell'elaborazione di %s", file.filename)
        return jsonify({"errore": str(e)}), 500

    logger.info("%s elaborata in %.2fs", file.filename, time.perf_counter() - t0)
    return jsonify({"schedina": schedina, "risultati": results})


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 5050))
    app.run(host="127.0.0.1", port=port)
