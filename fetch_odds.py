"""
2026 World Cup Prediction Engine - Data Ingestion Script
=========================================================
Fetches live odds from OddsPapi, calculates ELO/Poisson layer,
strips bookmaker margin, and writes normalized data.json.

API docs: https://oddspapi.io/blog/world-cup-odds-api-2026-fifa/

Key facts about this API:
  - Auth: query param ?apiKey=KEY  (NOT a header)
  - Fixtures: GET /fixtures?sportId=10&from=DATE&to=DATE
  - Odds:     GET /odds?fixtureId=ID&bookmakers=pinnacle,bet365,...
  - Odds path: bookmakerOdds[slug]["markets"]["101"]["outcomes"]["101"]["players"]["0"]["price"]
  - Market 101 = 1X2 Full Time Result
  - Outcome 101 = Home, 102 = Draw, 103 = Away

Run manually:  python fetch_odds.py
Run via CI:    GitHub Action calls this on schedule
Requirements:  pip install requests
Environment:   ODDSPAPI_KEY must be set
"""

import os
import json
import math
import time
import logging
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
API_KEY         = os.environ.get("ODDSPAPI_KEY", "")
BASE_URL        = "https://api.oddspapi.io/v4"
SPORT_ID        = 10          # Soccer
TOURNAMENT_NAME = "World Cup"
OUTPUT_FILE     = "data.json"
REQUEST_DELAY   = 1.0         # seconds between calls (free tier ~0.88s cooldown)
REQUEST_TIMEOUT = 15

# Bookmakers to pull odds from (sharps + US books + prediction markets)
BOOKMAKERS = "pinnacle,bet365,draftkings,fanduel,polymarket,kalshi"

# Market IDs
MARKET_1X2   = "101"
OUTCOME_HOME = "101"
OUTCOME_DRAW = "102"
OUTCOME_AWAY = "103"

# ── Team name normalisation ────────────────────────────────────────────────
TEAM_NAME_MAP = {
    "United States":                     "United States",
    "USA":                               "United States",
    "USMNT":                             "United States",
    "US":                                "United States",
    "South Korea":                       "South Korea",
    "Korea Republic":                    "South Korea",
    "Republic of Korea":                 "South Korea",
    "KOR":                               "South Korea",
    "Bosnia & Herzegovina":              "Bosnia & Herzegovina",
    "Bosnia and Herzegovina":            "Bosnia & Herzegovina",
    "Bosnia":                            "Bosnia & Herzegovina",
    "Czech Republic":                    "Czechia",
    "Czechia":                           "Czechia",
    "Netherlands":                       "Netherlands",
    "Holland":                           "Netherlands",
    "Mexico":                            "Mexico",
    "South Africa":                      "South Africa",
    "Canada":                            "Canada",
    "Paraguay":                          "Paraguay",
    "Germany":                           "Germany",
    "Argentina":                         "Argentina",
    "England":                           "England",
    "Italy":                             "Italy",
    "France":                            "France",
    "Brazil":                            "Brazil",
    "Spain":                             "Spain",
    "Portugal":                          "Portugal",
    "Morocco":                           "Morocco",
    "Japan":                             "Japan",
    "Australia":                         "Australia",
    "Croatia":                           "Croatia",
    "Switzerland":                       "Switzerland",
    "Uruguay":                           "Uruguay",
    "Colombia":                          "Colombia",
    "Senegal":                           "Senegal",
    "Denmark":                           "Denmark",
    "Ecuador":                           "Ecuador",
    "Norway":                            "Norway",
    "Turkey":                            "Turkey",
    "Serbia":                            "Serbia",
    "Poland":                            "Poland",
    "Iran":                              "Iran",
    "Saudi Arabia":                      "Saudi Arabia",
    "Ghana":                             "Ghana",
    "Cameroon":                          "Cameroon",
    "Ivory Coast":                       "Ivory Coast",
    "Tunisia":                           "Tunisia",
    "Egypt":                             "Egypt",
    "Algeria":                           "Algeria",
    "Nigeria":                           "Nigeria",
    "Panama":                            "Panama",
    "Costa Rica":                        "Costa Rica",
    "Wales":                             "Wales",
    "Uzbekistan":                        "Uzbekistan",
    "Iraq":                              "Iraq",
    "Jordan":                            "Jordan",
    "Qatar":                             "Qatar",
    "New Zealand":                       "New Zealand",
    "Cape Verde":                        "Cape Verde",
    "Curacao":                           "Curacao",
    "Haiti":                             "Haiti",
    "Belgium":                           "Belgium",
    "Scotland":                          "Scotland",
    "DR Congo":                          "DR Congo",
    "Congo DR":                          "DR Congo",
    "IR Iran":                           "Iran",
    "Turkiye":                           "Turkey",
    "Austria":                           "Austria",
    "Sweden":                            "Sweden",
    "Ivory Coast":                       "Ivory Coast",
    "Côte d'Ivoire":                     "Ivory Coast",
}

