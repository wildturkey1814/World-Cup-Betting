"""
2026 World Cup — Kalshi Prediction Market Ingestion
====================================================
Pulls two data types from Kalshi's public REST API:

  1. Match-level 1X2 odds  (KXWCGAME series)
     → populates layers[2] "Prediction Markets (Kalshi)" on each match card

  2. Tournament winner probabilities per team (KXMENWORLDCUP series)
     → stored as kalshiWinProbHome / kalshiWinProbAway on each match record

Auth: Kalshi uses RSA key-pair signing (API Key ID + Private Key).
Secrets required:
  KALSHI_API_KEY_ID   — the Key ID string from Kalshi dashboard
  KALSHI_PRIVATE_KEY  — the PEM-encoded RSA private key

Run manually:  python fetch_kalshi.py
Requirements:  pip install requests cryptography
"""

import os
import json
import time
import logging
import tempfile
import shutil
import base64
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
KALSHI_API_KEY_ID   = os.environ.get("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY  = os.environ.get("KALSHI_PRIVATE_KEY", "")

BASE_URL        = "https://api.elections.kalshi.com/trade-api/v2"
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 20

# Kalshi series tickers
MATCH_SERIES        = "KXWCGAME"
TOURNAMENT_SERIES   = "KXMENWORLDCUP"

# ── RSA Auth ───────────────────────────────────────────────────────────────

def build_auth_headers(method: str, path: str) -> dict:
    """
    Kalshi uses RSA-PSS signatures.
    Header format:  KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE
    """
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        raise ValueError("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set.")

    timestamp_ms = str(int(time.time() * 1000))
    msg = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")

    # Load the private key — handle both raw PEM and escaped newlines
    pem = KALSHI_PRIVATE_KEY.replace("\\n", "\n")
    if not pem.strip().startswith("-----"):
        # Bare base64 — wrap it
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem}\n-----END RSA PRIVATE KEY-----\n"

    private_key = serialization.load_pem_private_key(pem.encode(), password=None)

    sig = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode()

    return {
        "KALSHI-ACCESS-KEY":       KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type":            "application/json",
    }


