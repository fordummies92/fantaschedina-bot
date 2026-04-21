import base64
import io
import json
import logging
import os
import time

from groq import Groq
from PIL import Image

logger = logging.getLogger(__name__)

PROMPT = """Analizza questa schedina contenente un'intera giornata di Serie A, quindi 10 partite. Restituisci SOLO un JSON valido, senza testo aggiuntivo.

Struttura richiesta (sostituisci i valori di esempio con quelli reali della schedina):
{
  "is_schedina": true,
  "partite": [
    {
      "casa": "Juventus",
      "trasferta": "Inter",
      "data": "20/04/26",
      "ora": "20:45",
      "mercato": "1X2",
      "pronostico": "1",
      "quota": 1.27
    }
  ],
  "quota_totale": 383.93,
  "importo": 2.00,
  "id_coupon": "393336",
  "utente": "fcpollice"
}

Il campo "mercato" contiene il tipo di scommessa. Valori possibili: 1X2, GG/NG, DC, O/U FT, DC + Over/Under.
Il campo "pronostico" contiene la scelta esatta. Esempi: 1, X, 2, GG, NG, 1X, X2, Over (2.5), Under (4.5), 1X + Under (4.5).

Regole:
- Se l'immagine NON è una schedina di scommesse sportive, restituisci SOLO: {"is_schedina": false}
- Estrai TUTTE e 10 le partite presenti nella schedina, senza saltarne nessuna
- Copia mercato e pronostico esattamente come appaiono nella schedina
- quota_totale è il numero accanto a "QUOTA TOTALE" (non la vincita potenziale)
- Il campo "data" deve essere nel formato DD/MM/YY con l'anno a 2 cifre (es: 20/04/26)
- Restituisci SOLO il JSON, nessun testo prima o dopo"""

RETRY_PROMPT = """Hai estratto solo {n} partite su 10. La schedina ne contiene esattamente 10.
Rianalizza l'immagine con attenzione e restituisci SOLO il JSON completo con tutte e 10 le partite. Non omettere nessuna riga della schedina.

Regole importanti:
- Ogni partita DEVE avere "casa" e "trasferta" con il nome reale della squadra (mai stringa vuota, mai "...", mai placeholder)
- Se non riesci a leggere un nome, scrivi il nome parziale che riesci a vedere
- Restituisci esattamente 10 oggetti nell'array "partite"

Usa esattamente questa struttura:
{{
  "is_schedina": true,
  "partite": [
    {{
      "casa": "Juventus",
      "trasferta": "Inter",
      "data": "20/04/26",
      "ora": "20:45",
      "mercato": "1X2",
      "pronostico": "1",
      "quota": 1.27
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
    logger.info("[parser] %s raw output (len=%d): %s", label, len(raw), raw[:800])
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _parse_with_groq(image_bytes: bytes) -> dict:
    """Parser primario: Groq vision LLM."""
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

            result = _clean_partite(result)
            partite = result.get("partite", [])
            if len(partite) < 10:
                logger.info("[parser] groq retry: only %d valid partite, retrying", len(partite))
                retry_prompt = RETRY_PROMPT.format(n=len(partite))
                result = _call_groq(client, b64, retry_prompt, label="groq retry")
                result = _clean_partite(result)

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


def _parse_with_deterministic(image_bytes: bytes) -> dict | None:
    """Fallback deterministico (Tesseract + regex). Ritorna None se fallisce."""
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
        return result if partite else None
    except Exception:
        logger.exception("[parser] deterministic failed in %.2fs", time.perf_counter() - t_det)
        return None


def parse_schedina(image_bytes: bytes) -> dict:
    """
    Parser primario: Groq vision LLM (veloce, ~5s).
    Fallback: Tesseract deterministico, solo se Groq va in 429 o errore grave.
    """
    try:
        result = _parse_with_groq(image_bytes)
        if len(result.get("partite", [])) < 10:
            logger.warning("[parser] Groq returned %d partite after retries, trying deterministic", len(result.get("partite", [])))
            det = _parse_with_deterministic(image_bytes)
            if det and len(det.get("partite", [])) > len(result.get("partite", [])):
                logger.info("[parser] deterministic has more partite (%d), using it", len(det.get("partite", [])))
                return det
        return result
    except Exception as e:
        logger.warning("[parser] Groq failed (%s), falling back to deterministic", e)
        result = _parse_with_deterministic(image_bytes)
        if result:
            return result
        raise