# ── Team metadata (3-letter code + flag key for the dashboard) ─────────────
TEAM_META = {
    "Mexico":               {"code": "MEX", "flag": "mex"},
    "South Africa":         {"code": "RSA", "flag": "rsa"},
    "South Korea":          {"code": "KOR", "flag": "kor"},
    "Czechia":              {"code": "CZE", "flag": "cze"},
    "Canada":               {"code": "CAN", "flag": "can"},
    "Bosnia & Herzegovina": {"code": "BIH", "flag": "bih"},
    "United States":        {"code": "USA", "flag": "usa"},
    "Paraguay":             {"code": "PRY", "flag": "pry"},
    "Germany":              {"code": "GER", "flag": "ger"},
    "Argentina":            {"code": "ARG", "flag": "arg"},
    "England":              {"code": "ENG", "flag": "eng"},
    "Italy":                {"code": "ITA", "flag": "ita"},
    "France":               {"code": "FRA", "flag": "fra"},
    "Brazil":               {"code": "BRA", "flag": "bra"},
    "Spain":                {"code": "ESP", "flag": "esp"},
    "Portugal":             {"code": "POR", "flag": "por"},
    "Netherlands":          {"code": "NED", "flag": "ned"},
    "Morocco":              {"code": "MAR", "flag": "mar"},
    "Japan":                {"code": "JPN", "flag": "jpn"},
    "Australia":            {"code": "AUS", "flag": "aus"},
    "Croatia":              {"code": "CRO", "flag": "cro"},
    "Switzerland":          {"code": "SUI", "flag": "sui"},
    "Uruguay":              {"code": "URU", "flag": "uru"},
    "Colombia":             {"code": "COL", "flag": "col"},
    "Senegal":              {"code": "SEN", "flag": "sen"},
    "Denmark":              {"code": "DEN", "flag": "den"},
    "Ecuador":              {"code": "ECU", "flag": "ecu"},
    "Norway":               {"code": "NOR", "flag": "nor"},
    "Turkey":               {"code": "TUR", "flag": "tur"},
    "Serbia":               {"code": "SRB", "flag": "srb"},
    "Poland":               {"code": "POL", "flag": "pol"},
    "Iran":                 {"code": "IRN", "flag": "irn"},
    "Saudi Arabia":         {"code": "KSA", "flag": "ksa"},
    "Ghana":                {"code": "GHA", "flag": "gha"},
    "Cameroon":             {"code": "CMR", "flag": "cmr"},
    "Ivory Coast":          {"code": "CIV", "flag": "civ"},
    "Tunisia":              {"code": "TUN", "flag": "tun"},
    "Egypt":                {"code": "EGY", "flag": "egy"},
    "Algeria":              {"code": "ALG", "flag": "alg"},
    "Nigeria":              {"code": "NGA", "flag": "nga"},
    "Panama":               {"code": "PAN", "flag": "pan"},
    "Costa Rica":           {"code": "CRC", "flag": "crc"},
    "Wales":                {"code": "WAL", "flag": "wal"},
    "Uzbekistan":           {"code": "UZB", "flag": "uzb"},
    "Iraq":                 {"code": "IRQ", "flag": "irq"},
    "Jordan":               {"code": "JOR", "flag": "jor"},
    "Qatar":                {"code": "QAT", "flag": "qat"},
    "New Zealand":          {"code": "NZL", "flag": "nzl"},
    "Cape Verde":           {"code": "CPV", "flag": "cpv"},
    "Curacao":              {"code": "CUW", "flag": "cuw"},
    "Haiti":                {"code": "HAI", "flag": "hai"},
    "Belgium":              {"code": "BEL", "flag": "bel"},
    "Scotland":             {"code": "SCO", "flag": "sco"},
    "DR Congo":             {"code": "COD", "flag": "cod"},
    "Austria":              {"code": "AUT", "flag": "aut"},
    "Sweden":               {"code": "SWE", "flag": "swe"},
}

