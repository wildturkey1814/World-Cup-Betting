"""
2026 World Cup Prediction Engine - Data Ingestion Script
=========================================================
Fetches live odds from OddsPapi (sportsbooks + prediction markets),
calculates an ELO/Poisson probability layer, strips bookmaker margin,
and writes a normalized data.json for the dashboard.

Run manually:  python fetch_odds.py
Run via CI:    GitHub Action calls this automatically every 30 minutes.

Requirements:  pip install requests
Environment:   ODDSPAPI_KEY must be set (via GitHub Secret or local .env)
"""

import os
import json
import math
import time
import logging
import tempfile
import shutil
from datetime import datetime, timezone
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
API_KEY        = os.environ.get("ODDSPAPI_KEY", "")
BASE_URL       = "https://api.oddspapi.io/v4"          # OddsPapi base URL
SPORT          = "soccer"
LEAGUE_SLUG    = "world-cup"                            # OddsPapi soccer league slug
OUTPUT_FILE    = "data.json"                            # written to repo root
REQUEST_DELAY  = 1.0                                    # seconds between API calls (rate-limit safety)
REQUEST_TIMEOUT = 15                                    # seconds before giving up on a call

# ── Team name normalisation ────────────────────────────────────────────────
# Maps every known variant from the API to a single display name.
# Add entries here if a new variant shows up in the feed.
TEAM_NAME_MAP = {
    # United States
    "United States":                        "United States",
    "USA":                                  "United States",
    "USMNT":                                "United States",
    "United States Men's National Team":    "United States",
    "US":                                   "United States",
    # South Korea
    "South Korea":                          "South Korea",
    "Korea Republic":                       "South Korea",
    "Republic of Korea":                    "South Korea",
    "KOR":                                  "South Korea",
    # Bosnia
    "Bosnia & Herzegovina":                 "Bosnia & Herzegovina",
    "Bosnia and Herzegovina":               "Bosnia & Herzegovina",
    "Bosnia":                               "Bosnia & Herzegovina",
    "BIH":                                  "Bosnia & Herzegovina",
    # Standard names that may appear inconsistently
    "Mexico":                               "Mexico",
    "South Africa":                         "South Africa",
    "Czechia":                              "Czechia",
    "Czech Republic":                       "Czechia",
    "Canada":                               "Canada",
    "Paraguay":                             "Paraguay",
    "Germany":                              "Germany",
    "Argentina":                            "Argentina",
    "England":                              "England",
    "Italy":                                "Italy",
    "France":                               "France",
    "Brazil":                               "Brazil",
    "Spain":                                "Spain",
    "Portugal":                             "Portugal",
    "Netherlands":                          "Netherlands",
    "Holland":                              "Netherlands",
    "Morocco":                              "Morocco",
    "Japan":                                "Japan",
    "Australia":                            "Australia",
    "Croatia":                              "Croatia",
    "Switzerland":                          "Switzerland",
    "Uruguay":                              "Uruguay",
    "Colombia":                             "Colombia",
    "Senegal":                              "Senegal",
    "Denmark":                              "Denmark",
    "Ecuador":                              "Ecuador",
    "Ghana":                                "Ghana",
    "Cameroon":                             "Cameroon",
    "Serbia":                               "Serbia",
    "Poland":                               "Poland",
    "Wales":                                "Wales",
    "Iran":                                 "Iran",
    "Saudi Arabia":                         "Saudi Arabia",
    "Tunisia":                              "Tunisia",
    "Costa Rica":                           "Costa Rica",
    "Nigeria":                              "Nigeria",
    "Egypt":                                "Egypt",
    "Algeria":                              "Algeria",
    "Norway":                               "Norway",
    "Turkey":                               "Turkey",
    "Uzbekistan":                           "Uzbekistan",
    "Panama":                               "Panama",
    "Venezuela":                            "Venezuela",
    "Iraq":                                 "Iraq",
    "Jordan":                               "Jordan",
    "Qatar":                                "Qatar",
    "New Zealand":                          "New Zealand",
    "Cape Verde":                           "Cape Verde",
    "Curacao":                              "Curacao",
    "Haiti":                                "Haiti",
}

