import io
import json
import os
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import google.generativeai as genai
from PIL import Image

PROMPT = """Analizza questa schedina di scommesse sportive italiana e restituisci SOLO un JSON valido, senza testo aggiuntivo.

Struttura richiesta:
{
  "partite": [
    {
      "casa": "nome squadra casa",
      "trasferta": "nome squadra trasferta",
      "data": "DD/MM/YY",
      "ora": "HH:MM",
      "mercato": "tipo mercato esatto dalla schedina (es: 1X2, GG/NG, DC, O/U FT, DC + Over/Under)",
      "pronostico": "scelta esatta dalla schedina (es: 1, X, 2, GG, NG, 1X, X2, Over (2.5), Under (4.5), 1X + Under (4.5))",
      "quota": 1.27
    }
  ],
  "quota_totale": 383.93,
  "importo": 2.00,
  "id_coupon": "393336",
  "utente": "fcpollice"
}

Regole:
- Se l'immagine NON è una schedina di scommesse sportive, restituisci SOLO: {"is_schedina": false}
- Se è una schedina, aggiungi "is_schedina": true al JSON
- Estrai TUTTE le partite presenti nella schedina
- Copia il pronostico esattamente come appare
- quota_totale è il numero accanto a "QUOTA TOTALE" (non la vincita)
- Restituisci SOLO il JSON, nessun testo prima o dopo"""


def parse_schedina(image_bytes: bytes) -> dict:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash")

    img = Image.open(io.BytesIO(image_bytes))

    # Retry automatico in caso di 429 (rate limit)
    for attempt in range(3):
        try:
            response = model.generate_content([PROMPT, img])
            raw = response.text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(10)
            elif "429" in str(e):
                # Quota esaurita → fallback Tesseract
                from fallback_parser import parse_schedina_fallback
                return parse_schedina_fallback(image_bytes)
            else:
                raise