# ── ELO ratings ────────────────────────────────────────────────────────────
ELO = {
    "Argentina": 2040, "France": 2005, "England": 1960, "Brazil": 1955,
    "Spain": 1950, "Portugal": 1940, "Netherlands": 1935, "Germany": 1930,
    "Italy": 1910, "Croatia": 1880, "Belgium": 1875, "Uruguay": 1860,
    "Colombia": 1845, "United States": 1845, "Mexico": 1820,
    "Switzerland": 1815, "Denmark": 1810, "Japan": 1800, "Morocco": 1795,
    "Senegal": 1785, "Australia": 1770, "Canada": 1765, "South Korea": 1740,
    "Ecuador": 1730, "Norway": 1725, "Turkey": 1720, "Czechia": 1715,
    "Serbia": 1710, "Paraguay": 1705, "Poland": 1700,
    "Bosnia & Herzegovina": 1660, "Ghana": 1655, "Cameroon": 1650,
    "Ivory Coast": 1640, "South Africa": 1610, "Tunisia": 1605,
    "Iran": 1600, "Saudi Arabia": 1595, "Egypt": 1590, "Algeria": 1585,
    "Nigeria": 1580, "Panama": 1545, "Costa Rica": 1540, "Iraq": 1530,
    "Jordan": 1520, "Uzbekistan": 1510, "Qatar": 1500, "New Zealand": 1490,
    "Cape Verde": 1480, "Curacao": 1460, "Haiti": 1440, "Scotland": 1720,
    "DR Congo": 1620, "Wales": 1680, "Austria": 1830, "Sweden": 1790,
}

HOST_NATIONS   = {"United States", "Mexico", "Canada"}
HOST_ELO_BONUS = 100

# ── Maths ──────────────────────────────────────────────────────────────────

def normalise_team(raw: str) -> str:
    return TEAM_NAME_MAP.get(raw, raw)


def strip_vig(home_price: float, draw_price: float, away_price: float) -> dict:
    """Strip bookmaker margin; return true win/draw/loss probabilities."""
    if not all([home_price, draw_price, away_price]):
        return {}
    raws = [1/home_price, 1/draw_price, 1/away_price]
    total = sum(raws)
    return {
        "home": round(raws[0] / total, 4),
        "draw": round(raws[1] / total, 4),
        "away": round(raws[2] / total, 4),
    }


def elo_probs(home: str, away: str, group_stage: bool = True) -> dict:
    """Estimate match probabilities from ELO ratings."""
    elo_h = ELO.get(home, 1700)
    elo_a = ELO.get(away, 1700)
    if group_stage and home in HOST_NATIONS:
        elo_h += HOST_ELO_BONUS
    p_home_raw = 1.0 / (1.0 + math.pow(10, (elo_a - elo_h) / 400.0))
    diff = abs(elo_h - elo_a)
    draw_rate = max(0.15, 0.27 - diff / 4000.0)
    p_home = p_home_raw * (1.0 - draw_rate)
    p_away = (1.0 - p_home_raw) * (1.0 - draw_rate)
    p_draw = draw_rate
    total = p_home + p_away + p_draw
    return {
        "home": round(p_home / total, 4),
        "draw": round(p_draw / total, 4),
        "away": round(p_away / total, 4),
    }


def extract_1x2(bookmaker_odds: dict, slug: str):
    """
    Safely dig out (home, draw, away) decimal prices for one bookmaker.
    Path: bookmakerOdds[slug]["markets"]["101"]["outcomes"]["101"]["players"]["0"]["price"]
    Returns (home, draw, away) floats or None.
    """
    try:
        markets = bookmaker_odds[slug]["markets"]
        outcomes = markets[MARKET_1X2]["outcomes"]
        home = outcomes[OUTCOME_HOME]["players"]["0"]["price"]
        draw = outcomes[OUTCOME_DRAW]["players"]["0"]["price"]
        away = outcomes[OUTCOME_AWAY]["players"]["0"]["price"]
        # Validate all are sensible decimal odds
        if all(isinstance(p, (int, float)) and p > 1.0 for p in [home, draw, away]):
            return float(home), float(draw), float(away)
    except (KeyError, TypeError, IndexError):
        pass
    return None

