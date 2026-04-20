import os
import re
import requests
from datetime import datetime, timedelta
from difflib import SequenceMatcher

FOOTBALL_DATA_URL = "https://api.football-data.org/v4/competitions/SA/matches"

# Mappatura nomi italiani → nomi usati dall'API
TEAM_ALIASES = {
    "inter": ["fc internazionale", "internazionale", "inter milan"],
    "milan": ["ac milan"],
    "juventus": ["juventus fc"],
    "napoli": ["ssc napoli"],
    "roma": ["as roma"],
    "lazio": ["ss lazio"],
    "fiorentina": ["acf fiorentina"],
    "atalanta": ["atalanta bc"],
    "bologna": ["bologna fc"],
    "torino": ["torino fc"],
    "udinese": ["udinese calcio"],
    "genoa": ["genoa cfc"],
    "cagliari": ["cagliari calcio"],
    "lecce": ["us lecce"],
    "monza": ["ac monza"],
    "venezia": ["venezia fc"],
    "hellas verona": ["hellas verona fc", "verona"],
    "como": ["como 1907"],
    "parma": ["parma calcio", "parma calcio 1913"],
    "cremonese": ["us cremonese"],
    "sassuolo": ["us sassuolo", "sassuolo calcio"],
    "empoli": ["empoli fc"],
    "frosinone": ["frosinone calcio"],
    "salernitana": ["us salernitana"],
    "pisa": ["ac pisa", "sc pisa"],
}


def normalize(name: str) -> str:
    return name.lower().strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_best_match(schedina_name: str, api_matches: list) -> dict | None:
    name_norm = normalize(schedina_name)

    # Prova prima con alias diretti
    for canonical, aliases in TEAM_ALIASES.items():
        if canonical in name_norm or name_norm in canonical:
            schedina_name = canonical
            break

    best_score = 0.0
    best_match = None

    for m in api_matches:
        for team_key in ("homeTeam", "awayTeam"):
            api_name = m[team_key].get("shortName", "") or m[team_key].get("name", "")
            score = max(
                similarity(schedina_name, api_name),
                similarity(schedina_name, m[team_key].get("name", "")),
            )
            if score > best_score:
                best_score = score
                best_match = m

    return best_match if best_score >= 0.45 else None


def determine_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "1"
    elif home_goals == away_goals:
        return "X"
    else:
        return "2"


def check_prediction(mercato: str, pronostico: str, home_goals: int, away_goals: int) -> bool:
    mercato_u = mercato.upper().strip()
    prono_u = pronostico.upper().strip()
    total = home_goals + away_goals
    outcome = determine_outcome(home_goals, away_goals)
    both_scored = home_goals > 0 and away_goals > 0

    if mercato_u == "1X2":
        return outcome == prono_u

    if mercato_u in ("DC", "DOPPIA CHANCE"):
        if prono_u == "1X":
            return outcome in ("1", "X")
        if prono_u == "X2":
            return outcome in ("X", "2")
        if prono_u == "12":
            return outcome in ("1", "2")

    if "GG" in mercato_u or "NG" in mercato_u:
        if prono_u == "GG":
            return both_scored
        if prono_u == "NG":
            return not both_scored

    if "O/U" in mercato_u or "OVER" in mercato_u or "UNDER" in mercato_u:
        threshold_match = re.search(r"(\d+\.?\d*)", pronostico)
        if threshold_match:
            threshold = float(threshold_match.group(1))
            if "OVER" in prono_u:
                return total > threshold
            if "UNDER" in prono_u:
                return total < threshold

    # Mercato combinato: es. DC + Over/Under → "1X + Under (4.5)"
    if "+" in mercato_u or "+" in prono_u:
        dc_match = re.search(r"\b(1X|X2|12)\b", prono_u)
        ou_match = re.search(r"(OVER|UNDER)\s*\(\s*(\d+\.?\d*)\s*\)", prono_u)

        dc_ok = True
        ou_ok = True

        if dc_match:
            dc_pred = dc_match.group(1)
            if dc_pred == "1X":
                dc_ok = outcome in ("1", "X")
            elif dc_pred == "X2":
                dc_ok = outcome in ("X", "2")
            elif dc_pred == "12":
                dc_ok = outcome in ("1", "2")

        if ou_match:
            ou_type = ou_match.group(1)
            threshold = float(ou_match.group(2))
            ou_ok = total > threshold if ou_type == "OVER" else total < threshold

        return dc_ok and ou_ok

    return False


def get_results_for_matches(partite: list) -> list:
    dates = []
    for p in partite:
        try:
            d = datetime.strptime(p["data"], "%d/%m/%y")
            if d.year < 2024:
                # OCR ha letto male l'anno — proviamo formato YYYY
                d = datetime.strptime(p["data"], "%d/%m/%Y")
            if d.year >= 2024:
                dates.append(d)
        except Exception:
            pass

    if not dates:
        return [{"found": False, "correct": None} for _ in partite]

    date_from = min(dates).strftime("%Y-%m-%d")
    date_to = (max(dates) + timedelta(days=1)).strftime("%Y-%m-%d")

    headers = {"X-Auth-Token": os.getenv("FOOTBALL_DATA_TOKEN")}
    params = {"dateFrom": date_from, "dateTo": date_to}

    resp = requests.get(FOOTBALL_DATA_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    api_matches = resp.json().get("matches", [])

    output = []
    for partita in partite:
        match = find_best_match(partita["casa"], api_matches)
        # Verifica che la squadra trasferta corrisponda al match trovato
        if match:
            away_score = similarity(partita["trasferta"], match["awayTeam"].get("shortName", ""))
            away_score2 = similarity(partita["trasferta"], match["awayTeam"].get("name", ""))
            if max(away_score, away_score2) < 0.35:
                match = None  # Falso positivo, partita sbagliata

        if not match:
            output.append({"found": False, "correct": None})
            continue

        status = match.get("status", "")
        score = match.get("score", {}).get("fullTime", {})
        home_goals = score.get("home")
        away_goals = score.get("away")

        matchday = match.get("matchday")
        utc_date = match.get("utcDate", "")
        played_date = ""
        if utc_date:
            try:
                played_date = datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        if status != "FINISHED" or home_goals is None or away_goals is None:
            output.append({
                "found": True,
                "status": status,
                "score": "non ancora giocata",
                "correct": None,
                "matchday": matchday,
                "played_date": played_date,
            })
            continue

        correct = check_prediction(
            partita["mercato"], partita["pronostico"], int(home_goals), int(away_goals)
        )
        output.append({
            "found": True,
            "status": "FINISHED",
            "score": f"{home_goals}-{away_goals}",
            "correct": correct,
            "matchday": matchday,
            "played_date": played_date,
        })

    return output
