"""
Test locale: processa schedine da una cartella usando la stessa pipeline del bot.
Non tocca Telegram, non tocca il bot in produzione.

Uso:
    python3 test_local.py /Users/marconasu/Desktop/schedine_test
"""
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from parser import parse_schedina
from results import get_results_for_matches, check_prediction, determine_outcome
from formatter import format_output


def strip_html(s: str) -> str:
    """Rimuove i tag HTML per output leggibile su terminale."""
    return re.sub(r"<[^>]+>", "", s)


def process_one(image_path: Path) -> None:
    print("=" * 80)
    print(f"FILE: {image_path.name}")
    print("=" * 80)

    image_bytes = image_path.read_bytes()
    schedina = parse_schedina(image_bytes)

    if not schedina.get("is_schedina", True):
        print("→ Non riconosciuta come schedina. Skip.\n")
        return

    partite = schedina.get("partite") or []
    if not partite:
        print("→ Nessuna partita estratta dal parser.\n")
        return

    print(f"Utente: {schedina.get('utente', '?')}")
    print(f"Quota dichiarata: {schedina.get('quota_totale', '?')}")
    print(f"Partite estratte: {len(partite)}")
    print(f"Fallback OCR usato: {schedina.get('used_fallback', False)}")
    print()
    print("Mercati/Pronostici letti dal parser:")
    for i, p in enumerate(partite, 1):
        print(f"  {i:2d}. {p.get('casa', '?'):20s} - {p.get('trasferta', '?'):20s}  "
              f"| mercato={p.get('mercato', '?')!r:30s} pronostico={p.get('pronostico', '?')!r}")
    print()

    results = get_results_for_matches(partite)

    print("Output formatter (come lo riceverebbe l'utente):")
    print("-" * 80)
    out = format_output(schedina, results)
    print(strip_html(out))
    print("-" * 80)

    # Diagnostica extra: per ogni partita mostra outcome e perché check_prediction risponde così
    print("\nDiagnostica per partita:")
    for p, r in zip(partite, results):
        casa = p.get("casa", "?")
        trasferta = p.get("trasferta", "?")
        mercato = p.get("mercato", "")
        prono = p.get("pronostico", "")
        if not r.get("found"):
            print(f"  ❓ {casa} - {trasferta}: NON TROVATA su TheSportsDB")
            continue
        if r.get("correct") is None:
            print(f"  ⏳ {casa} - {trasferta}: non finita (status={r.get('status')!r})")
            continue
        score = r["score"]
        # ricalcola outcome per mostrarlo
        h, a = map(int, score.split("-"))
        outcome = determine_outcome(h, a)
        verified = check_prediction(mercato, prono, h, a)
        mark = "✅" if verified else "❌"
        print(f"  {mark} {casa} - {trasferta}  {score} (esito={outcome})  "
              f"| mercato={mercato!r} prono={prono!r} → {verified}")
    print()


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 test_local.py <cartella_immagini>")
        sys.exit(1)
    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"Cartella non valida: {folder}")
        sys.exit(1)

    images = sorted(
        [p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    )
    if not images:
        print(f"Nessuna immagine trovata in {folder}")
        sys.exit(1)

    print(f"Trovate {len(images)} immagini. Processo...\n")
    for img in images:
        try:
            process_one(img)
        except Exception as e:
            print(f"!! Errore su {img.name}: {e}\n")


if __name__ == "__main__":
    main()