# ── API helpers ─────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> Optional[dict]:
    """GET with error isolation. Auth goes in query params."""
    if not API_KEY:
        log.error("ODDSPAPI_KEY not set.")
        return None
    url = f"{BASE_URL}/{path}"
    p = {"apiKey": API_KEY}
    if params:
        p.update(params)
    try:
        resp = requests.get(url, params=p, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            log.warning("Rate limited — sleeping 5s.")
            time.sleep(5)
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


def fetch_fixtures_window(date_from: str, date_to: str) -> list:
    """
    Fetch fixtures for a date window (max 10 days).
    Filters to World Cup tournament only.
    """
    data = api_get("fixtures", {
        "sportId": SPORT_ID,
        "from": date_from,
        "to": date_to,
    })
    if not data:
        return []
    # Response may be a list directly or wrapped in a key
    fixtures = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    wc = [f for f in fixtures
          if isinstance(f, dict) and
          TOURNAMENT_NAME.lower() in str(f.get("tournamentName", "")).lower() and
          "srl" not in str(f.get("participant1Name", "")).lower() and
          "srl" not in str(f.get("participant2Name", "")).lower()]
    log.info("  %s to %s: %d World Cup fixtures", date_from, date_to, len(wc))
    return wc


def fetch_all_fixtures() -> list:
    """
    Walk the full tournament window in 9-day chunks (API max is 10 days).
    Tournament: June 11 – July 19, 2026.
    """
    start = datetime(2026, 6, 11)
    end   = datetime(2026, 7, 19)
    all_fixtures = []
    cursor = start

    while cursor <= end:
        window_end = min(cursor + timedelta(days=9), end)
        chunk = fetch_fixtures_window(
            cursor.strftime("%Y-%m-%d"),
            window_end.strftime("%Y-%m-%d"),
        )
        all_fixtures.extend(chunk)
        cursor = window_end + timedelta(days=1)
        if cursor <= end:
            time.sleep(REQUEST_DELAY)

    # Deduplicate by fixture ID
    seen = set()
    unique = []
    for f in all_fixtures:
        fid = str(f.get("fixtureId") or f.get("id") or "")
        if fid and fid not in seen:
            seen.add(fid)
            unique.append(f)

    log.info("Total unique World Cup fixtures: %d", len(unique))
    return unique


def fetch_odds(fixture_id: str) -> Optional[dict]:
    """Fetch 1X2 odds for one fixture. Returns bookmakerOdds dict or None."""
    time.sleep(REQUEST_DELAY)
    data = api_get("odds", {
        "fixtureId": fixture_id,
        "bookmakers": BOOKMAKERS,
    })
    if not data:
        return None
    # bookmakerOdds is the key per the docs
    return data.get("bookmakerOdds") or data.get("bookmakers")


# ── Build match record ──────────────────────────────────────────────────────

def build_record(fixture: dict, bm_odds: Optional[dict]) -> Optional[dict]:
    """Combine fixture + live odds + ELO into the dashboard's data schema."""
    raw_home = (fixture.get("participant1Name") or
                fixture.get("home_team") or
                fixture.get("homeTeam") or "")
    raw_away = (fixture.get("participant2Name") or
                fixture.get("away_team") or
                fixture.get("awayTeam") or "")

    home = normalise_team(raw_home)
    away = normalise_team(raw_away)
    if not home or not away:
        return None

    fixture_id = str(fixture.get("fixtureId") or fixture.get("id") or "")
    kickoff    = fixture.get("startTime") or fixture.get("date") or ""
    group      = (fixture.get("roundName") or
                  fixture.get("group") or
                  fixture.get("league_round") or "Group Stage")
    is_group   = "group" in str(group).lower()

    home_meta = TEAM_META.get(home, {"code": home[:3].upper(), "flag": home[:3].lower()})
    away_meta = TEAM_META.get(away, {"code": away[:3].upper(), "flag": away[:3].lower()})

    # ── Layer 1: Sportsbook consensus (average across sharp books) ─────────
    sb_home, sb_draw, sb_away = [], [], []
    SHARP_BOOKS = ["pinnacle", "bet365", "draftkings", "fanduel", "sbobet"]

    if bm_odds:
        for slug in SHARP_BOOKS:
            prices = extract_1x2(bm_odds, slug)
            if prices:
                stripped = strip_vig(*prices)
                sb_home.append(stripped["home"])
                sb_draw.append(stripped["draw"])
                sb_away.append(stripped["away"])

    has_sb = bool(sb_home)
    if has_sb:
        sb_layer = {
            "source": "Sportsbooks (Consensus)",
            "fav":    f"{round(sum(sb_home)/len(sb_home)*100, 1)}%",
            "draw":   f"{round(sum(sb_draw)/len(sb_draw)*100, 1)}%",
            "und":    f"{round(sum(sb_away)/len(sb_away)*100, 1)}%",
        }
    else:
        sb_layer = None

    # ── Layer 2: ELO/Poisson model ─────────────────────────────────────────
    ep = elo_probs(home, away, group_stage=is_group)
    elo_layer = {
        "source": "ELO/Poisson Model",
        "fav":    f"{round(ep['home']*100, 1)}%",
        "draw":   f"{round(ep['draw']*100, 1)}%",
        "und":    f"{round(ep['away']*100, 1)}%",
    }

    # ── Layer 3: Prediction markets ────────────────────────────────────────
    pm_home, pm_draw, pm_away = [], [], []
    PM_BOOKS = ["polymarket", "kalshi"]

    if bm_odds:
        for slug in PM_BOOKS:
            prices = extract_1x2(bm_odds, slug)
            if prices:
                stripped = strip_vig(*prices)
                pm_home.append(stripped["home"])
                pm_draw.append(stripped["draw"])
                pm_away.append(stripped["away"])

    has_pm = bool(pm_home)
    if has_pm:
        pm_layer = {
            "source": "Prediction Markets (P2P)",
            "fav":    f"{round(sum(pm_home)/len(pm_home)*100, 1)}%",
            "draw":   f"{round(sum(pm_draw)/len(pm_draw)*100, 1)}%",
            "und":    f"{round(sum(pm_away)/len(pm_away)*100, 1)}%",
        }
    else:
        pm_layer = None

    # Build layers list (always include ELO; others when data available)
    layers = []
    if sb_layer:
        layers.append(sb_layer)
    layers.append(elo_layer)
    if pm_layer:
        layers.append(pm_layer)

    # Favourite = team with highest average home-win probability
    best_home_pct = ep["home"]
    if sb_home:
        best_home_pct = sum(sb_home) / len(sb_home)

    fav_team = home if best_home_pct >= 0.5 else away
    und_team = away if fav_team == home else home

    # Human-readable kickoff line
    try:
        dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        # Convert to EDT (UTC-4) for display
        edt = dt - timedelta(hours=4)
        meta_str = f"{edt.strftime('%B %-d · %-I:%M %p')} EDT · {group}"
    except Exception:
        meta_str = f"{kickoff} · {group}" if kickoff else group

    return {
        "id":       f"match_{fixture_id}",
        "stage":    str(group),
        "side":     "left",
        "type":     "UPCOMING",
        "home":     home,
        "away":     away,
        "favTeam":  fav_team,
        "undTeam":  und_team,
        "homeFlag": home_meta["flag"],
        "awayFlag": away_meta["flag"],
        "meta":     meta_str,
        "layers":   layers,
    }


# ── Atomic file write ──────────────────────────────────────────────────────

def atomic_write(path: str, data: dict) -> None:
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.move(tmp, path)
        log.info("Wrote %s successfully.", path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s (%s) — starting fresh.", path, e)
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== World Cup data ingestion starting ===")

    if not API_KEY:
        log.error("ODDSPAPI_KEY is not set.")
        raise SystemExit(1)

    existing      = load_existing(OUTPUT_FILE)
    completed     = [m for m in existing.get("matches", []) if m.get("type") == "COMPLETED"]
    log.info("Preserved %d completed match(es).", len(completed))

    log.info("Fetching fixtures (June 11 – July 19)...")
    fixtures = fetch_all_fixtures()

    if not fixtures:
        log.warning("No fixtures returned — keeping existing data unchanged.")
        return

    # Only process UPCOMING fixtures (skip ones we already have as COMPLETED)
    completed_ids = {m["id"] for m in completed}
    new_upcoming  = []

    for i, fixture in enumerate(fixtures):
        fid = str(fixture.get("fixtureId") or fixture.get("id") or f"unknown_{i}")
        match_id = f"match_{fid}"

        if match_id in completed_ids:
            log.info("Skipping %s (already completed).", match_id)
            continue

        log.info("Processing %s (%d/%d)...", fid, i+1, len(fixtures))
        bm_odds = fetch_odds(fid)

        record = build_record(fixture, bm_odds)
        if record:
            new_upcoming.append(record)
            layers_count = len(record.get("layers", []))
            log.info("  → %s vs %s | %d layer(s)", record["home"], record["away"], layers_count)
        else:
            log.warning("  → Skipped fixture %s (no valid teams).", fid)

    log.info("Built %d upcoming record(s).", len(new_upcoming))

    output = {
        "currentStage": existing.get("currentStage", "Group Stage"),
        "lastUpdated":  datetime.now(timezone.utc).strftime("%B %-d, %Y · %-I:%M %p UTC"),
        "matches":      completed + new_upcoming,
    }

    atomic_write(OUTPUT_FILE, output)
    log.info("=== Done. %d total matches in %s ===", len(output["matches"]), OUTPUT_FILE)


if __name__ == "__main__":
    main()
