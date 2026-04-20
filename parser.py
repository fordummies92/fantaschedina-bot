import base64
import io
import json
import logging
import os
import time

from groq import Groq
from PIL import Image

logger = logging.getLogger(__name__)

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
Rianalizza l'immagine con attenzione e restituisci SOLO il JSON completo con tutte e 10 le partite. Non omettere nessuna riga della schedina.

Usa esattamente questa struttura:
{{
  "is_schedina": true,
  "partite": [
    {{
      "casa": "...",
      "trasferta": "...",
      "data": "DD/MM/YY",
      "ora": "HH:MM",
      "mercato": "...",
      "pronostico": "...",
      "quota": 1.00
    }}
  ],
  "quota_totale": 0.00,
  "importo": 0.00,
  "id_coupon": "...",
  "utente": "..."
}}

Restituisci SOLO il JSON, nessun testo prima o dopo."""


def _image_to_base64(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _call_groq(client: Groq, b64: str, prompt: str, label: str = "groq") -> dict:
    t = time.perf_counter()
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
    logger.info("[parser] %s API call: %.2fs", label, time.perf_counter() - t)
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _parse_with_groq(image_bytes: bytes) -> dict:
    """Fallback: parsing tramite Groq vision LLM."""
    t_total = time.perf_counter()
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    t_b64 = time.perf_counter()
    b64 = _image_to_base64(image_bytes)
    logger.info("[parser] groq b64 encode: %.3fs (len=%d)", time.perf_counter() - t_b64, len(b64))

    for attempt in range(3):
        try:
            result = _call_groq(client, b64, PROMPT, label=f"groq primary (try {attempt + 1})")
            if not result.get("is_schedina", True):
                logger.info("[parser] groq total: %.2fs (not-schedina)", time.perf_counter() - t_total)
                return result

            partite = result.get("partite", [])
            if len(partite) < 10:
                logger.info("[parser] groq retry: only %d partite, retrying", len(partite))
                retry_prompt = RETRY_PROMPT.format(n=len(partite))
                result = _call_groq(client, b64, retry_prompt, label="groq retry")

            logger.info(
                "[parser] groq total: %.2fs (partite=%d)",
                time.perf_counter() - t_total,
                len(result.get("partite", [])),
            )
            return result

        except Exception as e:
            if "429" in str(e) and attempt < 2:
                logger.warning("[parser] groq 429, sleep 10s (attempt %d)", attempt + 1)
                time.sleep(10)
            elif "429" in str(e):
                logger.warning("[parser] groq 429 exhausted, fallback to tesseract")
                from fallback_parser import parse_schedina_fallback
                return parse_schedina_fallback(image_bytes)
            else:
                raise


def _clean_partite(result: dict) -> dict:
    """Scarta partite con casa/trasferta vuoti (phantom match da OCR)."""
    if not isinstance(result, dict):
        return result
    partite = result.get("partite") or []
    cleaned = [
        p for p in partite
        if isinstance(p, dict) and (p.get("casa") or "").strip() and (p.get("trasferta") or "").strip()
    ]
    dropped = len(partite) - len(cleaned)
    if dropped:
        logger.warning("[parser] dropped %d partite with empty teams", dropped)
    result["partite"] = cleaned
    return result


def parse_schedina(image_bytes: bytes) -> dict:
    """
    Parser primario deterministico (Tesseract + regex sul formato fisso).
    Se non estrae esattamente 10 partite, fallback su Groq vision LLM.
    """
    from deterministic_parser import parse_schedina_deterministic

    t_det = time.perf_counter()
    try:
        result = parse_schedina_deterministic(image_bytes)
        result = _clean_partite(result)
        partite = result.get("partite", [])
        logger.info(
            "[parser] deterministic: %.2fs (partite=%d)",
            time.perf_counter() - t_det,
            len(partite),
        )
        # Se abbiamo tutte e 10 le partite, ritorna subito
        if len(partite) == 10:
            return result
    except Exception:
        logger.exception("[parser] deterministic failed in %.2fs", time.perf_counter() - t_det)

    # Fallback su Groq vision
    logger.info("[parser] falling back to Groq vision")
    return _clean_partite(_parse_with_groq(image_bytes))