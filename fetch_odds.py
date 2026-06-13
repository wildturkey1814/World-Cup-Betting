"""
2026 World Cup Prediction Engine — Event-Driven Ingestion Script
================================================================
Fetches ALL World Cup fixtures + odds in a SINGLE API call per run,
but only triggers that call when something meaningful is about to
happen or just finished.

Fetch triggers (checked in order, first match wins):
  1. Pre-day      — 06:00 UTC on any match day (opening odds snapshot)
  2. Pre-match    — within 90 min before any match kickoff
  3. Post-day     — within 60 min after the last match of the day ends
  4. Quiet-day    — once at 08:00 UTC on days with no matches

Cooldown: once a trigger fires, a 90-min lockout prevents re-fetching
for the same window (avoids double-triggers when the action runs every
30 min).

Run manually:        python fetch_odds.py
Force immediate run: FORCE_FETCH=1 python fetch_odds.py
Requirements:        pip install requests
Environment:         ODDSPAPI_KEY must be set
"""

import os
import json
import math
import time
import logging
import tempfile
import shutil
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import requests

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
API_KEY         = os.environ.get("ODDSPAPI_KEY", "")
BASE_URL        = "https://api.oddspapi.io/v4"
OUTPUT_FILE     = "data.json"
FETCH_LOG_FILE  = ".fetch_log"   # JSON log: {window_key: unix_timestamp}
REQUEST_TIMEOUT = 20
FORCE_FETCH     = os.environ.get("FORCE_FETCH", "0") == "1"

# Trigger windows
PRE_DAY_HOUR_UTC    = 6           # fetch at 06:00 UTC on match days
QUIET_DAY_HOUR_UTC  = 8           # fetch at 08:00 UTC on quiet days
PRE_MATCH_WINDOW    = 90          # minutes before kickoff
POST_DAY_WINDOW     = 60          # minutes after last match ends
MATCH_DURATION_MIN  = 110         # assumed match length incl. stoppage time
COOLDOWN_MIN        = 90          # minimum gap between fetches for same window

BOOKMAKERS = "pinnacle,bet365,draftkings,fanduel,polymarket,kalshi"
MARKET_1X2, OUTCOME_HOME, OUTCOME_DRAW, OUTCOME_AWAY = "101","101","102","103"

# ── 2026 World Cup match schedule ──────────────────────────────────────────
# ISO 8601 UTC kickoff times for all 104 matches.
# Used for event-driven scheduling independent of data.json.
# (Group stage times sourced from FIFA schedule; knockout TBD slots
#  use placeholder times that get overwritten once fixtures are known.)