# ── Team metadata ──────────────────────────────────────────────────────────
# 3-letter code and flag SVG key for each normalised team name.
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
    "Tunisia":              {"code": "TUN", "flag": "tun"},
    "Egypt":                {"code": "EGY", "flag": "egy"},
    "Algeria":              {"code": "ALG", "flag": "alg"},
    "Panama":               {"code": "PAN", "flag": "pan"},
    "Costa Rica":           {"code": "CRC", "flag": "crc"},
    "Nigeria":              {"code": "NGA", "flag": "nga"},
    "Wales":                {"code": "WAL", "flag": "wal"},
    "Uzbekistan":           {"code": "UZB", "flag": "uzb"},
    "Iraq":                 {"code": "IRQ", "flag": "irq"},
    "Jordan":               {"code": "JOR", "flag": "jor"},
    "Qatar":                {"code": "QAT", "flag": "qat"},
    "New Zealand":          {"code": "NZL", "flag": "nzl"},
    "Cape Verde":           {"code": "CPV", "flag": "cpv"},
    "Curacao":              {"code": "CUW", "flag": "cuw"},
    "Haiti":                {"code": "HAI", "flag": "hai"},
}

# ── ELO ratings (baseline, updated pre-tournament) ─────────────────────────
# Source: eloratings.net pre-tournament values.
# Update these once before the tournament starts; they don't change mid-game.
ELO_RATINGS = {
    "Argentina":            2040,
    "France":               2005,
    "England":              1960,
    "Brazil":               1955,
    "Spain":                1950,
    "Portugal":             1940,
    "Netherlands":          1935,
    "Germany":              1930,
    "Italy":                1910,
    "Croatia":              1880,
    "Belgium":              1875,
    "Uruguay":              1860,
    "Colombia":             1845,
    "United States":        1845,
    "Mexico":               1820,
    "Switzerland":          1815,
    "Denmark":              1810,
    "Japan":                1800,
    "Morocco":              1795,
    "Senegal":              1785,
    "Australia":            1770,
    "Canada":               1765,
    "South Korea":          1740,
    "Ecuador":              1730,
    "Norway":               1725,
    "Turkey":               1720,
    "Czechia":              1715,
    "Serbia":               1710,
    "Paraguay":             1705,
    "Poland":               1700,
    "Bosnia & Herzegovina": 1660,
    "Ghana":                1655,
    "Cameroon":             1650,
    "Ivory Coast":          1640,
    "South Africa":         1610,
    "Tunisia":              1605,
    "Iran":                 1600,
    "Saudi Arabia":         1595,
    "Egypt":                1590,
    "Algeria":              1585,
    "Nigeria":              1580,
    "Panama":               1545,
    "Costa Rica":           1540,
    "Iraq":                 1530,
    "Jordan":               1520,
    "Uzbekistan":           1510,
    "Qatar":                1500,
    "New Zealand":          1490,
    "Cape Verde":           1480,
    "Curacao":              1460,
    "Haiti":                1440,
}

# Host-nation bonus applied during group stage (home crowd effect)
HOST_NATIONS   = {"United States", "Mexico", "Canada"}
HOST_ELO_BONUS = 100

# ── Maths helpers ──────────────────────────────────────────────────────────

def normalise_team(raw: str) -> str:
    """Return the canonical team name, or the raw string if not in the map."""
    return TEAM_NAME_MAP.get(raw, raw)


def strip_vig(outcomes: list[dict]) -> dict:
    """
    Given a list of {"name": str, "price": float} decimal-odds outcomes,
    strip the bookmaker margin and return true probabilities.

    P_raw_i  = 1 / decimal_odds_i
    overround = sum(P_raw_i)
    P_true_i  = P_raw_i / overround
    """
    raw_probs = {o["name"]: 1.0 / o["price"] for o in outcomes if o.get("price", 0) > 1}
    total = sum(raw_probs.values())
    if total == 0:
        return {}
    return {name: round(p / total, 4) for name, p in raw_probs.items()}


def elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Expected win probability for team A against team B using ELO formula."""
    return 1.0 / (1.0 + math.pow(10, (elo_b - elo_a) / 400.0))


def elo_match_probs(home: str, away: str, group_stage: bool = True) -> dict:
    """
    Estimate Win / Draw / Loss probabilities from ELO ratings.

    Draw probability is derived empirically from tournament data:
    roughly 23-27% of World Cup group matches end level. We use a
    base draw rate modulated by how close the teams are in ELO.
    """
    elo_h = ELO_RATINGS.get(home, 1700)
    elo_a = ELO_RATINGS.get(away, 1700)

    if group_stage and home in HOST_NATIONS:
        elo_h += HOST_ELO_BONUS

    p_home_win_raw = elo_win_prob(elo_h, elo_a)

    # Draw rate: closer teams draw more often (max ~30%), dominant gaps draw less (~15%)
    elo_diff = abs(elo_h - elo_a)
    draw_rate = max(0.15, 0.27 - (elo_diff / 4000.0))

    # Split remaining probability proportionally
    p_home = p_home_win_raw * (1.0 - draw_rate)
    p_away = (1.0 - p_home_win_raw) * (1.0 - draw_rate)
    p_draw = draw_rate

    # Normalise to ensure they sum to 1.0
    total = p_home + p_away + p_draw
    return {
        "home": round(p_home / total, 4),
        "draw": round(p_draw / total, 4),
        "away": round(p_away / total, 4),
    }


# ── API client ─────────────────────────────────────────────────────────────

def api_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    GET request to OddsPapi with error isolation.
    Returns parsed JSON dict or None if the call fails.
    """
    if not API_KEY:
        log.error("ODDSPAPI_KEY environment variable is not set.")
        return None

    url = f"{BASE_URL}/{path}"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            log.warning("Rate limited — waiting 5 s then retrying.")
            time.sleep(5)
            resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        log.error("Request timed out: %s", url)
    except requests.exceptions.HTTPError as e:
        log.error("HTTP error %s for %s", e.response.status_code, url)
    except requests.exceptions.RequestException as e:
        log.error("Request failed: %s", e)
    except ValueError:
        log.error("Response was not valid JSON from %s", url)
    return None


# ── Data fetching ──────────────────────────────────────────────────────────

def fetch_fixtures() -> list[dict]:
    """
    Pull all upcoming and live World Cup fixtures.
    OddsPapi returns fixtures paginated; we walk all pages.
    """
    fixtures = []
    page = 1

    while True:
        data = api_get(f"sports/{SPORT}/leagues/{LEAGUE_SLUG}/fixtures",
                       params={"page": page, "status": "upcoming"})
        if not data:
            break

        batch = data.get("data") or data.get("fixtures") or []
        if not batch:
            break

        fixtures.extend(batch)
        log.info("  fetched fixtures page %d (%d fixtures so far)", page, len(fixtures))

        meta = data.get("meta", {})
        if not meta.get("next_page"):
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    log.info("Total fixtures found: %d", len(fixtures))
    return fixtures


def fetch_odds_for_fixture(fixture_id: str) -> Optional[dict]:
    """
    Fetch 1X2 head-to-head odds for a single fixture.
    Returns the raw bookmakers list or None.
    """
    time.sleep(REQUEST_DELAY)
    data = api_get(f"sports/{SPORT}/fixtures/{fixture_id}/odds",
                   params={"market": "h2h"})
    if not data:
        return None
    return data.get("data") or data.get("bookmakers")


# ── Match processing ───────────────────────────────────────────────────────

def process_sportsbook_layer(bookmakers_data) -> Optional[dict]:
    """
    Average the vig-stripped probabilities across all available bookmakers.
    Returns {"home": float, "draw": float, "away": float} or None.
    """
    if not bookmakers_data:
        return None

    home_probs, draw_probs, away_probs = [], [], []

    books = bookmakers_data if isinstance(bookmakers_data, list) else [bookmakers_data]

    for book in books:
        markets = book.get("markets", [])
        for market in markets:
            if market.get("key") not in ("h2h", "1x2", "match_winner"):
                continue
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 3:
                continue
            stripped = strip_vig(outcomes)
            for name, prob in stripped.items():
                norm = normalise_team(name)
                if name.lower() in ("draw", "tie", "x"):
                    draw_probs.append(prob)
                # We'll match home/away later once we know the teams
                else:
                    # Stash with the normalised team name
                    if norm:
                        home_probs.append((norm, prob))

    # This raw approach works; the caller correlates home/away names
    return {"raw_books": books}


