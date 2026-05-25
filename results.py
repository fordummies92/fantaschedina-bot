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

_api_cache: dict[str, tuple[float, list]] = {}
_API_CACHE_TTL = 300  # secondi

THESPORTSDB_URL = "https://www.thesportsdb.com/api/v1/json/3/eventsround.php"
THESPORTSDB_LEAGUE_ID = 4332       # Italian Serie A
THESPORTSDB_SEASON = "2025-2026"
SERIE_A_SEASON_START = datetime(2025, 8, 24)  # inizio stagione 2025/26

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

    # Mercato combinato (es. "DC + Over/Under" → "1X + UNDER (4.5)") va valutato
    # prima dei check singoli, altrimenti il ramo O/U fa match sul mercato
    # "DC + OVER/UNDER" e pesca la cifra sbagliata come soglia.
    if "+" in mercato_u or "+" in prono_u:
        dc_match = re.search(r"\b(1X|X2|12)\b", prono_u)
        ou_match = re.search(r"(OVER|UNDER)\s*\(\s*(\d+[.,]?\d*)\s*\)", prono_u)

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
            threshold = float(ou_match.group(2).replace(",", "."))
            ou_ok = total > threshold if ou_type == "OVER" else total < threshold

        result = dc_ok and ou_ok
        logger.info(
            "[check] combined '%s' / '%s' score=%d-%d -> dc_ok=%s ou_ok=%s => %s",
            mercato, pronostico, home_goals, away_goals, dc_ok, ou_ok, result,
        )
        return result

    if mercato_u == "1X2":
        # Doppia chance anche su mercato 1X2 (es. pronostico "X2", "1X", "12")
        if prono_u == "1X":
            return outcome in ("1", "X")
        if prono_u == "X2":
            return outcome in ("X", "2")
        if prono_u == "12":
            return outcome in ("1", "2")
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
        threshold_match = re.search(r"(\d+[.,]?\d*)", pronostico)
        if threshold_match:
            threshold = float(threshold_match.group(1).replace(",", "."))
            if "OVER" in prono_u:
                return total > threshold
            if "UNDER" in prono_u:
                return total < threshold

    logger.warning(
        "[check] unhandled mercato='%s' pronostico='%s'", mercato, pronostico,
    )
    return False


def _estimate_round(match_date: datetime) -> int:
    """Stima la giornata Serie A dalla data della partita."""
    days = (match_date - SERIE_A_SEASON_START).days
    return max(1, min(38, round(days / 7)))


def _fetch_round(round_num: int) -> list:
    """Scarica le partite di una giornata da TheSportsDB."""
    resp = requests.get(
        THESPORTSDB_URL,
        params={"id": THESPORTSDB_LEAGUE_ID, "r": round_num, "s": THESPORTSDB_SEASON},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("events") or []


def _normalize_sportsdb(e: dict, round_num: int) -> dict:
    """Converte un evento TheSportsDB nel formato interno."""
    date_str = e.get("dateEvent", "")
    time_str = e.get("strTime", "00:00:00") or "00:00:00"
    utc_date = ""
    try:
        dt = datetime.strptime(f"{date_str} {time_str[:5]}", "%Y-%m-%d %H:%M")
        utc_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    status_raw = e.get("strStatus", "")
    status = "FINISHED" if status_raw in ("Match Finished", "FT", "AET", "PEN", "AP") else status_raw

    home_score = e.get("intHomeScore")
    away_score = e.get("intAwayScore")
    try:
        home_score = int(home_score) if home_score is not None else None
        away_score = int(away_score) if away_score is not None else None
    except Exception:
        home_score = away_score = None

    return {
        "utcDate": utc_date,
        "status": status,
        "matchday": round_num,
        "homeTeam": {
            "name": e.get("strHomeTeam", ""),
            "shortName": e.get("strHomeTeam", ""),
        },
        "awayTeam": {
            "name": e.get("strAwayTeam", ""),
            "shortName": e.get("strAwayTeam", ""),
        },
        "score": {"fullTime": {"home": home_score, "away": away_score}},
    }


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

    cache_key = f"{date_from}_{date_to}"
    cached = _api_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _API_CACHE_TTL:
        api_matches = cached[1]
        logger.info(
            "[results] cache HIT (matches=%d, range=%s→%s, age=%.0fs)",
            len(api_matches), date_from, date_to, time.time() - cached[0],
        )
    else:
        t_api = time.perf_counter()
        estimated_round = _estimate_round(min(dates))
        date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")

        all_events = []
        for delta in (-1, 0, 1):
            r = estimated_round + delta
            if r < 1 or r > 38:
                continue
            all_events.extend(_fetch_round(r))

        # Filtra per data nella finestra della schedina (±1 giorno)
        api_matches = []
        for e in all_events:
            try:
                e_date = datetime.strptime(e.get("dateEvent", ""), "%Y-%m-%d")
                if date_from_dt - timedelta(days=1) <= e_date <= date_to_dt + timedelta(days=1):
                    round_num = int(e.get("intRound") or estimated_round)
                    api_matches.append(_normalize_sportsdb(e, round_num))
            except Exception:
                pass

        _api_cache[cache_key] = (time.time(), api_matches)
        logger.info(
            "[results] thesportsdb: %.2fs (matches=%d, round~%d, range=%s→%s)",
            time.perf_counter() - t_api,
            len(api_matches),
            estimated_round,
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