SCHEDULE = [
    # ── Group Stage ────────────────────────────────────────────────────
    # June 11
    "2026-06-11T23:00:00Z",
    # June 12
    "2026-06-12T18:00:00Z","2026-06-12T21:00:00Z","2026-06-13T00:00:00Z",
    # June 13
    "2026-06-13T18:00:00Z","2026-06-13T21:00:00Z","2026-06-14T00:00:00Z",
    # June 14
    "2026-06-14T17:00:00Z","2026-06-14T20:00:00Z","2026-06-14T23:00:00Z",
    # June 15
    "2026-06-15T17:00:00Z","2026-06-15T20:00:00Z","2026-06-15T23:00:00Z",
    # June 16
    "2026-06-16T17:00:00Z","2026-06-16T20:00:00Z","2026-06-16T23:00:00Z",
    # June 17
    "2026-06-17T17:00:00Z","2026-06-17T20:00:00Z","2026-06-17T23:00:00Z",
    # June 18
    "2026-06-18T17:00:00Z","2026-06-18T20:00:00Z","2026-06-18T23:00:00Z",
    # June 19
    "2026-06-19T17:00:00Z","2026-06-19T20:00:00Z","2026-06-19T23:00:00Z",
    # June 20
    "2026-06-20T17:00:00Z","2026-06-20T20:00:00Z","2026-06-20T23:00:00Z",
    # June 21
    "2026-06-21T17:00:00Z","2026-06-21T20:00:00Z","2026-06-21T23:00:00Z",
    # June 22
    "2026-06-22T17:00:00Z","2026-06-22T20:00:00Z","2026-06-22T23:00:00Z",
    # June 23
    "2026-06-23T17:00:00Z","2026-06-23T20:00:00Z","2026-06-23T23:00:00Z",
    # June 24
    "2026-06-24T17:00:00Z","2026-06-24T20:00:00Z","2026-06-24T23:00:00Z",
    # June 25
    "2026-06-25T17:00:00Z","2026-06-25T20:00:00Z","2026-06-25T23:00:00Z",
    # June 26 (final group stage matchday — groups play simultaneously)
    "2026-06-26T18:00:00Z","2026-06-26T18:00:00Z",
    "2026-06-26T22:00:00Z","2026-06-26T22:00:00Z",
    # ── Round of 32 ────────────────────────────────────────────────────
    "2026-06-28T18:00:00Z","2026-06-28T22:00:00Z",
    "2026-06-29T18:00:00Z","2026-06-29T22:00:00Z",
    "2026-06-30T18:00:00Z","2026-06-30T22:00:00Z",
    "2026-07-01T18:00:00Z","2026-07-01T22:00:00Z",
    "2026-07-02T18:00:00Z","2026-07-02T22:00:00Z",
    "2026-07-03T18:00:00Z","2026-07-03T22:00:00Z",
    "2026-07-04T18:00:00Z","2026-07-04T22:00:00Z",
    "2026-07-05T18:00:00Z","2026-07-05T22:00:00Z",
    # ── Round of 16 ────────────────────────────────────────────────────
    "2026-07-06T18:00:00Z","2026-07-06T22:00:00Z",
    "2026-07-07T18:00:00Z","2026-07-07T22:00:00Z",
    "2026-07-08T18:00:00Z","2026-07-08T22:00:00Z",
    "2026-07-09T18:00:00Z","2026-07-09T22:00:00Z",
    # ── Quarter-Finals ─────────────────────────────────────────────────
    "2026-07-14T18:00:00Z","2026-07-14T22:00:00Z",
    "2026-07-15T18:00:00Z","2026-07-15T22:00:00Z",
    # ── Semi-Finals ────────────────────────────────────────────────────
    "2026-07-18T18:00:00Z",
    "2026-07-19T18:00:00Z",
    # ── Final ──────────────────────────────────────────────────────────
    "2026-07-23T20:00:00Z",
]

