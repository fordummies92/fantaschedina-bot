import io
import re

import pytesseract
from PIL import Image, ImageEnhance


def preprocess(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    w, h = img.size
    return img.resize((w * 2, h * 2), Image.LANCZOS)


def parse_schedina_fallback(image_bytes: bytes) -> dict:
    img = preprocess(image_bytes)
    text = pytesseract.image_to_string(img, lang="ita+eng", config="--oem 3 --psm 6")
    return parse_text(text)


def parse_text(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    partite = []
    quota_totale = None
    importo = None
    id_coupon = None
    utente = None

    MARKETS = ["DC + Over/Under", "DC + Multigame", "GG/NG", "O/U FT", "1X2", "DC"]

    i = 0
    while i < len(lines):
        line = lines[i]

        # Coupon ID
        if re.search(r"coupon|id\s+coupon", line, re.IGNORECASE):
            m = re.search(r"\b(\d{5,})\b", line)
            if m:
                id_coupon = m.group(1)

        # Utente
        if re.search(r"utente", line, re.IGNORECASE):
            parts = line.split()
            if len(parts) >= 2:
                utente = parts[-1]

        # Serie A event line
        if re.search(r"serie\s*a|calcio", line, re.IGNORECASE):
            date_m = re.search(r"(\d{2}/\d{2}/\d{2,4})\s+(\d{2}:\d{2})", line)
            if date_m and i + 2 < len(lines):
                data = date_m.group(1)
                # normalize to DD/MM/YY
                if len(data.split("/")[2]) == 4:
                    parts = data.split("/")
                    data = f"{parts[0]}/{parts[1]}/{parts[2][2:]}"
                ora = date_m.group(2)

                teams_line = lines[i + 1]
                teams_m = re.match(r"^(.+?)\s*-\s*(.+)$", teams_line)
                if teams_m:
                    casa = teams_m.group(1).strip()
                    trasferta = teams_m.group(2).strip()

                    market_line = lines[i + 2] if i + 2 < len(lines) else ""
                    # Sometimes market info spans 2 lines
                    if i + 3 < len(lines) and not re.search(
                        r"serie\s*a|calcio|importo|quota|multipla",
                        lines[i + 3],
                        re.IGNORECASE,
                    ):
                        market_line += " " + lines[i + 3]

                    parsed = _parse_market_line(market_line)
                    if parsed:
                        mercato, pronostico, quota = parsed
                        partite.append({
                            "casa": casa,
                            "trasferta": trasferta,
                            "data": data,
                            "ora": ora,
                            "mercato": mercato,
                            "pronostico": pronostico,
                            "quota": quota,
                        })

        # Quota totale
        if re.search(r"quota\s+totale", line, re.IGNORECASE):
            m = re.search(r"(\d+[.,]\d+)", line)
            if not m and i + 1 < len(lines):
                m = re.search(r"(\d+[.,]\d+)", lines[i + 1])
            if m:
                quota_totale = float(m.group(1).replace(",", "."))

        # Importo
        if re.search(r"^importo", line, re.IGNORECASE):
            m = re.search(r"(\d+[.,]\d+)", line)
            if m:
                importo = float(m.group(1).replace(",", "."))

        i += 1

    return {
        "is_schedina": bool(partite),
        "partite": partite,
        "quota_totale": quota_totale,
        "importo": importo,
        "id_coupon": id_coupon,
        "utente": utente,
    }


def _parse_market_line(line: str):
    quota_m = re.search(r"(\d+[.,]\d+)\s*$", line)
    if not quota_m:
        return None
    quota = float(quota_m.group(1).replace(",", "."))
    rest = line[: quota_m.start()].strip()

    MARKETS = ["DC + Over/Under", "DC + Multigame", "GG/NG", "O/U FT", "1X2", "DC"]
    for market in MARKETS:
        if rest.upper().startswith(market.upper()):
            prediction = rest[len(market):].strip()
            return market, prediction, quota

    # Fallback: split by 2+ spaces
    parts = re.split(r"\s{2,}", rest)
    if len(parts) >= 2:
        return parts[0], parts[-1], quota

    return None