def build_match_record(fixture: dict, bookmakers_data) -> Optional[dict]:
    """
    Combine fixture metadata + sportsbook odds + ELO layer into the
    exact schema that data.json (and the dashboard) expects.
    """
    raw_home = fixture.get("home_team") or fixture.get("homeTeam") or ""
    raw_away = fixture.get("away_team") or fixture.get("awayTeam") or ""
    home = normalise_team(raw_home)
    away = normalise_team(raw_away)

    if not home or not away:
        return None

    fixture_id   = fixture.get("id") or fixture.get("fixture_id") or ""
    kickoff      = fixture.get("date") or fixture.get("kickoff") or ""
    group        = fixture.get("group") or fixture.get("league_round") or "Group Stage"
    is_group     = "group" in str(group).lower()

    home_meta = TEAM_META.get(home, {"code": home[:3].upper(), "flag": home[:3].lower()})
    away_meta = TEAM_META.get(away, {"code": away[:3].upper(), "flag": away[:3].lower()})

    # ── Layer 1: Sportsbook consensus (vig-stripped average) ──────────────
    sb_home_probs, sb_draw_probs, sb_away_probs = [], [], []

    if bookmakers_data:
        books = bookmakers_data if isinstance(bookmakers_data, list) else [bookmakers_data]
        for book in books:
            markets = book.get("markets", [])
            for market in markets:
                if market.get("key") not in ("h2h", "1x2", "match_winner"):
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) < 3:
                    continue
                stripped = strip_vig(outcomes)
                for name, prob in stripped.items():
                    norm = normalise_team(name)
                    low  = name.lower()
                    if low in ("draw", "tie", "x"):
                        sb_draw_probs.append(prob)
                    elif norm == home:
                        sb_home_probs.append(prob)
                    elif norm == away:
                        sb_away_probs.append(prob)

    has_sb = sb_home_probs and sb_draw_probs and sb_away_probs
    if has_sb:
        sb_layer = {
            "source":   "Sportsbooks (Consensus)",
            "fav":      f"{round(sum(sb_home_probs)/len(sb_home_probs)*100, 1)}%",
            "draw":     f"{round(sum(sb_draw_probs)/len(sb_draw_probs)*100, 1)}%",
            "und":      f"{round(sum(sb_away_probs)/len(sb_away_probs)*100, 1)}%",
        }
    else:
        sb_layer = None

    # ── Layer 2: ELO / Poisson model ──────────────────────────────────────
    elo_probs = elo_match_probs(home, away, group_stage=is_group)
    elo_layer = {
        "source": "ELO/Poisson Model",
        "fav":    f"{round(elo_probs['home']*100, 1)}%",
        "draw":   f"{round(elo_probs['draw']*100, 1)}%",
        "und":    f"{round(elo_probs['away']*100, 1)}%",
    }

    # ── Layer 3: Prediction markets (Polymarket / Kalshi) ─────────────────
    # OddsPapi includes these as bookmakers with keys "polymarket"/"kalshi"
    pm_home_probs, pm_draw_probs, pm_away_probs = [], [], []

    if bookmakers_data:
        books = bookmakers_data if isinstance(bookmakers_data, list) else [bookmakers_data]
        for book in books:
            if book.get("key", "").lower() not in ("polymarket", "kalshi"):
                continue
            markets = book.get("markets", [])
            for market in markets:
                outcomes = market.get("outcomes", [])
                stripped = strip_vig(outcomes)
                for name, prob in stripped.items():
                    norm = normalise_team(name)
                    low  = name.lower()
                    if low in ("draw", "tie", "x"):
                        pm_draw_probs.append(prob)
                    elif norm == home:
                        pm_home_probs.append(prob)
                    elif norm == away:
                        pm_away_probs.append(prob)

    has_pm = pm_home_probs and pm_draw_probs and pm_away_probs
    if has_pm:
        pm_layer = {
            "source": "Prediction Markets (P2P)",
            "fav":    f"{round(sum(pm_home_probs)/len(pm_home_probs)*100, 1)}%",
            "draw":   f"{round(sum(pm_draw_probs)/len(pm_draw_probs)*100, 1)}%",
            "und":    f"{round(sum(pm_away_probs)/len(pm_away_probs)*100, 1)}%",
        }
    else:
        pm_layer = None

    # Build the layers list — always include ELO, include others when available
    layers = []
    if sb_layer:
        layers.append(sb_layer)
    layers.append(elo_layer)
    if pm_layer:
        layers.append(pm_layer)

    # Determine favourite (highest home-win probability across available sources)
    best_home_pct = elo_probs["home"]
    if sb_home_probs:
        best_home_pct = sum(sb_home_probs) / len(sb_home_probs)

    fav_team = home if best_home_pct >= 0.5 else away
    und_team = away if fav_team == home else home

    # Human-readable meta line
    if kickoff:
        try:
            dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
            meta_str = f"{dt.strftime('%B %-d · %-I:%M %p %Z')} · {group}"
        except Exception:
            meta_str = f"{kickoff} · {group}"
    else:
        meta_str = group

    return {
        "id":        f"match_{fixture_id}",
        "stage":     group,
        "side":      "left",          # bracket side — future enhancement
        "type":      "UPCOMING",
        "home":      home,
        "away":      away,
        "favTeam":   fav_team,
        "undTeam":   und_team,
        "homeFlag":  home_meta["flag"],
        "awayFlag":  away_meta["flag"],
        "meta":      meta_str,
        "layers":    layers,
    }