def kalshi_get(path: str, params: dict = None) -> Optional[dict]:
    """Authenticated GET request to Kalshi API."""
    try:
        headers = build_auth_headers("GET", path)
    except ValueError as e:
        log.error("Auth error: %s", e)
        return None

    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401:
            log.error("Kalshi 401 Unauthorized — check KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY.")
            return None
        if resp.status_code == 429:
            log.warning("Kalshi rate limited — waiting 5s.")
            time.sleep(5)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        log.error("Timeout: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("Request failed: %s", e)
    except ValueError:
        log.error("Non-JSON response from %s", url)
    return None


# ── Team name normalisation ────────────────────────────────────────────────
# Maps Kalshi ticker suffixes and market titles to our canonical team names.

KALSHI_TEAM_MAP = {
    # Ticker 3-letter codes used in KXWCGAME tickers
    "MEX": "Mexico",       "RSA": "South Africa", "CAN": "Canada",
    "USA": "United States","PAR": "Paraguay",      "GER": "Germany",
    "ARG": "Argentina",    "ENG": "England",       "ITA": "Italy",
    "FRA": "France",       "BRA": "Brazil",        "ESP": "Spain",
    "POR": "Portugal",     "NED": "Netherlands",   "MAR": "Morocco",
    "JPN": "Japan",        "AUS": "Australia",     "CRO": "Croatia",
    "SUI": "Switzerland",  "URU": "Uruguay",       "COL": "Colombia",
    "SEN": "Senegal",      "DEN": "Denmark",       "ECU": "Ecuador",
    "NOR": "Norway",       "TUR": "Turkey",        "SRB": "Serbia",
    "POL": "Poland",       "IRN": "Iran",          "KSA": "Saudi Arabia",
    "GHA": "Ghana",        "CMR": "Cameroon",      "CIV": "Ivory Coast",
    "TUN": "Tunisia",      "EGY": "Egypt",         "ALG": "Algeria",
    "NGA": "Nigeria",      "PAN": "Panama",        "CRC": "Costa Rica",
    "WAL": "Wales",        "UZB": "Uzbekistan",    "IRQ": "Iraq",
    "JOR": "Jordan",       "QAT": "Qatar",         "NZL": "New Zealand",
    "CPV": "Cape Verde",   "CUW": "Curacao",       "HAI": "Haiti",
    "BEL": "Belgium",      "SCO": "Scotland",      "COD": "DR Congo",
    "AUT": "Austria",      "SWE": "Sweden",        "KOR": "South Korea",
    "CZE": "Czechia",      "BIH": "Bosnia & Herzegovina",
    # Full name variants from market titles
    "United States":        "United States",
    "South Africa":         "South Africa",
    "South Korea":          "South Korea",
    "Bosnia and Herzegovina":"Bosnia & Herzegovina",
    "Bosnia & Herzegovina": "Bosnia & Herzegovina",
    "DR Congo":             "DR Congo",
    "Ivory Coast":          "Ivory Coast",
    "Cape Verde":           "Cape Verde",
    "New Zealand":          "New Zealand",
    "Saudi Arabia":         "Saudi Arabia",
    "Costa Rica":           "Costa Rica",
}

def normalise_kalshi_team(raw: str) -> str:
    return KALSHI_TEAM_MAP.get(raw.strip(), raw.strip())


# ── Tournament winner markets ──────────────────────────────────────────────

def fetch_tournament_winner_probs() -> dict:
    """
    Fetches all KXMENWORLDCUP markets and returns:
      { "France": 0.161, "Spain": 0.168, ... }
    Price of the YES contract = implied probability of winning the tournament.
    """
    log.info("Fetching Kalshi tournament winner markets...")
    result = {}
    cursor = None

    while True:
        params = {"series_ticker": TOURNAMENT_SERIES, "limit": 100, "status": "open"}
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/markets", params)
        if not data:
            break

        markets = data.get("markets", [])
        for m in markets:
            title = m.get("title", "")
            yes_price = m.get("yes_bid") or m.get("last_price") or 0

            # Extract team name from title e.g. "Will France win the 2026 Men's World Cup?"
            match = re.search(r"Will (.+?) win the 2026", title, re.IGNORECASE)
            if match:
                raw_team = match.group(1).strip()
                team = normalise_kalshi_team(raw_team)
                if yes_price and yes_price > 0:
                    result[team] = round(yes_price / 100, 4)  # cents → probability
                    log.info("  Tournament: %s → %.1f%%", team, yes_price)

        cursor = data.get("cursor")
        if not cursor or not markets:
            break

    log.info("Fetched tournament winner probs for %d teams.", len(result))
    return result


# ── Match-level 1X2 markets ────────────────────────────────────────────────

def parse_match_ticker(ticker: str) -> Optional[tuple]:
    """
    Parse KXWCGAME-26MMMDDXXXYYY into (month, day, home_code, away_code).
    Example: KXWCGAME-26JUN16FRASEN → ("JUN", 16, "FRA", "SEN")
    """
    # Pattern: KXWCGAME-26 + MMM + DD + HOME(3) + AWAY(3)
    m = re.match(r"KXWCGAME-26([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})", ticker)
    if m:
        return m.group(1), int(m.group(2)), m.group(3), m.group(4)
    return None


def fetch_match_odds() -> dict:
    """
    Fetches all KXWCGAME markets and returns a dict keyed by
    (home_canonical, away_canonical) → {"home": 0.62, "draw": 0.21, "away": 0.17}

    Kalshi match markets are structured as mutually exclusive outcomes:
      - Home win (YES price)
      - Draw (YES price)
      - Away win (YES price)
    All three sum to ~1.0 (minus vig).
    """
    log.info("Fetching Kalshi match-level markets...")
    raw_markets = {}
    cursor = None

    while True:
        params = {"series_ticker": MATCH_SERIES, "limit": 100, "status": "open"}
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/markets", params)
        if not data:
            break

        markets = data.get("markets", [])
        for m in markets:
            ticker = m.get("ticker", "")
            parsed = parse_match_ticker(ticker)
            if not parsed:
                continue

            _, _, home_code, away_code = parsed
            home = normalise_kalshi_team(home_code)
            away = normalise_kalshi_team(away_code)
            key  = (home, away)

            yes_price = m.get("yes_bid") or m.get("last_price") or 0
            title     = m.get("title", "").lower()

            if key not in raw_markets:
                raw_markets[key] = {"home": None, "draw": None, "away": None}

            # Identify outcome type from title
            if "tie" in title or "draw" in title:
                raw_markets[key]["draw"] = yes_price / 100
            elif home.lower() in title or home_code.lower() in title:
                raw_markets[key]["home"] = yes_price / 100
            elif away.lower() in title or away_code.lower() in title:
                raw_markets[key]["away"] = yes_price / 100

        cursor = data.get("cursor")
        if not cursor or not markets:
            break

    # Normalise: strip vig so probabilities sum to 1.0
    result = {}
    for (home, away), probs in raw_markets.items():
        h, d, a = probs.get("home"), probs.get("draw"), probs.get("away")
        if h is not None and d is not None and a is not None:
            total = h + d + a
            if total > 0:
                result[(home, away)] = {
                    "home": round(h / total, 4),
                    "draw": round(d / total, 4),
                    "away": round(a / total, 4),
                }
                log.info("  Match: %s vs %s → H:%.1f%% D:%.1f%% A:%.1f%%",
                         home, away, h*100, d*100, a*100)
        else:
            log.warning("  Incomplete odds for %s vs %s — skipping.", home, away)

    log.info("Fetched match odds for %d matches.", len(result))
    return result


# ── File helpers ───────────────────────────────────────────────────────────

def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s (%s) — using empty.", path, e)
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}


def atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.move(tmp, path)
        log.info("Wrote %s.", path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def pct(v: float) -> str:
    return f"{round(v * 100, 1)}%" if v is not None else "--"


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Kalshi ingestion starting ===")

    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        log.error("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set as environment secrets.")
        raise SystemExit(1)

    # Fetch both data types
    tournament_probs = fetch_tournament_winner_probs()
    match_odds       = fetch_match_odds()

    if not tournament_probs and not match_odds:
        log.warning("No data returned from Kalshi — leaving data.json unchanged.")
        raise SystemExit(0)

    # Load existing data.json
    existing = load_existing(OUTPUT_FILE)
    matches  = existing.get("matches", [])
    updated  = 0

    for match in matches:
        home = match.get("home", "")
        away = match.get("away", "")

        # ── 1. Tournament winner probabilities ─────────────────────────
        home_win_prob = tournament_probs.get(home)
        away_win_prob = tournament_probs.get(away)

        if home_win_prob is not None:
            match["kalshiWinProbHome"] = pct(home_win_prob)
        if away_win_prob is not None:
            match["kalshiWinProbAway"] = pct(away_win_prob)

        # ── 2. Match-level 1X2 odds ────────────────────────────────────
        odds = match_odds.get((home, away)) or match_odds.get((away, home))
        if odds:
            # If we found reversed (away, home), flip the values
            if (away, home) in match_odds and (home, away) not in match_odds:
                odds = {"home": odds["away"], "draw": odds["draw"], "away": odds["home"]}

            kalshi_layer = {
                "source": "Prediction Markets (Kalshi)",
                "fav":    pct(odds["home"]),
                "draw":   pct(odds["draw"]),
                "und":    pct(odds["away"]),
            }

            # Replace existing Kalshi/P2P layer if present, else append
            layers = match.get("layers", [])
            replaced = False
            for i, layer in enumerate(layers):
                src = layer.get("source", "")
                if "Kalshi" in src or "P2P" in src or "Prediction" in src:
                    layers[i] = kalshi_layer
                    replaced = True
                    break
            if not replaced:
                layers.append(kalshi_layer)
            match["layers"] = layers
            updated += 1

    now = datetime.now(timezone.utc)
    existing["lastUpdated"] = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
    existing["kalshiUpdated"] = now.strftime("%B %-d, %Y · %-I:%M %p UTC")

    atomic_write(OUTPUT_FILE, existing)
    log.info("=== Done. Updated %d match records. ===", updated)


if __name__ == "__main__":
    main()
