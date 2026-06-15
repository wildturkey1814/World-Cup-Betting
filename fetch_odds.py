"""
2026 World Cup Prediction Engine — Event-Driven Ingestion Script
================================================================
Fetches ALL World Cup fixtures + odds in a SINGLE API call per run,
but only triggers that call when something meaningful is about to
happen or just finished.

Also downloads player headshot images to assets/headshots/ on each run
so they can be served locally from GitHub Pages (avoids hotlink blocks).
"""

import os
import json
import math
import time
import logging
import tempfile
import shutil
import re
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

BOOKMAKERS = "pinnacle,bet365,draftkings,fanduel,polymarket,kalshi"
MARKET_1X2, OUTCOME_HOME, OUTCOME_DRAW, OUTCOME_AWAY = "101","101","102","103"

# ── Player headshot registry ───────────────────────────────────────────────
# Maps country name → (player display name, Wikimedia Commons thumb path)
# Images are downloaded once to assets/headshots/ and served locally.

HEADSHOT_REGISTRY = {
    "Argentina":    ("L. Messi",       "b/b4/Lionel-Messi-Argentina-2022-FIFA-World-Cup_%28cropped%29.jpg/400px-Lionel-Messi-Argentina-2022-FIFA-World-Cup_%28cropped%29.jpg"),
    "France":       ("K. Mbappe",      "5/5f/Kylian_Mbapp%C3%A9_2019.jpg/400px-Kylian_Mbapp%C3%A9_2019.jpg"),
    "England":      ("J. Bellingham",  "7/7d/Jude_Bellingham_2022_%28cropped%29.jpg/400px-Jude_Bellingham_2022_%28cropped%29.jpg"),
    "Brazil":       ("Vinicius Jr.",   "9/9e/Vinicius_Junior_2022_FIFA_World_Cup.jpg/400px-Vinicius_Junior_2022_FIFA_World_Cup.jpg"),
    "Spain":        ("Pedri",          "9/91/Pedri_2021_%28cropped%29.jpg/400px-Pedri_2021_%28cropped%29.jpg"),
    "Portugal":     ("C. Ronaldo",     "8/8c/Cristiano_Ronaldo_2018.jpg/400px-Cristiano_Ronaldo_2018.jpg"),
    "Netherlands":  ("V. van Dijk",    "a/a3/Virgil_van_Dijk_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Virgil_van_Dijk_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Germany":      ("J. Musiala",     "8/86/Jamal_Musiala_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Jamal_Musiala_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Morocco":      ("A. Hakimi",      "3/3c/Achraf_Hakimi_2022_FIFA_World_Cup.jpg/400px-Achraf_Hakimi_2022_FIFA_World_Cup.jpg"),
    "Japan":        ("T. Mitoma",      "c/cc/Kaoru_Mitoma_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Kaoru_Mitoma_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "United States":("C. Pulisic",     "e/e6/Christian_Pulisic_2019_%28cropped%29.jpg/400px-Christian_Pulisic_2019_%28cropped%29.jpg"),
    "Mexico":       ("H. Lozano",      "0/0e/Hirving_Lozano_2018.jpg/400px-Hirving_Lozano_2018.jpg"),
    "Canada":       ("A. Davies",      "3/3e/Alphonso_Davies_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Alphonso_Davies_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Uruguay":      ("D. Nunez",       "2/2b/Darwin_N%C3%BA%C3%B1ez_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Darwin_N%C3%BA%C3%B1ez_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Colombia":     ("L. Diaz",        "a/ac/Luis_D%C3%ADaz_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Luis_D%C3%ADaz_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Croatia":      ("L. Modric",      "9/9e/Luka_Modric_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Luka_Modric_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Switzerland":  ("G. Xhaka",       "c/c5/Granit_Xhaka_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Granit_Xhaka_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Senegal":      ("S. Mane",        "a/a0/Sadio_Man%C3%A9_2019_%28cropped%29.jpg/400px-Sadio_Man%C3%A9_2019_%28cropped%29.jpg"),
    "South Korea":  ("Son Heung-min",  "b/bf/Son_Heung-min_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Son_Heung-min_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Norway":       ("E. Haaland",     "1/17/Erling_Haaland_2023.jpg/400px-Erling_Haaland_2023.jpg"),
    "Australia":    ("M. Leckie",      "0/0a/Mathew_Leckie_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Mathew_Leckie_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Ecuador":      ("E. Valencia",    "d/d9/Enner_Valencia_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Enner_Valencia_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Turkey":       ("H. Calhanoglu",  "7/7e/Hakan_Calhanoglu_2021.jpg/400px-Hakan_Calhanoglu_2021.jpg"),
    "Belgium":      ("K. De Bruyne",   "1/18/Kevin_De_Bruyne_2022_FIFA_World_Cup_%28cropped%29.jpg/400px-Kevin_De_Bruyne_2022_FIFA_World_Cup_%28cropped%29.jpg"),
    "Austria":      ("D. Alaba",       "5/5d/David_Alaba_2021.jpg/400px-David_Alaba_2021.jpg"),
    "Scotland":     ("A. Robertson",   "3/38/Andrew_Robertson_2022_%28cropped%29.jpg/400px-Andrew_Robertson_2022_%28cropped%29.jpg"),
}

def slugify(text):
    """Convert player name to safe filename: 'L. Messi' -> 'l-messi'"""
    text = text.lower()
    # Basic ASCII transliteration for common accented chars
    text = text.replace('é','e').replace('ü','u').replace('ã','a').replace('í','i').replace('ó','o')
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text

def download_headshots():
    """
    Download all player headshots from Wikimedia Commons to assets/headshots/.
    Skips files that already exist (idempotent).
    Uses a browser User-Agent to avoid Wikimedia bot blocks.
    """
    target_dir = "assets/headshots"
    os.makedirs(target_dir, exist_ok=True)

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; WorldCupDashboard/1.0; educational use)'
    }

    downloaded = 0
    skipped = 0
    failed = 0

    for country, (player_name, wiki_path) in HEADSHOT_REGISTRY.items():
        filename = slugify(player_name) + ".jpg"
        filepath = os.path.join(target_dir, filename)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
            skipped += 1
            continue

        url = f"https://upload.wikimedia.org/wikipedia/commons/thumb/{wiki_path}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                log.info("Downloaded: %s (%s)", player_name, country)
                downloaded += 1
            else:
                log.warning("Failed %s: HTTP %d", player_name, resp.status_code)
                failed += 1
        except Exception as e:
            log.warning("Error downloading %s: %s", player_name, e)
            failed += 1

        # Small delay to be polite to Wikimedia
        time.sleep(0.3)

    log.info("Headshots: %d downloaded, %d skipped (cached), %d failed.",
             downloaded, skipped, failed)


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
    "2026-06-26T18:00:00Z","2026-06-26T18:00:00Z",
    "2026-06-26T22:00:00Z","2026-06-26T22:00:00Z",
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

def normalise_team(raw):
    return TEAM_NAME_MAP.get(raw, raw)

def strip_vig(h, d, a):
    if not all(p > 1.0 for p in [h, d, a]):
        return {}
    r = [1/h, 1/d, 1/a]; t = sum(r)
    return {"home": round(r[0]/t,4), "draw": round(r[1]/t,4), "away": round(r[2]/t,4)}

def elo_probs(home, away, group_stage=True):
    eh = ELO.get(home, 1700) + (HOST_ELO_BONUS if group_stage and home in HOST_NATIONS else 0)
    ea = ELO.get(away, 1700)
    ph = 1.0 / (1.0 + math.pow(10, (ea - eh) / 400.0))
    dr = max(0.15, 0.27 - abs(eh - ea) / 4000.0)
    t  = ph*(1-dr) + (1-ph)*(1-dr) + dr
    return {"home": round(ph*(1-dr)/t, 4), "draw": round(dr/t, 4), "away": round((1-ph)*(1-dr)/t, 4)}

def extract_1x2(bm, slug):
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
            if home_price and draw_price and away_price:
                return home_price, draw_price, away_price
        return None
    except (KeyError, TypeError, AttributeError):
        return None

# ── API ─────────────────────────────────────────────────────────────────────

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
        real = [f for f in fixtures if isinstance(f, dict)
                and "srl" not in str(f.get("participant1Name","")).lower()
                and "srl" not in str(f.get("participant2Name","")).lower()]
        log.info("  Success via %s: %d real fixtures.", bookmaker, len(real))
        return real
    log.error("All bookmaker attempts failed.")
    return None

def fetch_participants_map():
    fetch_log = load_fetch_log()
    cached = fetch_log.get("participants_map")
    if cached and isinstance(cached, dict) and len(cached) > 10:
        return {str(k): normalise_team(v) for k, v in cached.items()}
    log.info("Fetching all soccer participants...")
    data = api_get("participants", {"sportId": 10, "language": "en"})
    if not data or not isinstance(data, dict):
        return {}
    result = {str(k): normalise_team(str(v)) for k, v in data.items()}
    fetch_log["participants_map"] = data
    save_fetch_log(fetch_log)
    return result

# ── Record builder ─────────────────────────────────────────────────────────

def build_record(fixture, pmap=None):
    raw_home = (fixture.get("participant1Name") or
                (pmap or {}).get(str(fixture.get("participant1Id", ""))) or "")
    raw_away = (fixture.get("participant2Name") or
                (pmap or {}).get(str(fixture.get("participant2Id", ""))) or "")
    home = normalise_team(raw_home)
    away = normalise_team(raw_away)
    if not home or not away:
        return None

    fid     = str(fixture.get("fixtureId") or fixture.get("id") or "")
    kickoff = fixture.get("startTime") or fixture.get("date") or ""
    group   = fixture.get("roundName") or fixture.get("group") or "Group Stage"
    stage   = "Group Stage" if "group" in str(group).lower() else str(group)
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

    try:
        dt  = datetime.fromisoformat(kickoff.replace("Z","+00:00"))
        edt = dt - timedelta(hours=4)
        meta_str = edt.strftime("%B %-d · %-I:%M %p") + " EDT · " + str(group)
    except Exception:
        meta_str = f"{kickoff} · {group}" if kickoff else str(group)

    return {
        "id":f"match_{fid}", "stage":stage, "group":str(group),
        "side":"left", "type":"UPCOMING",
        "home":home, "away":away, "favTeam":fav_team,
        "homeFlag":hm["flag"], "awayFlag":am["flag"],
        "kickoff":kickoff, "meta":meta_str, "layers":layers,
    }

# ── File helpers ───────────────────────────────────────────────────────────

def atomic_write(path, data):
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

def load_existing(path):
    if not os.path.exists(path):
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s (%s) — starting fresh.", path, e)
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    log.info("=== World Cup ingestion starting ===")

    if not API_KEYS:
        log.error("No ODDSPAPI keys set.")
        raise SystemExit(1)

    # Always download headshots (idempotent — skips existing files)
    log.info("Checking player headshots...")
    download_headshots()

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

    existing  = load_existing(OUTPUT_FILE)
    completed = [m for m in existing.get("matches",[]) if m.get("type")=="COMPLETED"]
    done_ids  = {m["id"] for m in completed}
    log.info("Preserved %d completed match(es).", len(completed))

    fixtures = fetch_all_odds()
    if not fixtures:
        log.warning("No fixtures returned — keeping existing data.")
        raise SystemExit(0)

    record_fetch(fetch_log, wkey)
    pmap = fetch_participants_map()

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
