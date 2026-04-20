import base64
import io
import json
import os
import time

from groq import Groq
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
- Estrai sempre TUTTE e 10 le partite presenti nella schedina
- Copia il pronostico esattamente come appare
- quota_totale è il numero accanto a "QUOTA TOTALE" (non la vincita)
- Il campo "data" deve essere nel formato DD/MM/YY con l'anno a 2 cifre (es: 20/04/26 per il 20 aprile 2026)
- Restituisci SOLO il JSON, nessun testo prima o dopo"""

RETRY_PROMPT = """Hai estratto solo {n} partite su 10. La schedina ne contiene esattamente 10.
Rianalizza l'immagine con attenzione e restituisci SOLO il JSON completo con tutte e 10 le partite.
Non omettere nessuna riga della schedina."""


def _image_to_base64(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _call_groq(client: Groq, b64: str, prompt: str) -> dict:
    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def parse_schedina(image_bytes: bytes) -> dict:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    b64 = _image_to_base64(image_bytes)

    for attempt in range(3):
        try:
            result = _call_groq(client, b64, PROMPT)

            # Se non è una schedina, ritorna subito
            if not result.get("is_schedina", True):
                return result

            # Se mancano partite, riprova con prompt specifico
            partite = result.get("partite", [])
            if len(partite) < 10:
                retry_prompt = RETRY_PROMPT.format(n=len(partite))
                result = _call_groq(client, b64, retry_prompt)

            return result

        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(10)
            elif "429" in str(e):
                from fallback_parser import parse_schedina_fallback
                return parse_schedina_fallback(image_bytes)
            else:
                raise