# ── Atomic file writer ─────────────────────────────────────────────────────

def atomic_write(path: str, data: dict) -> None:
    """
    Write to a temp file first, then rename over the target.
    This prevents the dashboard from ever reading a half-written file.
    """
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.move(tmp_path, path)
        log.info("Wrote %s successfully.", path)
    except Exception:
        os.unlink(tmp_path)
        raise


# ── Load existing data (preserve completed matches) ───────────────────────

def load_existing(path: str) -> dict:
    """
    Read the current data.json.
    We keep all COMPLETED matches exactly as they are — only UPCOMING
    records get replaced by fresh API data.
    """
    if not os.path.exists(path):
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read existing %s (%s) — starting fresh.", path, e)
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== World Cup data ingestion starting ===")

    if not API_KEY:
        log.error("ODDSPAPI_KEY is not set. Set it as an environment variable and retry.")
        raise SystemExit(1)

    # Load existing file so we can preserve completed matches
    existing = load_existing(OUTPUT_FILE)
    completed_matches = [m for m in existing.get("matches", []) if m.get("type") == "COMPLETED"]
    log.info("Preserved %d completed match(es) from existing file.", len(completed_matches))

    # Fetch live fixtures
    log.info("Fetching fixtures from OddsPapi...")
    fixtures = fetch_fixtures()

    if not fixtures:
        log.warning("No fixtures returned — keeping existing data unchanged.")
        return

    # Process each fixture
    new_upcoming = []
    for i, fixture in enumerate(fixtures):
        fid = fixture.get("id") or fixture.get("fixture_id") or f"unknown_{i}"
        log.info("Processing fixture %s (%d/%d)...", fid, i + 1, len(fixtures))

        bookmakers = fetch_odds_for_fixture(str(fid))

        record = build_match_record(fixture, bookmakers)
        if record:
            new_upcoming.append(record)
        else:
            log.warning("Skipped fixture %s (could not build record).", fid)

    log.info("Built %d upcoming match record(s).", len(new_upcoming))

    # Assemble final output — completed first (chronological), then upcoming
    output = {
        "currentStage": existing.get("currentStage", "Group Stage"),
        "lastUpdated":  datetime.now(timezone.utc).strftime("%B %-d, %Y · %-I:%M %p UTC"),
        "matches":      completed_matches + new_upcoming,
    }

    atomic_write(OUTPUT_FILE, output)
    log.info("=== Done. %d total matches in data.json ===", len(output["matches"]))


if __name__ == "__main__":
    main()
