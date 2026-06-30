"""
2026 World Cup Prediction Engine — Event-Driven Ingestion Script
================================================================
Fetches ALL World Cup fixtures + odds in a SINGLE API call per run,
but only triggers that call when something meaningful is about to
happen or just finished.

Player images are served via statically.io CDN in index.html.
No image downloading happens here.
"""

import os
import json
import math
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from data_utils import (
    KNOWN_WC_TEAMS,
    atomic_write,
    filter_participants_map,
    format_edt_meta,
    format_utc_display,
    is_ghost_match,
    is_real_fixture,
    load_data,
    match_pair_key,
    merge_odds_fetch,
    normalize_match_id,
)

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
API_KEYS = [k for k in [
    os.environ.get("ODDSPAPI_KEY",  ""),
    os.environ.get("ODDSPAPI_KEY2", ""),
    os.environ.get("ODDSPAPI_KEY3", ""),
    os.environ.get("ODDSPAPI_KEY4", ""),
    os.environ.get("ODDSPAPI_KEY5", ""),
    os.environ.get("ODDSPAPI_KEY6", ""),
] if k]

BASE_URL        = "https://api.oddspapi.io/v4"
OUTPUT_FILE     = "data.json"
FETCH_LOG_FILE  = ".fetch_log"
REQUEST_TIMEOUT = 20
FORCE_FETCH     = os.environ.get("FORCE_FETCH", "0") == "1"

_active_key_index = 0

PRE_DAY_HOUR_UTC    = 6
QUIET_DAY_HOUR_UTC  = 8
PRE_MATCH_WINDOW    = 90
POST_DAY_WINDOW     = 60
MATCH_DURATION_MIN  = 110
COOLDOWN_MIN        = 90

SUPERCHARGER_SOURCE = "Supercharger (ELO+)"
SOURCE_SPORTSBOOKS = "Sportsbooks (Consensus)"
SOURCE_ELO = "ELO/Poisson Model"
SOURCE_PM = "Prediction Markets (P2P)"

HOST_NATIONS = {"United States", "Mexico", "Canada"}
HOST_VENUE_BONUS = 50

VENUE_HOST_COUNTRY: dict[str, str] = {
    "MetLife Stadium": "United States",
    "AT&T Stadium": "United States",
    "Levi's Stadium": "United States",
    "SoFi Stadium": "United States",
    "Rose Bowl": "United States",
    "Arrowhead Stadium": "United States",
    "Lumen Field": "United States",
    "Lincoln Financial Field": "United States",
    "BC Place": "Canada",
    "BMO Field": "Canada",
    "Stade Olympique": "Canada",
    "Estadio Azteca": "Mexico",
    "Estadio BBVA": "Mexico",
    "Estadio Akron": "Mexico",
}

# ── Schedule ───────────────────────────────────────────────────────────────

SCHEDULE = [
    "2026-06-11T23:00:00Z",
    "2026-06-12T18:00:00Z","2026-06-12T21:00:00Z","2026-06-13T00:00:00Z",
    "2026-06-13T18:00:00Z","2026-06-13T21:00:00Z","2026-06-14T00:00:00Z",
    "2026-06-14T17:00:00Z","2026-06-14T20:00:00Z","2026-06-14T23:00:00Z",
    "2026-06-15T17:00:00Z","2026-06-15T20:00:00Z","2026-06-15T23:00:00Z",
    "2026-06-16T17:00:00Z","2026-06-16T20:00:00Z","2026-06-16T23:00:00Z",
    "2026-06-17T17:00:00Z","2026-06-17T20:00:00Z","2026-06-17T23:00:00Z",
    "2026-06-18T17:00:00Z","2026-06-18T20:00:00Z","2026-06-18T23:00:00Z",
    "2026-06-19T17:00:00Z","2026-06-19T20:00:00Z","2026-06-19T23:00:00Z",
    "2026-06-20T17:00:00Z","2026-06-20T20:00:00Z","2026-06-20T23:00:00Z",
    "2026-06-21T17:00:00Z","2026-06-21T20:00:00Z","2026-06-21T23:00:00Z",
    "2026-06-22T17:00:00Z","2026-06-22T20:00:00Z","2026-06-22T23:00:00Z",
    "2026-06-23T17:00:00Z","2026-06-23T20:00:00Z","2026-06-23T23:00:00Z",
    "2026-06-24T17:00:00Z","2026-06-24T20:00:00Z","2026-06-24T23:00:00Z",
    "2026-06-25T17:00:00Z","2026-06-25T20:00:00Z","2026-06-25T23:00:00Z",
    "2026-06-26T18:00:00Z","2026-06-26T22:00:00Z",
    "2026-06-28T18:00:00Z","2026-06-28T22:00:00Z",
    "2026-06-29T18:00:00Z","2026-06-29T22:00:00Z",
    "2026-06-30T18:00:00Z","2026-06-30T22:00:00Z",
    "2026-07-01T18:00:00Z","2026-07-01T22:00:00Z",
    "2026-07-02T18:00:00Z","2026-07-02T22:00:00Z",
    "2026-07-03T18:00:00Z","2026-07-03T22:00:00Z",
    "2026-07-04T18:00:00Z","2026-07-04T22:00:00Z",
    "2026-07-05T18:00:00Z","2026-07-05T22:00:00Z",
    "2026-07-06T18:00:00Z","2026-07-06T22:00:00Z",
    "2026-07-07T18:00:00Z","2026-07-07T22:00:00Z",
    "2026-07-08T18:00:00Z","2026-07-08T22:00:00Z",
    "2026-07-09T18:00:00Z","2026-07-09T22:00:00Z",
    "2026-07-14T18:00:00Z","2026-07-14T22:00:00Z",
    "2026-07-15T18:00:00Z","2026-07-15T22:00:00Z",
    "2026-07-18T18:00:00Z",
    "2026-07-19T18:00:00Z",
    "2026-07-23T20:00:00Z",
]

# Recalibrated at runtime from completed results
CURRENT_ELO: dict[str, float] = {}


