"""
2026 World Cup — Polymarket Tournament Winner Ingestion
=======================================================
Pulls World Cup tournament winner probabilities from Polymarket's
public Gamma API (no API key required).

Stores polymarketWinProbHome / polymarketWinProbAway on each match
record so the frontend can show a "Tournament Outlook" comparison
panel alongside Kalshi prices.

Run manually:  python fetch_polymarket.py
Requirements:  pip install requests
"""

import os
import json
import logging
import tempfile
import shutil
import re
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

# ── Config ─────────────────────────────────────────────────────────────────
GAMMA_API       = "https://gamma-api.polymarket.com"
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 20

# The Polymarket World Cup winner event slug
WC_EVENT_SLUG   = "world-cup-winner"

# ── Team name normalisation ────────────────────────────────────────────────
# Maps Polymarket market question text to our canonical team names.

POLYMARKET_TEAM_MAP = {
    "Argentina":             "Argentina",
    "France":                "France",
    "England":               "England",
    "Brazil":                "Brazil",
    "Spain":                 "Spain",
    "Portugal":              "Portugal",
    "Netherlands":           "Netherlands",
    "Germany":               "Germany",
    "Italy":                 "Italy",
    "Croatia":               "Croatia",
    "Belgium":               "Belgium",
    "Uruguay":               "Uruguay",
    "Colombia":              "Colombia",
    "United States":         "United States",
    "USA":                   "United States",
    "Mexico":                "Mexico",
    "Austria":               "Austria",
    "Switzerland":           "Switzerland",
    "Sweden":                "Sweden",
    "Denmark":               "Denmark",
    "Japan":                 "Japan",
    "Morocco":               "Morocco",
    "Senegal":               "Senegal",
    "Australia":             "Australia",
    "Canada":                "Canada",
    "South Korea":           "South Korea",
    "Korea Republic":        "South Korea",
    "Ecuador":               "Ecuador",
    "Norway":                "Norway",
    "Turkey":                "Turkey",
    "Turkiye":               "Turkey",
    "Czechia":               "Czechia",
    "Czech Republic":        "Czechia",
    "Scotland":              "Scotland",
    "Serbia":                "Serbia",
    "Paraguay":              "Paraguay",
    "Poland":                "Poland",
    "Wales":                 "Wales",
    "Bosnia & Herzegovina":  "Bosnia & Herzegovina",
    "Bosnia and Herzegovina":"Bosnia & Herzegovina",
    "Ghana":                 "Ghana",
    "Cameroon":              "Cameroon",
    "Ivory Coast":           "Ivory Coast",
    "Côte d'Ivoire":         "Ivory Coast",
    "DR Congo":              "DR Congo",
    "Congo DR":              "DR Congo",
    "South Africa":          "South Africa",
    "Tunisia":               "Tunisia",
    "Iran":                  "Iran",
    "IR Iran":               "Iran",
    "Saudi Arabia":          "Saudi Arabia",
    "Egypt":                 "Egypt",
    "Algeria":               "Algeria",
    "Nigeria":               "Nigeria",
    "Panama":                "Panama",
    "Costa Rica":            "Costa Rica",
    "Iraq":                  "Iraq",
    "Jordan":                "Jordan",
    "Uzbekistan":            "Uzbekistan",
    "Qatar":                 "Qatar",
    "New Zealand":           "New Zealand",
    "Cape Verde":            "Cape Verde",
    "Curacao":               "Curacao",
    "Haiti":                 "Haiti",
}

def normalise_poly_team(raw: str) -> str:
    return POLYMARKET_TEAM_MAP.get(raw.strip(), raw.strip())


# ── API helpers ─────────────────────────────────────────────────────────────

def gamma_get(path: str, params: dict = None) -> Optional[dict]:
    url = f"{GAMMA_API}{path}"
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        log.error("Timeout: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("Request failed: %s", e)
    except ValueError:
        log.error("Non-JSON response from %s", url)
    return None


# ── Fetch tournament winner probs ──────────────────────────────────────────

def fetch_tournament_winner_probs() -> dict:
    """
    Fetches the World Cup winner event from Polymarket Gamma API.
    Returns { "France": 0.161, "Spain": 0.168, ... }

    The event contains one market per team. Each market's outcomePrices[0]
    is the YES share price = implied win probability.
    """
    log.info("Fetching Polymarket World Cup winner markets...")

    # First try the known event slug
    data = gamma_get(f"/events/{WC_EVENT_SLUG}")

    # Fall back to search if slug fails
    if not data:
        log.info("Slug lookup failed — trying search...")
        data = gamma_get("/events", {"q": "2026 FIFA World Cup winner", "active": "true"})
        if isinstance(data, list) and data:
            # Pick the event with highest volume (most likely the main one)
            data = max(data, key=lambda e: e.get("volume", 0))

    if not data:
        log.error("Could not find Polymarket World Cup winner event.")
        return {}

    # The event contains a list of markets (one per team)
    markets = data.get("markets", [])
    if not markets:
        # Some responses nest differently
        markets = data if isinstance(data, list) else []

    result = {}
    for market in markets:
        question = market.get("question", "") or market.get("title", "")

        # Extract team name from question
        # e.g. "Will France win the 2026 FIFA World Cup?"
        m = re.search(r"Will (.+?) win", question, re.IGNORECASE)
        if not m:
            continue

        raw_team = m.group(1).strip()
        team = normalise_poly_team(raw_team)

        # outcomePrices is a JSON string array e.g. '["0.161", "0.839"]'
        outcome_prices = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, ValueError):
                continue

        if outcome_prices and len(outcome_prices) >= 1:
            try:
                yes_price = float(outcome_prices[0])
                result[team] = round(yes_price, 4)
                log.info("  %s → %.1f%%", team, yes_price * 100)
            except (ValueError, TypeError):
                continue

    log.info("Fetched Polymarket probs for %d teams.", len(result))
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
    log.info("=== Polymarket ingestion starting ===")

    probs = fetch_tournament_winner_probs()

    if not probs:
        log.warning("No Polymarket data returned — leaving data.json unchanged.")
        raise SystemExit(0)

    existing = load_existing(OUTPUT_FILE)
    matches  = existing.get("matches", [])
    updated  = 0

    for match in matches:
        home = match.get("home", "")
        away = match.get("away", "")

        home_prob = probs.get(home)
        away_prob = probs.get(away)

        if home_prob is not None:
            match["polymarketWinProbHome"] = pct(home_prob)
        if away_prob is not None:
            match["polymarketWinProbAway"] = pct(away_prob)

        if home_prob is not None or away_prob is not None:
            updated += 1

    now = datetime.now(timezone.utc)
    existing["lastUpdated"]        = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
    existing["polymarketUpdated"]  = now.strftime("%B %-d, %Y · %-I:%M %p UTC")

    atomic_write(OUTPUT_FILE, existing)
    log.info("=== Done. Updated %d match records. ===", updated)


if __name__ == "__main__":
    main()
