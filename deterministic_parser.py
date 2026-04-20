"""
Parser deterministico per schedine in formato fisso.
OCR con Tesseract + regex basato sul layout esatto del bookmaker.
"""
import io
import re

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

# Mercati noti, ordinati dal più lungo al più corto per evitare falsi match
KNOWN_MARKETS = [
    "DC + Over/Under",
    "DC + Multigoal",
    "GG/NG",
    "O/U FT",
    "1X2",
    "DC",
]

# Data evento: DD/MM/YY HH:MM (senza secondi — distingue dall'header DATA)
EVENT_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})(?!:)")
HEADER_DATE_RE = re.compile(r"\d{2}:\d{2}:\d{2}")


def preprocess(image_bytes: bytes) -> Image.Image:
    """Ottimizza l'immagine per OCR: grayscale, upscale, contrasto."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img = ImageOps.autocontrast(img)
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.3)
    return img


def ocr_text(image_bytes: bytes) -> str:
    img = preprocess(image_bytes)
    return pytesseract.image_to_string(img, lang="ita+eng", config="--oem 3 --psm 6")


def parse_market_line(line: str):
    """Parsea una riga 'MERCATO    PRONOSTICO    QUOTA' → tupla."""
    # Quota alla fine
    quota_m = re.search(r"(\d+[.,]\d+)\s*$", line)
    if not quota_m:
        return None
    try:
        quota = float(quota_m.group(1).replace(",", "."))
    except ValueError:
        return None
    rest = line[: quota_m.start()].strip()

    # Match mercato (dal più lungo al più corto)
    for market in KNOWN_MARKETS:
        if rest.upper().startswith(market.upper()):
            prediction = rest[len(market):].strip()
            # Normalizza parentesi graffe lette da Tesseract come }
            prediction = prediction.replace("{", "(").replace("}", ")")
            # Rimuovi spazi dentro le parentesi: "( 4.5 )" → "(4.5)"
            prediction = re.sub(r"\(\s+", "(", prediction)
            prediction = re.sub(r"\s+\)", ")", prediction)
            return market, prediction, quota

    # Fallback: split per spazi multipli
    parts = re.split(r"\s{2,}", rest)
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]), quota

    return None


def parse_event_block(lines: list, start: int) -> dict:
    """Parsea un blocco evento di 3 righe a partire dalla riga data."""
    if start + 2 >= len(lines):
        return None

    date_line = lines[start]
    m = EVENT_DATE_RE.search(date_line)
    if not m:
        return None
    data, ora = m.group(1), m.group(2)

    # Riga successiva: "Squadra A - Squadra B"
    team_line = lines[start + 1]
    team_m = re.match(r"^(.+?)\s+-\s+(.+)$", team_line)
    if not team_m:
        return None
    casa = team_m.group(1).strip()
    trasferta = team_m.group(2).strip()

    # Riga mercato
    market_line = lines[start + 2]
    parsed = parse_market_line(market_line)

    # Se fallisce, potrebbe essere spezzata su 2 righe (es: mercato lungo)
    if not parsed and start + 3 < len(lines):
        combined = market_line + " " + lines[start + 3]
        parsed = parse_market_line(combined)

    if not parsed:
        return None

    mercato, pronostico, quota = parsed
    return {
        "casa": casa,
        "trasferta": trasferta,
        "data": data,
        "ora": ora,
        "mercato": mercato,
        "pronostico": pronostico.upper(),
        "quota": quota,
    }


def extract_header_fields(lines: list) -> dict:
    id_coupon = None
    utente = None
    quota_totale = None
    importo = None

    for i, line in enumerate(lines):
        if re.match(r"^ID\s+coupon", line, re.IGNORECASE):
            m = re.search(r"(\d{5,})", line)
            if not m and i + 1 < len(lines):
                m = re.search(r"(\d{5,})", lines[i + 1])
            if m:
                id_coupon = m.group(1)

        elif re.match(r"^utente", line, re.IGNORECASE):
            parts = line.split()
            if len(parts) >= 2:
                utente = parts[-1]

        elif re.search(r"QUOTA\s+TOTALE", line, re.IGNORECASE):
            m = re.search(r"(\d+[.,]\d+)", line)
            if not m and i + 1 < len(lines):
                m = re.search(r"(\d+[.,]\d+)", lines[i + 1])
            if m:
                try:
                    quota_totale = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass

        elif re.match(r"^IMPORTO", line, re.IGNORECASE):
            m = re.search(r"(\d+[.,]\d+)", line)
            if not m and i + 1 < len(lines):
                m = re.search(r"€?\s*(\d+[.,]\d+)", lines[i + 1])
            if m:
                try:
                    importo = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass

    return {
        "id_coupon": id_coupon,
        "utente": utente,
        "quota_totale": quota_totale,
        "importo": importo,
    }


def parse_schedina_deterministic(image_bytes: bytes) -> dict:
    text = ocr_text(image_bytes)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    header = extract_header_fields(lines)
    partite = []

    for i, line in enumerate(lines):
        # Escludi la riga header DATA (ha i secondi)
        if HEADER_DATE_RE.search(line):
            continue
        if not EVENT_DATE_RE.search(line):
            continue

        event = parse_event_block(lines, i)
        if event:
            partite.append(event)

    return {
        "is_schedina": len(partite) > 0,
        "partite": partite,
        "quota_totale": header["quota_totale"],
        "importo": header["importo"],
        "id_coupon": header["id_coupon"],
        "utente": header["utente"],
    }