def parse_kickoffs() -> list:
    """Return all kickoff datetimes as UTC-aware datetime objects."""
    result = []
    for s in SCHEDULE:
        try:
            result.append(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            pass
    return result

ALL_KICKOFFS = parse_kickoffs()

# ── Fetch log helpers ──────────────────────────────────────────────────────

def load_fetch_log() -> dict:
    try:
        with open(FETCH_LOG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_fetch_log(log_data: dict) -> None:
    with open(FETCH_LOG_FILE, "w") as f:
        json.dump(log_data, f)

def window_key(now: datetime) -> str:
    """Unique string identifying the current fetch window (date + hour bucket)."""
    return now.strftime("%Y-%m-%d-%H")

def cooldown_ok(fetch_log: dict, key: str) -> bool:
    """True if no fetch has occurred for this window key within the cooldown period."""
    ts = fetch_log.get(key)
    if ts is None:
        return True
    return (time.time() - ts) >= (COOLDOWN_MIN * 60)

def record_fetch(fetch_log: dict, key: str) -> None:
    fetch_log[key] = time.time()
    save_fetch_log(fetch_log)
    # Prune entries older than 7 days to keep the file tiny
    cutoff = time.time() - 7 * 86400
    pruned = {k: v for k, v in fetch_log.items() if v > cutoff}
    save_fetch_log(pruned)

# ── Scheduling logic ───────────────────────────────────────────────────────

def todays_kickoffs(now: datetime) -> list:
    """All kickoffs on today's UTC date."""
    today = now.date()
    return [k for k in ALL_KICKOFFS if k.date() == today]

def decide_fetch(now: datetime, fetch_log: dict) -> tuple:
    """
    Returns (should_fetch: bool, reason: str, window_key: str).
    Checks triggers in priority order.
    """
    today_kicks = todays_kickoffs(now)
    is_match_day = len(today_kicks) > 0

    # ── Trigger 1: Pre-day snapshot ───────────────────────────────────
    if is_match_day:
        pre_day_utc = now.replace(
            hour=PRE_DAY_HOUR_UTC, minute=0, second=0, microsecond=0
        )
        key = f"preday-{now.strftime('%Y-%m-%d')}"
        if now >= pre_day_utc and cooldown_ok(fetch_log, key):
            return True, "Pre-day odds snapshot", key

    # ── Trigger 2: Pre-match window ───────────────────────────────────
    for ko in today_kicks:
        mins_to_ko = (ko - now).total_seconds() / 60
        if 0 <= mins_to_ko <= PRE_MATCH_WINDOW:
            key = f"prematch-{ko.strftime('%Y-%m-%dT%H%M')}"
            if cooldown_ok(fetch_log, key):
                return True, f"Pre-match: {int(mins_to_ko)}min before {ko.strftime('%H:%M')} UTC", key

    # ── Trigger 3: Post-day window ─────────────────────────────────────
    if today_kicks:
        last_ko  = max(today_kicks)
        last_end = last_ko + timedelta(minutes=MATCH_DURATION_MIN)
        mins_since_end = (now - last_end).total_seconds() / 60
        if 0 <= mins_since_end <= POST_DAY_WINDOW:
            key = f"postday-{now.strftime('%Y-%m-%d')}"
            if cooldown_ok(fetch_log, key):
                return True, "Post-day results capture", key

    # ── Trigger 4: Quiet-day morning pull ─────────────────────────────
    if not is_match_day:
        quiet_utc = now.replace(
            hour=QUIET_DAY_HOUR_UTC, minute=0, second=0, microsecond=0
        )
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

ELO = {
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

HOST_NATIONS   = {"United States","Mexico","Canada"}
HOST_ELO_BONUS = 100

# ── Maths ──────────────────────────────────────────────────────────────────

def normalise_team(raw: str) -> str:
    return TEAM_NAME_MAP.get(raw, raw)

def strip_vig(h: float, d: float, a: float) -> dict:
    if not all(p > 1.0 for p in [h, d, a]):
        return {}
    r = [1/h, 1/d, 1/a]; t = sum(r)
    return {"home": round(r[0]/t,4), "draw": round(r[1]/t,4), "away": round(r[2]/t,4)}

def elo_probs(home: str, away: str, group_stage: bool = True) -> dict:
    eh = ELO.get(home, 1700) + (HOST_ELO_BONUS if group_stage and home in HOST_NATIONS else 0)
    ea = ELO.get(away, 1700)
    ph = 1.0 / (1.0 + math.pow(10, (ea - eh) / 400.0))
    dr = max(0.15, 0.27 - abs(eh - ea) / 4000.0)
    t  = ph*(1-dr) + (1-ph)*(1-dr) + dr
    return {"home": round(ph*(1-dr)/t, 4),
            "draw": round(dr/t, 4),
            "away": round((1-ph)*(1-dr)/t, 4)}

def extract_1x2(bm: dict, slug: str):
    """
    Extract (home, draw, away) decimal prices for one bookmaker.
    The actual API uses dynamic market IDs — we find the 1X2 market
    by looking for outcomes with bookmakerOutcomeId of 'home'/'draw'/'away'.
    """
    try:
        markets = bm[slug]["markets"]
        home_price = draw_price = away_price = None
        for market_id, market in markets.items():
            outcomes = market.get("outcomes", {})
            for outcome_id, outcome in outcomes.items():
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
            # If we found all three in this market, stop
            if home_price and draw_price and away_price:
                return home_price, draw_price, away_price
        return None
    except (KeyError, TypeError, AttributeError):
        return None

# ── API ─────────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> Optional[dict]:
    if not API_KEY:
        log.error("ODDSPAPI_KEY not set.")
        return None
    url = f"{BASE_URL}/{path}"
    p   = {"apiKey": API_KEY}
    if params: p.update(params)
    try:
        resp = requests.get(url, params=p, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            log.warning("Rate limited — waiting 10s and retrying.")
            time.sleep(10)
            resp = requests.get(url, params=p, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        log.error("HTTP %s for %s", e.response.status_code, url)
    except requests.exceptions.Timeout:
        log.error("Timeout: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("Request failed: %s", e)
    except ValueError:
        log.error("Non-JSON response from %s", url)
    return None

WC_TOURNAMENT_ID = "16"   # Confirmed: "World Cup" (International) on OddsPapi
                          # futureFixtures: 100, upcomingFixtures: 1 as of June 13 2026


def fetch_all_odds() -> Optional[list]:
    """
    Single API call — all World Cup fixtures + odds.
    Tournament ID 16 = "World Cup" (confirmed from live API June 13 2026).
    Tries multiple bookmakers in case one isn't available on free tier.
    """
    log.info("Fetching World Cup odds (tournamentId=%s)...", WC_TOURNAMENT_ID)

    for bookmaker in ["pinnacle", "bet365", "draftkings", "fanduel"]:
        params = {
            "tournamentIds": WC_TOURNAMENT_ID,
            "bookmaker":     bookmaker,
        }
        log.info("  Trying bookmaker=%s...", bookmaker)
        data = api_get("odds-by-tournaments", params)
        if not data:
            continue

        fixtures = data if isinstance(data, list) else (
            data.get("data") or data.get("fixtures") or
            data.get("matches") or None)

        if not fixtures:
            log.warning("  No fixture array in response.")
            continue

        real = [f for f in fixtures if isinstance(f, dict)
                and "srl" not in str(f.get("participant1Name","")).lower()
                and "srl" not in str(f.get("participant2Name","")).lower()]

        log.info("  Success via %s: %d real fixtures (dropped %d SRL).",
                 bookmaker, len(real), len(fixtures) - len(real))
        return real

    log.error("All bookmaker attempts failed for tournament ID %s.", WC_TOURNAMENT_ID)
    return None



def fetch_participants_map() -> dict:
    """
    GET /v4/participants?sportId=10 returns ALL soccer participants as
    a flat {participantId: name} dict. One call, no ID list needed.
    We cache this in .fetch_log since it rarely changes.
    """
    fetch_log = load_fetch_log()
    cached = fetch_log.get("participants_map")
    if cached and isinstance(cached, dict) and len(cached) > 10:
        log.info("Using cached participant map (%d entries).", len(cached))
        return {str(k): normalise_team(v) for k, v in cached.items()}

    log.info("Fetching all soccer participants...")
    data = api_get("participants", {"sportId": 10, "language": "en"})
    if not data or not isinstance(data, dict):
        log.warning("Participant lookup failed — team names unavailable.")
        return {}

    # Response is {id: name} directly
    result = {str(k): normalise_team(str(v)) for k, v in data.items()}
    log.info("Loaded %d participants.", len(result))

    # Cache it (valid for the session)
    fetch_log["participants_map"] = data
    save_fetch_log(fetch_log)
    return result


# ── Record builder ─────────────────────────────────────────────────────────

def build_record(fixture: dict, pmap: dict = None) -> Optional[dict]:
    # Resolve team names — try name fields first, fall back to participant ID map
    raw_home = (fixture.get("participant1Name") or
                (pmap or {}).get(str(fixture.get("participant1Id", ""))) or "")
    raw_away = (fixture.get("participant2Name") or
                (pmap or {}).get(str(fixture.get("participant2Id", ""))) or "")

    home = normalise_team(raw_home)
    away = normalise_team(raw_away)
    if not home or not away:
        return None

    fid      = str(fixture.get("fixtureId") or fixture.get("id") or "")
    kickoff  = fixture.get("startTime") or fixture.get("date") or ""
    group    = fixture.get("roundName") or fixture.get("group") or "Group Stage"
    stage    = "Group Stage" if "group" in str(group).lower() else str(group)
    is_group = stage == "Group Stage"

    hm = TEAM_META.get(home, {"code": home[:3].upper(), "flag": home[:3].lower()})
    am = TEAM_META.get(away, {"code": away[:3].upper(), "flag": away[:3].lower()})

    bm = fixture.get("bookmakerOdds") or fixture.get("odds") or {}

    def collect_layers(slugs):
        probs = []
        for s in slugs:
            px = extract_1x2(bm, s)
            if px:
                sv = strip_vig(*px)
                if sv: probs.append(sv)
        return probs

    sb = collect_layers(["pinnacle","bet365","draftkings","fanduel","sbobet"])
    pm = collect_layers(["polymarket","kalshi"])
    ep = elo_probs(home, away, group_stage=is_group)

    def avg(lst, k): return sum(x[k] for x in lst)/len(lst) if lst else None
    def pct(v): return f"{round(v*100,1)}%" if v is not None else "--"

    layers = []
    if sb:
        layers.append({"source":"Sportsbooks (Consensus)",
                        "fav":pct(avg(sb,"home")), "draw":pct(avg(sb,"draw")), "und":pct(avg(sb,"away"))})
    layers.append({"source":"ELO/Poisson Model",
                   "fav":pct(ep["home"]), "draw":pct(ep["draw"]), "und":pct(ep["away"])})
    if pm:
        layers.append({"source":"Prediction Markets (P2P)",
                        "fav":pct(avg(pm,"home")), "draw":pct(avg(pm,"draw")), "und":pct(avg(pm,"away"))})

    best_home = avg(sb,"home") if sb else ep["home"]
    fav_team  = home if best_home >= 0.5 else away
    und_team  = away if fav_team == home else home

    try:
        dt  = datetime.fromisoformat(kickoff.replace("Z","+00:00"))
        edt = dt - timedelta(hours=4)
        meta_str = edt.strftime("%B %-d · %-I:%M %p") + " EDT · " + str(group)
    except Exception:
        meta_str = f"{kickoff} · {group}" if kickoff else str(group)

    return {
        "id":f"match_{fid}", "stage":stage, "group":str(group),
        "side":"left", "type":"UPCOMING",
        "home":home, "away":away, "favTeam":fav_team, "undTeam":und_team,
        "homeFlag":hm["flag"], "awayFlag":am["flag"],
        "kickoff":kickoff, "meta":meta_str, "layers":layers,
    }

# ── File helpers ───────────────────────────────────────────────────────────

def atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False); f.write("\n")
        shutil.move(tmp, path)
        log.info("Wrote %s.", path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s (%s) — starting fresh.", path, e)
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}

# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== World Cup ingestion starting ===")

    if not API_KEY:
        log.error("ODDSPAPI_KEY is not set."); raise SystemExit(1)

    now       = datetime.now(timezone.utc)
    fetch_log = load_fetch_log()

    if FORCE_FETCH:
        should, reason, wkey = True, "FORCE_FETCH override", f"force-{now.strftime('%Y%m%d%H%M')}"
    else:
        should, reason, wkey = decide_fetch(now, fetch_log)

    log.info("Decision: %s — %s", "FETCH" if should else "SKIP", reason)

    if not should:
        raise SystemExit(0)

    # Log what today looks like
    kicks = todays_kickoffs(now)
    if kicks:
        log.info("Today's matches: %s", [k.strftime("%H:%M") for k in sorted(kicks)])
    else:
        log.info("No matches scheduled today.")

    # Preserve completed matches
    existing  = load_existing(OUTPUT_FILE)
    completed = [m for m in existing.get("matches",[]) if m.get("type")=="COMPLETED"]
    done_ids  = {m["id"] for m in completed}
    log.info("Preserved %d completed match(es).", len(completed))

    # Single API call
    fixtures = fetch_all_odds()
    if not fixtures:
        log.warning("No fixtures returned — keeping existing data."); raise SystemExit(0)

    # Mark fetch as done BEFORE processing (so a crash doesn't re-trigger immediately)
    record_fetch(fetch_log, wkey)

    # Fetch participant names (API returns IDs, not names in fixture list)
    pmap = fetch_participants_map()

    # Build records
    upcoming = []
    for i, fx in enumerate(fixtures):
        fid = fx.get('fixtureId') or fx.get('id','')
        if f"match_{fid}" in done_ids:
            continue
        rec = build_record(fx, pmap)
        if rec:
            upcoming.append(rec)
            log.info("  [%d] %s vs %s — %d layer(s)", i+1,
                     rec["home"], rec["away"], len(rec["layers"]))
        else:
            p1 = str(fx.get("participant1Id","?"))
            p2 = str(fx.get("participant2Id","?"))
            if i < 3:
                log.warning("  [%d] Skipped — p1=%s (%s) p2=%s (%s)",
                            i+1, p1, pmap.get(p1,"?"), p2, pmap.get(p2,"?"))

    log.info("Built %d upcoming records.", len(upcoming))

    output = {
        "currentStage": existing.get("currentStage","Group Stage"),
        "lastUpdated":  now.strftime("%B %-d, %Y · %-I:%M %p UTC"),
        "matches":      completed + upcoming,
    }
    atomic_write(OUTPUT_FILE, output)
    log.info("=== Done. %d total matches ===", len(output["matches"]))


if __name__ == "__main__":
    main()
