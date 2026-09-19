# Prova a mano del parser di riserva. Serve una foto di schedina chiamata
# schedina.jpg accanto a questo file: nel repo non c'e' (dati personali).
from fallback_parser import parse_schedina_fallback
import json

with open("schedina.jpg", "rb") as f:
    image_bytes = f.read()

result = parse_schedina_fallback(image_bytes)
print(json.dumps(result, indent=2, ensure_ascii=False))