def parse_kickoffs():
    result = []
    for s in SCHEDULE:
        try:
            result.append(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            pass
    return result


ALL_KICKOFFS = parse_kickoffs()

# ── Fetch log helpers ──────────────────────────────────────────────────────

def load_fetch_log():
    try:
        with open(FETCH_LOG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_fetch_log(log_data):
    with open(FETCH_LOG_FILE, "w") as f:
        json.dump(log_data, f)


def cooldown_ok(fetch_log, key):
    ts = fetch_log.get(key)
    if ts is None:
        return True
    return (time.time() - ts) >= (COOLDOWN_MIN * 60)


def record_fetch(fetch_log, key):
    fetch_log[key] = time.time()
    save_fetch_log(fetch_log)
    cutoff = time.time() - 7 * 86400
    pruned = {k: v for k, v in fetch_log.items() if v > cutoff}
    save_fetch_log(pruned)


# ── Scheduling logic ───────────────────────────────────────────────────────

def todays_kickoffs(now):
    today = now.date()
    return [k for k in ALL_KICKOFFS if k.date() == today]


def decide_fetch(now, fetch_log):
    today_kicks = todays_kickoffs(now)
    is_match_day = len(today_kicks) > 0

    if is_match_day:
        pre_day_utc = now.replace(hour=PRE_DAY_HOUR_UTC, minute=0, second=0, microsecond=0)
        key = f"preday-{now.strftime('%Y-%m-%d')}"
        if now >= pre_day_utc and cooldown_ok(fetch_log, key):
            return True, "Pre-day odds snapshot", key

    for ko in today_kicks:
        mins_to_ko = (ko - now).total_seconds() / 60
        if 0 <= mins_to_ko <= PRE_MATCH_WINDOW:
            key = f"prematch-{ko.strftime('%Y-%m-%dT%H%M')}"
            if cooldown_ok(fetch_log, key):
                return True, f"Pre-match: {int(mins_to_ko)}min before {ko.strftime('%H:%M')} UTC", key

    if today_kicks:
        last_ko  = max(today_kicks)
        last_end = last_ko + timedelta(minutes=MATCH_DURATION_MIN)
        mins_since_end = (now - last_end).total_seconds() / 60
        if 0 <= mins_since_end <= POST_DAY_WINDOW:
            key = f"postday-{now.strftime('%Y-%m-%d')}"
            if cooldown_ok(fetch_log, key):
                return True, "Post-day results capture", key

    if not is_match_day:
        quiet_utc = now.replace(hour=QUIET_DAY_HOUR_UTC, minute=0, second=0, microsecond=0)
        key = f"quiet-{now.strftime('%Y-%m-%d')}"
        if now >= quiet_utc and cooldown_ok(fetch_log, key):
            return True, "Quiet-day morning update", key

    return False, "No trigger condition met", ""


# ── Team data ──────────────────────────────────────────────────────────────

TEAM_NAME_MAP = {
    "United States":"United States","USA":"United States","USMNT":"United States",
    "South Korea":"South Korea","Korea Republic":"South Korea","Republic of Korea":"South Korea",
    "Bosnia & Herzegovina":"Bosnia & Herzegovina","Bosnia and Herzegovina":"Bosnia & Herzegovina",
    "Bosnia":"Bosnia & Herzegovina","Czech Republic":"Czechia","Czechia":"Czechia",
    "Netherlands":"Netherlands","Holland":"Netherlands",
    "IR Iran":"Iran","Iran":"Iran","Turkiye":"Turkey","Turkey":"Turkey",
    "Congo DR":"DR Congo","DR Congo":"DR Congo",
    "Côte d'Ivoire":"Ivory Coast","Ivory Coast":"Ivory Coast",
    "Mexico":"Mexico","South Africa":"South Africa","Canada":"Canada",
    "Paraguay":"Paraguay","Germany":"Germany","Argentina":"Argentina",
    "England":"England","Italy":"Italy","France":"France","Brazil":"Brazil",
    "Spain":"Spain","Portugal":"Portugal","Morocco":"Morocco","Japan":"Japan",
    "Australia":"Australia","Croatia":"Croatia","Switzerland":"Switzerland",
    "Uruguay":"Uruguay","Colombia":"Colombia","Senegal":"Senegal",
    "Denmark":"Denmark","Ecuador":"Ecuador","Norway":"Norway","Serbia":"Serbia",
    "Poland":"Poland","Saudi Arabia":"Saudi Arabia","Ghana":"Ghana",
    "Cameroon":"Cameroon","Tunisia":"Tunisia","Egypt":"Egypt","Algeria":"Algeria",
    "Nigeria":"Nigeria","Panama":"Panama","Costa Rica":"Costa Rica",
    "Wales":"Wales","Uzbekistan":"Uzbekistan","Iraq":"Iraq","Jordan":"Jordan",
    "Qatar":"Qatar","New Zealand":"New Zealand","Cape Verde":"Cape Verde",
    "Curacao":"Curacao","Haiti":"Haiti","Belgium":"Belgium",
    "Scotland":"Scotland","Austria":"Austria","Sweden":"Sweden",
}

TEAM_META = {
    "Mexico":{"code":"MEX","flag":"mex"},"South Africa":{"code":"RSA","flag":"rsa"},
    "South Korea":{"code":"KOR","flag":"kor"},"Czechia":{"code":"CZE","flag":"cze"},
    "Canada":{"code":"CAN","flag":"can"},"Bosnia & Herzegovina":{"code":"BIH","flag":"bih"},
    "United States":{"code":"USA","flag":"usa"},"Paraguay":{"code":"PRY","flag":"pry"},
    "Germany":{"code":"GER","flag":"ger"},"Argentina":{"code":"ARG","flag":"arg"},
    "England":{"code":"ENG","flag":"eng"},"Italy":{"code":"ITA","flag":"ita"},
    "France":{"code":"FRA","flag":"fra"},"Brazil":{"code":"BRA","flag":"bra"},
    "Spain":{"code":"ESP","flag":"esp"},"Portugal":{"code":"POR","flag":"por"},
    "Netherlands":{"code":"NED","flag":"ned"},"Morocco":{"code":"MAR","flag":"mar"},
    "Japan":{"code":"JPN","flag":"jpn"},"Australia":{"code":"AUS","flag":"aus"},
    "Croatia":{"code":"CRO","flag":"cro"},"Switzerland":{"code":"SUI","flag":"sui"},
    "Uruguay":{"code":"URU","flag":"uru"},"Colombia":{"code":"COL","flag":"col"},
    "Senegal":{"code":"SEN","flag":"sen"},"Denmark":{"code":"DEN","flag":"den"},
    "Ecuador":{"code":"ECU","flag":"ecu"},"Norway":{"code":"NOR","flag":"nor"},
    "Turkey":{"code":"TUR","flag":"tur"},"Serbia":{"code":"SRB","flag":"srb"},
    "Poland":{"code":"POL","flag":"pol"},"Iran":{"code":"IRN","flag":"irn"},
    "Saudi Arabia":{"code":"KSA","flag":"ksa"},"Ghana":{"code":"GHA","flag":"gha"},
    "Cameroon":{"code":"CMR","flag":"cmr"},"Ivory Coast":{"code":"CIV","flag":"civ"},
    "Tunisia":{"code":"TUN","flag":"tun"},"Egypt":{"code":"EGY","flag":"egy"},
    "Algeria":{"code":"ALG","flag":"alg"},"Nigeria":{"code":"NGA","flag":"nga"},
    "Panama":{"code":"PAN","flag":"pan"},"Costa Rica":{"code":"CRC","flag":"crc"},
    "Wales":{"code":"WAL","flag":"wal"},"Uzbekistan":{"code":"UZB","flag":"uzb"},
    "Iraq":{"code":"IRQ","flag":"irq"},"Jordan":{"code":"JOR","flag":"jor"},
    "Qatar":{"code":"QAT","flag":"qat"},"New Zealand":{"code":"NZL","flag":"nzl"},
    "Cape Verde":{"code":"CPV","flag":"cpv"},"Curacao":{"code":"CUW","flag":"cuw"},
    "Haiti":{"code":"HAI","flag":"hai"},"Belgium":{"code":"BEL","flag":"bel"},
    "Scotland":{"code":"SCO","flag":"sco"},"DR Congo":{"code":"COD","flag":"cod"},
    "Austria":{"code":"AUT","flag":"aut"},"Sweden":{"code":"SWE","flag":"swe"},
}

BASE_ELO = {
    "Argentina":2040,"France":2005,"England":1960,"Brazil":1955,
    "Spain":1950,"Portugal":1940,"Netherlands":1935,"Germany":1930,
    "Italy":1910,"Croatia":1880,"Belgium":1875,"Uruguay":1860,
    "Colombia":1845,"United States":1845,"Mexico":1820,"Austria":1830,
    "Switzerland":1815,"Sweden":1790,"Denmark":1810,"Japan":1800,
    "Morocco":1795,"Senegal":1785,"Australia":1770,"Canada":1765,
    "South Korea":1740,"Ecuador":1730,"Norway":1725,"Turkey":1720,
    "Czechia":1715,"Scotland":1720,"Serbia":1710,"Paraguay":1705,
    "Poland":1700,"Wales":1680,"Bosnia & Herzegovina":1660,
    "Ghana":1655,"Cameroon":1650,"Ivory Coast":1640,"DR Congo":1620,
    "South Africa":1610,"Tunisia":1605,"Iran":1600,"Saudi Arabia":1595,
    "Egypt":1590,"Algeria":1585,"Nigeria":1580,"Panama":1545,
    "Costa Rica":1540,"Iraq":1530,"Jordan":1520,"Uzbekistan":1510,
    "Qatar":1500,"New Zealand":1490,"Cape Verde":1480,
    "Curacao":1460,"Haiti":1440,
}

# Backward-compatible alias used by other scripts importing ELO from fetch_odds
ELO = BASE_ELO

# ── Maths & ELO ────────────────────────────────────────────────────────────

def normalise_team(raw):
    return TEAM_NAME_MAP.get(raw, raw)


def pct(v: float | None) -> str:
    return f"{round(v * 100, 1)}%" if v is not None else "--"


def parse_pct_str(raw: Any) -> float | None:
    if raw is None or raw == "--":
        return None
    try:
        return float(str(raw).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def strip_vig(h, d, a):
    if not all(p > 1.0 for p in [h, d, a]):
        return {}
    r = [1/h, 1/d, 1/a]
    t = sum(r)
    return {"home": round(r[0]/t, 4), "draw": round(r[1]/t, 4), "away": round(r[2]/t, 4)}


def strip_vig_2way(h, a):
    if not all(p > 1.0 for p in [h, a]):
        return {}
    r = [1/h, 1/a]
    t = sum(r)
    return {"home": round(r[0]/t, 4), "draw": None, "away": round(r[1]/t, 4)}


def normalize_stage(raw: str | None) -> str:
    if not raw:
        return "Group Stage"
    s = str(raw).strip()
    low = s.lower()
    if "group" in low:
        return "Group Stage"
    if "32" in low and "16" not in low:
        return "Round of 32"
    if "16" in low and "round" in low:
        return "Round of 16"
    if "quarter" in low:
        return "Quarter-Finals"
    if "semi" in low:
        return "Semi-Finals"
    if low == "final" or low.endswith(" final"):
        return "Final"
    return s


def is_knockout_stage(stage: str | None) -> bool:
    return normalize_stage(stage) != "Group Stage"


def host_bonus_for_team(team: str, venue: str | None) -> float:
    if not venue:
        return 0.0
    host = VENUE_HOST_COUNTRY.get(str(venue).strip())
    if host and team == host:
        return float(HOST_VENUE_BONUS)
    return 0.0


def match_kickoff_dt(match: dict) -> datetime:
    raw = match.get("kickoff") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def went_to_penalties(match: dict) -> bool:
    if match.get("wentToPenalties"):
        return True
    if match.get("penaltyWinner") or match.get("penaltyNote"):
        return True
    return "penalt" in str(match.get("insight") or "").lower()


def penalty_winner_name(match: dict) -> str | None:
    winner = match.get("penaltyWinner")
    if winner:
        return str(winner)
    note = str(match.get("penaltyNote") or match.get("insight") or "")
    low = note.lower()
    if "penalt" not in low:
        return None
    home, away = match.get("home", ""), match.get("away", "")
    for team in (home, away):
        if team and team.lower() in low and ("advance" in low or "won on" in low or "won" in low):
            return team
    return None


def elo_actuals(match: dict) -> tuple[float, float] | None:
    home, away = match.get("home", ""), match.get("away", "")
    hg, ag = match.get("homeScore"), match.get("awayScore")
    if hg is None or ag is None:
        return None
    hg, ag = int(hg), int(ag)
    stage = normalize_stage(match.get("stage"))

    if stage != "Group Stage" and went_to_penalties(match):
        winner = penalty_winner_name(match)
        if winner == home:
            return 0.6, 0.4
        if winner == away:
            return 0.4, 0.6
        if hg == ag:
            return 0.5, 0.5

    if hg > ag:
        return 1.0, 0.0
    if ag > hg:
        return 0.0, 1.0
    return 0.5, 0.5


def apply_elo_update(elo: dict[str, float], home: str, away: str,
                     actual_home: float, actual_away: float, k: float) -> None:
    eh = elo.get(home, 1700.0)
    ea = elo.get(away, 1700.0)
    exp_home = 1.0 / (1.0 + math.pow(10, (ea - eh) / 400.0))
    exp_away = 1.0 - exp_home
    elo[home] = eh + k * (actual_home - exp_home)
    elo[away] = ea + k * (actual_away - exp_away)


def prior_h2h_match(home: str, away: str, kickoff: datetime,
                    prior_completed: list[dict]) -> dict | None:
    cutoff = kickoff - timedelta(days=4 * 365)
    best = None
    for pm in prior_completed:
        pk = match_kickoff_dt(pm)
        if pk >= kickoff or pk < cutoff:
            continue
        if {pm.get("home"), pm.get("away")} == {home, away}:
            if best is None or pk > match_kickoff_dt(best):
                best = pm
    return best


def recalibrate_elo(base_elo: dict[str, float], completed_matches: list[dict]) -> dict[str, float]:
    elo = dict(base_elo)
    done = [
        m for m in completed_matches
        if m.get("type") == "COMPLETED"
        and m.get("homeScore") is not None
        and m.get("awayScore") is not None
    ]
    done.sort(key=match_kickoff_dt)
    processed: list[dict] = []

    for match in done:
        home, away = match.get("home", ""), match.get("away", "")
        actuals = elo_actuals(match)
        if not home or not away or actuals is None:
            continue
        actual_home, actual_away = actuals
        stage = normalize_stage(match.get("stage"))
        base_k = 40.0 if stage == "Group Stage" else 30.0
        kickoff = match_kickoff_dt(match)

        if prior_h2h_match(home, away, kickoff, processed):
            apply_elo_update(elo, home, away, actual_home, actual_away, base_k + 15.0)
        else:
            apply_elo_update(elo, home, away, actual_home, actual_away, base_k)

        processed.append(match)

    log.info("Recalibrated ELO from %d completed match(es).", len(processed))
    return elo


def elo_probs(home, away, group_stage=True, venue=None, elo_table=None):
    table = elo_table if elo_table is not None else CURRENT_ELO
    eh = table.get(home, 1700) + host_bonus_for_team(home, venue)
    ea = table.get(away, 1700) + host_bonus_for_team(away, venue)
    ph = 1.0 / (1.0 + math.pow(10, (ea - eh) / 400.0))
    dr = max(0.15, 0.27 - abs(eh - ea) / 4000.0)
    if not group_stage:
        dr = 0.0
    if dr <= 0:
        t = ph + (1 - ph)
        return {"home": round(ph / t, 4), "draw": 0.0, "away": round((1 - ph) / t, 4)}
    t  = ph*(1-dr) + (1-ph)*(1-dr) + dr
    return {"home": round(ph*(1-dr)/t, 4), "draw": round(dr/t, 4), "away": round((1-ph)*(1-dr)/t, 4)}


def extract_1x2(bm, slug, *, allow_2way=False):
    try:
        markets = bm[slug]["markets"]
        home_price = draw_price = away_price = None
        for market in markets.values():
            outcomes = market.get("outcomes", {})
            for outcome in outcomes.values():
                players = outcome.get("players", {})
                for player in players.values():
                    bid = str(player.get("bookmakerOutcomeId", "")).lower()
                    price = player.get("price")
                    if not price or price <= 1.0:
                        continue
                    if bid == "home":
                        home_price = float(price)
                    elif bid == "draw":
                        draw_price = float(price)
                    elif bid == "away":
                        away_price = float(price)
            if home_price and away_price and (draw_price or allow_2way):
                if draw_price:
                    return strip_vig(home_price, draw_price, away_price)
                return strip_vig_2way(home_price, away_price)
        return None
    except (KeyError, TypeError, AttributeError):
        return None


# ── Supercharger+ ────────────────────────────────────────────────────────────

def layer_by_source(match: dict, source_name: str) -> dict | None:
    for layer in match.get("layers") or []:
        if layer.get("source") == source_name:
            return layer
    return None


def match_pm_layer(match: dict) -> dict | None:
    """Return the first match-level prediction-market layer (P2P or Kalshi)."""
    for layer in match.get("layers") or []:
        src = str(layer.get("source") or "")
        if src == SOURCE_PM or "Prediction Markets" in src:
            return layer
    return None


def source_fav_prob_from_pm_layer(match: dict, *, normalize: bool = True) -> float | None:
    layer = match_pm_layer(match)
    if not layer:
        return None
    return source_fav_prob_from_layer(match, layer.get("source"), normalize=normalize)


def upsert_layer(match: dict, layer: dict) -> None:
    layers = list(match.get("layers") or [])
    source = layer.get("source")
    for i, existing in enumerate(layers):
        if existing.get("source") == source:
            layers[i] = {**existing, **layer}
            match["layers"] = layers
            return
    layers.append(layer)
    match["layers"] = layers


def fav_side_probs(match: dict, home_prob: float | None, draw_prob: float | None,
                   away_prob: float | None) -> float | None:
    fav = match.get("favTeam")
    if fav is None or home_prob is None or away_prob is None:
        return None
    if fav == match.get("home"):
        return home_prob
    if fav == match.get("away"):
        return away_prob
    return None


def actual_fav_outcome_group(match: dict) -> float | None:
    fav = match.get("favTeam")
    if not fav:
        return None
    hg, ag = match.get("homeScore"), match.get("awayScore")
    if hg is None or ag is None:
        return None
    hg, ag = int(hg), int(ag)
    if hg == ag:
        return 0.5
    fav_is_home = fav == match.get("home")
    fav_won = (fav_is_home and hg > ag) or (not fav_is_home and ag > hg)
    return 1.0 if fav_won else 0.0


def actual_fav_outcome_knockout(match: dict) -> float | None:
    fav = match.get("favTeam")
    if not fav:
        return None
    hg, ag = match.get("homeScore"), match.get("awayScore")
    if hg is None or ag is None:
        return None
    home, away = match.get("home", ""), match.get("away", "")
    if normalize_stage(match.get("stage")) != "Group Stage" and went_to_penalties(match):
        winner = penalty_winner_name(match)
        if winner:
            return 1.0 if winner == fav else 0.0
    hg, ag = int(hg), int(ag)
    if hg == ag:
        return None
    fav_is_home = fav == home
    fav_won = (fav_is_home and hg > ag) or (not fav_is_home and ag > hg)
    return 1.0 if fav_won else 0.0


def source_fav_prob_from_layer(match: dict, source_name: str, *, normalize: bool = True) -> float | None:
    layer = layer_by_source(match, source_name)
    if not layer:
        return None
    home = parse_pct_str(layer.get("fav"))
    away = parse_pct_str(layer.get("und"))
    draw = parse_pct_str(layer.get("draw"))
    if home is None or away is None:
        return None
    fav = match.get("favTeam")
    if not normalize and is_knockout_stage(match.get("stage")):
        raw = home if fav == match.get("home") else away if fav == match.get("away") else None
        return (raw / 100.0) if raw is not None else None
    if is_knockout_stage(match.get("stage")):
        total = home + away
        if total <= 0:
            return None
        home /= total
        away /= total
    elif draw is None:
        total = home + away
        if total <= 0:
            return None
        home /= total
        away /= total
    else:
        draw = draw or 0.0
        total = home + away + draw
        if total <= 0:
            return None
        home /= total
        away /= total
    fav = match.get("favTeam")
    if fav == match.get("home"):
        return home
    if fav == match.get("away"):
        return away
    return None


def tournament_proxy_probs(match: dict) -> tuple[float | None, float | None, str | None]:
    home, away = match.get("home", ""), match.get("away", "")

    def tour_prob(team: str) -> float | None:
        if team == home:
            return parse_pct_str(match.get("kalshiWinProbHome")) or parse_pct_str(match.get("polymarketWinProbHome"))
        if team == away:
            return parse_pct_str(match.get("kalshiWinProbAway")) or parse_pct_str(match.get("polymarketWinProbAway"))
        return None

    th = tour_prob(home)
    ta = tour_prob(away)
    if th is None or ta is None or th + ta <= 0:
        return None, None, None
    implied = (th / 100.0) / ((th + ta) / 100.0)
    dampened = 0.5 + 0.6 * (implied - 0.5)
    home_p = dampened
    away_p = 1.0 - dampened
    note = "Prediction markets: implied from tournament winner odds (dampened proxy)."
    return home_p, away_p, note


def prediction_market_fav_prob(match: dict) -> tuple[float | None, str | None, bool]:
    """Match-level PM layer first; kalshiWinProb* fields are tournament-winner odds only."""
    layer = match_pm_layer(match)
    if layer:
        prob = source_fav_prob_from_layer(match, layer.get("source"), normalize=False)
        if prob is not None:
            src = layer.get("source", SOURCE_PM)
            return prob, f"Prediction markets: {src} match odds.", True
    home_p, away_p, note = tournament_proxy_probs(match)
    if home_p is not None:
        fav = match.get("favTeam")
        if fav == match.get("home"):
            return home_p, note, False
        if fav == match.get("away"):
            return away_p, note, False
    return None, None, False


def track_brier(completed: list[dict], source_name: str, *, knockout: bool) -> float | None:
    errors: list[float] = []
    for match in completed:
        if knockout != is_knockout_stage(match.get("stage")):
            continue
        if source_name == SOURCE_PM:
            pred = source_fav_prob_from_pm_layer(match)
            if pred is None:
                pm_pred, _, _ = prediction_market_fav_prob(match)
                pred = pm_pred
        else:
            pred = source_fav_prob_from_layer(match, source_name)
        if pred is None:
            continue
        actual = actual_fav_outcome_knockout(match) if knockout else actual_fav_outcome_group(match)
        if actual is None:
            continue
        errors.append((pred - actual) ** 2)
    if not errors:
        return None
    return sum(errors) / len(errors)


def group_stage_has_upcoming(all_matches: list[dict]) -> bool:
    return any(
        normalize_stage(m.get("stage")) == "Group Stage" and m.get("type") == "UPCOMING"
        for m in all_matches
    )


def calculate_supercharger(completed_matches: list[dict], match: dict,
                           all_matches: list[dict] | None = None) -> dict:
    all_matches = all_matches or completed_matches
    stage = normalize_stage(match.get("stage"))
    knockout_match = is_knockout_stage(stage)
    completed = [
        m for m in completed_matches
        if m.get("type") == "COMPLETED" and m.get("favTeam")
    ]
    group_completed = [m for m in completed if normalize_stage(m.get("stage")) == "Group Stage"]
    knockout_completed = [m for m in completed if is_knockout_stage(m.get("stage"))]

    use_knockout_track = knockout_match and len(knockout_completed) > 0
    if knockout_match and not use_knockout_track:
        use_knockout_track = False

    track_pool = knockout_completed if use_knockout_track else group_completed
    if not track_pool:
        return {
            "source": SUPERCHARGER_SOURCE,
            "fav": "--", "draw": "--", "und": "--",
            "tooltip": "Awaiting completed matches to calibrate Supercharger+.",
        }

    sources: list[tuple[str, float | None, float]] = []
    for name in (SOURCE_SPORTSBOOKS, SOURCE_ELO):
        brier = track_brier(track_pool, name, knockout=use_knockout_track)
        if brier is None or brier <= 0:
            continue
        sources.append((name, brier, 1.0))

    pm_pred, pm_note, pm_direct = prediction_market_fav_prob(match)
    pm_brier = track_brier(track_pool, SOURCE_PM, knockout=use_knockout_track)
    pm_weight_scale = 1.0 if pm_direct else 0.5
    if pm_pred is not None and pm_brier is not None and pm_brier > 0:
        sources.append((SOURCE_PM, pm_brier, pm_weight_scale))
    elif pm_pred is not None:
        sources.append((SOURCE_PM, 0.25, pm_weight_scale))

    if not sources:
        return {
            "source": SUPERCHARGER_SOURCE,
            "fav": "--", "draw": "--", "und": "--",
            "tooltip": "Insufficient source accuracy history for Supercharger+.",
        }

    weights = {name: (scale / brier) for name, brier, scale in sources}
    total_w = sum(weights.values())
    if total_w <= 0:
        return {"source": SUPERCHARGER_SOURCE, "fav": "--", "draw": "--", "und": "--"}

    fav = match.get("favTeam")
    if not fav:
        return {"source": SUPERCHARGER_SOURCE, "fav": "--", "draw": "--", "und": "--"}

    blended_fav = 0.0
    blended_draw = 0.0
    used_weight = 0.0
    for name, _, _ in sources:
        w = weights[name] / total_w
        if name == SOURCE_PM:
            prob, _, _ = prediction_market_fav_prob(match)
            layer = layer_by_source(match, SOURCE_PM)
            draw_p = parse_pct_str(layer.get("draw")) if layer else None
            if draw_p is not None:
                draw_p /= 100.0
        else:
            layer = layer_by_source(match, name)
            if not layer:
                continue
            hp = parse_pct_str(layer.get("fav"))
            ap = parse_pct_str(layer.get("und"))
            dp = parse_pct_str(layer.get("draw"))
            if hp is None or ap is None:
                continue
            if knockout_match:
                prob = (hp if fav == match.get("home") else ap) / 100.0
                draw_p = None
            else:
                total = hp + ap + (dp or 0.0)
                if total <= 0:
                    continue
                prob = (hp if fav == match.get("home") else ap) / total
                draw_p = (dp / total) if dp is not None else None
        if prob is None:
            continue
        blended_fav += w * prob
        if not knockout_match and draw_p is not None:
            blended_draw += w * draw_p
        used_weight += w

    if used_weight <= 0 or blended_fav <= 0:
        return {"source": SUPERCHARGER_SOURCE, "fav": "--", "draw": "--", "und": "--"}

    fav_prob = min(max(blended_fav / used_weight, 0.01), 0.98)

    source_probs: list[float] = []
    for name, _, _ in sources:
        if name == SOURCE_PM:
            p, _, _ = prediction_market_fav_prob(match)
        else:
            p = source_fav_prob_from_layer(match, name, normalize=False)
        if p is not None:
            source_probs.append(p)
    if source_probs:
        lo, hi = min(source_probs), max(source_probs)
        if fav_prob < lo - 0.001 or fav_prob > hi + 0.001:
            log.warning(
                "Supercharger+ out of bounds for %s vs %s: %.3f not in [%.3f, %.3f] (inputs=%s)",
                match.get("home"), match.get("away"), fav_prob, lo, hi,
                [round(p, 3) for p in source_probs],
            )
            fav_prob = max(lo, min(hi, fav_prob))

    if knockout_match:
        und_prob = 1.0 - fav_prob
        draw_out = "--"
    else:
        draw_prob = min(max(blended_draw / used_weight, 0.01), 0.90) if blended_draw > 0 else 0.25
        remaining = max(0.01, 1.0 - fav_prob - draw_prob)
        total = fav_prob + draw_prob + remaining
        fav_prob /= total
        draw_prob /= total
        und_prob = remaining / total
        draw_out = pct(draw_prob)

    tooltip_parts = [
        "Supercharger+ blends source win probabilities weighted by inverse Brier score.",
        f"Track: {'knockout (2-way)' if use_knockout_track else 'group stage (3-way)'}.",
    ]
    if pm_note:
        tooltip_parts.append(pm_note)
    elif pm_pred is not None and not pm_direct:
        tooltip_parts.append("Prediction markets weighted at 50% (tournament winner proxy).")

    # Layers store home win % in "fav" and away win % in "und" (same as ELO/sportsbooks).
    home_team = match.get("home")
    if fav == home_team:
        home_prob, away_prob = fav_prob, und_prob
    else:
        home_prob, away_prob = und_prob, fav_prob

    return {
        "source": SUPERCHARGER_SOURCE,
        "fav": pct(home_prob),
        "draw": draw_out,
        "und": pct(away_prob),
        "tooltip": " ".join(tooltip_parts),
    }


def refresh_model_layers(match: dict, venue: str | None = None) -> None:
    home, away = match.get("home", ""), match.get("away", "")
    if not home or not away:
        return
    stage = normalize_stage(match.get("stage"))
    is_group = stage == "Group Stage"
    ep = elo_probs(home, away, group_stage=is_group, venue=venue or match.get("venue"))

    draw_val = pct(ep["draw"]) if is_group else "--"
    upsert_layer(match, {
        "source": SOURCE_ELO,
        "fav": pct(ep["home"]),
        "draw": draw_val,
        "und": pct(ep["away"]),
    })


def apply_supercharger_layer(match: dict, completed_matches: list[dict],
                             all_matches: list[dict]) -> None:
    layer = calculate_supercharger(completed_matches, match, all_matches)
    upsert_layer(match, layer)


def ensure_fav_team(match: dict) -> None:
    if match.get("favTeam"):
        return
    layer = layer_by_source(match, SOURCE_SPORTSBOOKS)
    if layer:
        hp = parse_pct_str(layer.get("fav"))
        ap = parse_pct_str(layer.get("und"))
        if hp is not None and ap is not None and hp != ap:
            match["favTeam"] = match["home"] if hp > ap else match["away"]
            return
    layer = layer_by_source(match, SOURCE_ELO)
    if layer:
        hp = parse_pct_str(layer.get("fav"))
        ap = parse_pct_str(layer.get("und"))
        if hp is not None and ap is not None and hp != ap:
            match["favTeam"] = match["home"] if hp > ap else match["away"]


# ── API ────────────────────────────────────────────────────────────────────

def api_get(path, params=None):
    global _active_key_index
    if not API_KEYS:
        log.error("No ODDSPAPI keys set.")
        return None
    url = f"{BASE_URL}/{path}"
    for attempt in range(len(API_KEYS)):
        key_idx = (_active_key_index + attempt) % len(API_KEYS)
        key     = API_KEYS[key_idx]
        p       = {"apiKey": key}
        if params: p.update(params)
        try:
            resp = requests.get(url, params=p, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                log.warning("Key %d rate limited.", key_idx + 1)
                _active_key_index = (key_idx + 1) % len(API_KEYS)
                time.sleep(2); continue
            if resp.status_code == 403:
                log.warning("Key %d quota exhausted.", key_idx + 1)
                _active_key_index = (key_idx + 1) % len(API_KEYS)
                continue
            resp.raise_for_status()
            if attempt > 0:
                _active_key_index = key_idx
            return resp.json()
        except requests.exceptions.HTTPError as e:
            log.error("HTTP %s for %s", e.response.status_code, url)
        except requests.exceptions.Timeout:
            log.error("Timeout: %s", url)
        except requests.exceptions.RequestException as e:
            log.error("Request failed: %s", e)
        except ValueError:
            log.error("Non-JSON response from %s", url)
    log.error("All API keys failed for %s.", url)
    return None


WC_TOURNAMENT_ID = "16"


def fetch_all_odds():
    log.info("Fetching World Cup odds (tournamentId=%s)...", WC_TOURNAMENT_ID)
    for bookmaker in ["pinnacle", "bet365", "draftkings", "fanduel"]:
        params = {"tournamentIds": WC_TOURNAMENT_ID, "bookmaker": bookmaker}
        log.info("  Trying bookmaker=%s...", bookmaker)
        data = api_get("odds-by-tournaments", params)
        if not data:
            continue
        fixtures = data if isinstance(data, list) else (
            data.get("data") or data.get("fixtures") or data.get("matches") or None)
        if not fixtures:
            continue
        real = [f for f in fixtures if isinstance(f, dict) and is_real_fixture(f)]
        log.info("  Success via %s: %d real fixtures.", bookmaker, len(real))
        return real
    log.error("All bookmaker attempts failed.")
    return None


def fetch_participants_map():
    fetch_log = load_fetch_log()
    cached = fetch_log.get("participants_map")
    if cached and isinstance(cached, dict) and len(cached) > 10:
        return filter_participants_map(
            {str(k): normalise_team(v) for k, v in cached.items()}
        )
    log.info("Fetching all soccer participants...")
    data = api_get("participants", {"sportId": 10, "language": "en"})
    if not data or not isinstance(data, dict):
        return {}
    result = filter_participants_map(
        {str(k): normalise_team(str(v)) for k, v in data.items()}
    )
    fetch_log["participants_map"] = result
    save_fetch_log(fetch_log)
    return result


# ── Record builder ─────────────────────────────────────────────────────────

def build_record(fixture, pmap=None, venue_by_pair=None):
    raw_home = (fixture.get("participant1Name") or
                (pmap or {}).get(str(fixture.get("participant1Id", ""))) or "")
    raw_away = (fixture.get("participant2Name") or
                (pmap or {}).get(str(fixture.get("participant2Id", ""))) or "")
    home = normalise_team(raw_home)
    away = normalise_team(raw_away)
    if not home or not away:
        return None
    if home not in KNOWN_WC_TEAMS or away not in KNOWN_WC_TEAMS:
        return None
    if is_ghost_match({"home": home, "away": away}):
        return None

    fid     = normalize_match_id(fixture.get("fixtureId") or fixture.get("id") or "")
    kickoff = fixture.get("startTime") or fixture.get("date") or ""
    group   = fixture.get("roundName") or fixture.get("group") or "Group Stage"
    stage   = normalize_stage(group)
    is_group = stage == "Group Stage"
    venue = (venue_by_pair or {}).get(match_pair_key({"home": home, "away": away}))

    hm = TEAM_META.get(home, {"code": home[:3].upper(), "flag": home[:3].lower()})
    am = TEAM_META.get(away, {"code": away[:3].upper(), "flag": away[:3].lower()})
    bm = fixture.get("bookmakerOdds") or fixture.get("odds") or {}

    def collect_layers(slugs):
        probs = []
        for s in slugs:
            px = extract_1x2(bm, s, allow_2way=not is_group)
            if px:
                probs.append(px)
        return probs

    sb = collect_layers(["pinnacle", "bet365", "draftkings", "fanduel", "sbobet"])
    pm = collect_layers(["polymarket", "kalshi"])
    ep = elo_probs(home, away, group_stage=is_group, venue=venue)

    def avg(lst, k):
        vals = [x[k] for x in lst if x.get(k) is not None]
        return sum(vals) / len(vals) if vals else None

    layers = []
    if sb:
        draw_avg = avg(sb, "draw")
        layers.append({
            "source": SOURCE_SPORTSBOOKS,
            "fav": pct(avg(sb, "home")),
            "draw": pct(draw_avg) if is_group and draw_avg is not None else "--",
            "und": pct(avg(sb, "away")),
        })
    layers.append({
        "source": SOURCE_ELO,
        "fav": pct(ep["home"]),
        "draw": pct(ep["draw"]) if is_group else "--",
        "und": pct(ep["away"]),
    })
    if pm:
        draw_avg = avg(pm, "draw")
        layers.append({
            "source": SOURCE_PM,
            "fav": pct(avg(pm, "home")),
            "draw": pct(draw_avg) if is_group and draw_avg is not None else "--",
            "und": pct(avg(pm, "away")),
        })

    fav_team = None
    if sb:
        fav_team = home if avg(sb, "home") >= avg(sb, "away") else away

    try:
        dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        meta_str = format_edt_meta(dt, str(group))
    except Exception:
        meta_str = f"{kickoff} · {group}" if kickoff else str(group)

    return {
        "id": fid, "stage": stage, "group": str(group),
        "side": "left", "type": "UPCOMING",
        "home": home, "away": away, "favTeam": fav_team,
        "homeFlag": hm["flag"], "awayFlag": am["flag"],
        "kickoff": kickoff, "meta": meta_str, "layers": layers,
        "venue": venue,
    }


def preserve_existing_metadata(existing_by_pair: dict[str, dict], matches: list[dict]) -> None:
    preserve = (
        "stage", "group", "importedFrom", "penaltyNote", "meta", "venue",
        "kalshiWinProbHome", "kalshiWinProbAway",
        "polymarketWinProbHome", "polymarketWinProbAway",
        "homeMomentumFactor", "awayMomentumFactor",
    )
    for match in matches:
        old = existing_by_pair.get(match_pair_key(match))
        if not old:
            continue
        for field in preserve:
            if old.get(field) is not None and (
                match.get(field) is None or field in ("stage", "group", "importedFrom", "venue")
            ):
                match[field] = old[field]
        if old.get("importedFrom") == "ESPN" and old.get("stage"):
            match["stage"] = old["stage"]
            match["group"] = old.get("group") or old["stage"]


def finalize_predictions(matches: list[dict], completed: list[dict]) -> None:
    venue_by_pair = {
        match_pair_key(m): m.get("venue")
        for m in matches if m.get("venue")
    }
    for match in matches:
        if match.get("type") not in ("UPCOMING", "IN_PLAY"):
            continue
        venue = match.get("venue") or venue_by_pair.get(match_pair_key(match))
        refresh_model_layers(match, venue)
        ensure_fav_team(match)
        apply_supercharger_layer(match, completed, matches)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    log.info("=== World Cup ingestion starting ===")

    if not API_KEYS:
        log.error("No ODDSPAPI keys set.")
        raise SystemExit(1)

    now       = datetime.now(timezone.utc)
    fetch_log = load_fetch_log()

    if FORCE_FETCH:
        should, reason, wkey = True, "FORCE_FETCH override", f"force-{now.strftime('%Y%m%d%H%M')}"
    else:
        should, reason, wkey = decide_fetch(now, fetch_log)

    log.info("Decision: %s — %s", "FETCH" if should else "SKIP", reason)

    if not should:
        raise SystemExit(0)

    kicks = todays_kickoffs(now)
    if kicks:
        log.info("Today's matches: %s", [k.strftime("%H:%M") for k in sorted(kicks)])

    existing = load_data(OUTPUT_FILE)
    existing_matches = existing.get("matches") or []
    existing_by_pair = {match_pair_key(m): m for m in existing_matches}
    completed = [m for m in existing_matches if m.get("type") == "COMPLETED"]
    completed_count = len(completed)
    log.info("Loaded %d match(es) (%d completed).", len(existing_matches), completed_count)

    global CURRENT_ELO
    CURRENT_ELO = recalibrate_elo(BASE_ELO, existing_matches)

    venue_by_pair = {
        match_pair_key(m): m.get("venue")
        for m in existing_matches if m.get("venue")
    }

    fixtures = fetch_all_odds()
    if not fixtures:
        log.warning("No fixtures returned — keeping existing data.")
        raise SystemExit(0)

    record_fetch(fetch_log, wkey)
    pmap = fetch_participants_map()

    upcoming = []
    for i, fx in enumerate(fixtures):
        if not is_real_fixture(fx, pmap, normalise_team):
            continue
        rec = build_record(fx, pmap, venue_by_pair)
        if rec and not is_ghost_match(rec):
            upcoming.append(rec)
            has_sb = any(l.get("source") == SOURCE_SPORTSBOOKS for l in rec.get("layers", []))
            log.info("  [%d] %s vs %s (%s) — %d layer(s)%s", i + 1,
                     rec["home"], rec["away"], rec.get("stage", "?"),
                     len(rec["layers"]),
                     "" if has_sb else " [no sportsbook — 2-way only?]")

    log.info("Built %d upcoming records from API.", len(upcoming))

    matches = merge_odds_fetch(existing, upcoming)
    preserve_existing_metadata(existing_by_pair, matches)
    finalize_predictions(matches, completed)

    output = {
        "currentStage": existing.get("currentStage", "Group Stage"),
        "lastUpdated": format_utc_display(now),
        "matches": matches,
    }
    if existing.get("groupStageProgress"):
        output["groupStageProgress"] = existing["groupStageProgress"]
    atomic_write(OUTPUT_FILE, output)
    log.info("=== Done. %d total matches ===", len(output["matches"]))


if __name__ == "__main__":
    main()
