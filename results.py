import logging
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROME_TZ = ZoneInfo("Europe/Rome")

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


def find_best_match(home_name: str, away_name: str, match_date: datetime, api_matches: list) -> dict | None:
    # Alias squadra casa
    home_norm = normalize(home_name)
    for canonical, aliases in TEAM_ALIASES.items():
        if canonical in home_norm or home_norm in canonical:
            home_name = canonical
            break

    # Filtra solo le partite giocate nella stessa data (±1 giorno per fuso orario)
    date_filtered = []
    for m in api_matches:
        utc_date = m.get("utcDate", "")
        try:
            api_date = datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ")
            if abs((api_date.date() - match_date.date()).days) <= 1:
                date_filtered.append(m)
        except Exception:
            pass

    candidates = date_filtered if date_filtered else api_matches

    best_score = 0.0
    best_match = None

    for m in candidates:
        api_home = m["homeTeam"].get("shortName", "") or m["homeTeam"].get("name", "")
        api_away = m["awayTeam"].get("shortName", "") or m["awayTeam"].get("name", "")

        home_score = max(
            similarity(home_name, api_home),
            similarity(home_name, m["homeTeam"].get("name", "")),
        )
        away_score = max(
            similarity(away_name, api_away),
            similarity(away_name, m["awayTeam"].get("name", "")),
        )
        combined = (home_score + away_score) / 2

        if combined > best_score:
            best_score = combined
            best_match = m

    if best_match and best_score >= 0.45:
        picked_home = best_match["homeTeam"].get("name", "?")
        picked_away = best_match["awayTeam"].get("name", "?")
        picked_utc = best_match.get("utcDate", "?")
        picked_status = best_match.get("status", "?")
        picked_score = best_match.get("score", {}).get("fullTime", {})
        logger.info(
            "[match] '%s - %s' [%s] -> '%s - %s' @ %s status=%s score=%s-%s sim=%.2f",
            home_name,
            away_name,
            match_date.strftime("%d/%m/%y"),
            picked_home,
            picked_away,
            picked_utc,
            picked_status,
            picked_score.get("home"),
            picked_score.get("away"),
            best_score,
        )
        return best_match
    logger.warning(
        "[match] NO MATCH for '%s - %s' [%s] (best_sim=%.2f, candidates=%d)",
        home_name,
        away_name,
        match_date.strftime("%d/%m/%y"),
        best_score,
        len(candidates),
    )
    return None


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

    t_api = time.perf_counter()
    resp = requests.get(FOOTBALL_DATA_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    api_matches = resp.json().get("matches", [])
    logger.info(
        "[results] football-data API: %.2fs (matches=%d, range=%s→%s)",
        time.perf_counter() - t_api,
        len(api_matches),
        date_from,
        date_to,
    )

    t_match = time.perf_counter()
    output = []
    for partita in partite:
        try:
            match_date = datetime.strptime(partita["data"], "%d/%m/%y")
            if match_date.year < 2024:
                match_date = datetime.strptime(partita["data"], "%d/%m/%Y")
        except Exception:
            match_date = datetime.now()

        match = find_best_match(partita["casa"], partita["trasferta"], match_date, api_matches)

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
                dt_utc = datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                played_date = dt_utc.astimezone(ROME_TZ).strftime("%d/%m/%Y %H:%M")
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

    matched = sum(1 for o in output if o.get("found"))
    logger.info(
        "[results] matching: %.3fs (%d/%d matched)",
        time.perf_counter() - t_match,
        matched,
        len(output),
    )
    return output
