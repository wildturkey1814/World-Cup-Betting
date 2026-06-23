"""
2026 World Cup — Polymarket Tournament Winner Ingestion
=======================================================
Pulls World Cup tournament winner probabilities from Polymarket's
public Gamma API (no API key required).
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from data_utils import atomic_write, format_utc_display, load_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

GAMMA_API       = "https://gamma-api.polymarket.com"
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 20

POLYMARKET_TEAM_MAP = {
    "Argentina":"Argentina","France":"France","England":"England",
    "Brazil":"Brazil","Spain":"Spain","Portugal":"Portugal",
    "Netherlands":"Netherlands","Germany":"Germany","Italy":"Italy",
    "Croatia":"Croatia","Belgium":"Belgium","Uruguay":"Uruguay",
    "Colombia":"Colombia","United States":"United States","USA":"United States",
    "Mexico":"Mexico","Austria":"Austria","Switzerland":"Switzerland",
    "Sweden":"Sweden","Denmark":"Denmark","Japan":"Japan",
    "Morocco":"Morocco","Senegal":"Senegal","Australia":"Australia",
    "Canada":"Canada","South Korea":"South Korea","Korea Republic":"South Korea",
    "Ecuador":"Ecuador","Norway":"Norway","Turkey":"Turkey","Turkiye":"Turkey",
    "Czechia":"Czechia","Czech Republic":"Czechia","Scotland":"Scotland",
    "Serbia":"Serbia","Paraguay":"Paraguay","Poland":"Poland","Wales":"Wales",
    "Bosnia & Herzegovina":"Bosnia & Herzegovina",
    "Bosnia and Herzegovina":"Bosnia & Herzegovina",
    "Ghana":"Ghana","Cameroon":"Cameroon","Ivory Coast":"Ivory Coast",
    "Côte d'Ivoire":"Ivory Coast","DR Congo":"DR Congo","Congo DR":"DR Congo",
    "South Africa":"South Africa","Tunisia":"Tunisia","Iran":"Iran","IR Iran":"Iran",
    "Saudi Arabia":"Saudi Arabia","Egypt":"Egypt","Algeria":"Algeria",
    "Nigeria":"Nigeria","Panama":"Panama","Costa Rica":"Costa Rica",
    "Iraq":"Iraq","Jordan":"Jordan","Uzbekistan":"Uzbekistan","Qatar":"Qatar",
    "New Zealand":"New Zealand","Cape Verde":"Cape Verde",
    "Curacao":"Curacao","Haiti":"Haiti",
}

def normalise_poly_team(raw: str) -> str:
    return POLYMARKET_TEAM_MAP.get(raw.strip(), raw.strip())


def gamma_get(path: str, params: dict = None) -> Optional[object]:
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


def fetch_tournament_winner_probs() -> dict:
    log.info("Fetching Polymarket World Cup winner markets...")
    result = {}

    # Strategy 1: search markets directly for World Cup winner
    log.info("Trying markets search...")
    data = gamma_get("/markets", {
        "q": "win the 2026 FIFA World Cup",
        "active": "true",
        "limit": 100,
    })

    markets = []
    if isinstance(data, list):
        markets = data
    elif isinstance(data, dict):
        markets = data.get("markets", [])

    log.info("  Found %d markets from search.", len(markets))

    for market in markets:
        question = market.get("question", "") or market.get("title", "")

        m = re.search(r"Will (.+?) win the 2026", question, re.IGNORECASE)
        if not m:
            continue

        raw_team = m.group(1).strip()
        team = normalise_poly_team(raw_team)

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

    # Strategy 2: if search returned nothing, try the event by tag
    if not result:
        log.info("Search returned nothing — trying event tag search...")
        data = gamma_get("/events", {
            "tag": "sports",
            "active": "true",
            "limit": 50,
        })
        events = data if isinstance(data, list) else (data or {}).get("events", [])
        for event in events:
            title = event.get("title", "").lower()
            if "world cup" in title and "winner" in title:
                log.info("  Found event: %s", event.get("title",""))
                for market in event.get("markets", []):
                    question = market.get("question", "")
                    m = re.search(r"Will (.+?) win", question, re.IGNORECASE)
                    if not m:
                        continue
                    team = normalise_poly_team(m.group(1).strip())
                    prices = market.get("outcomePrices", "[]")
                    if isinstance(prices, str):
                        try: prices = json.loads(prices)
                        except: continue
                    if prices:
                        try:
                            result[team] = round(float(prices[0]), 4)
                            log.info("  %s → %.1f%%", team, float(prices[0])*100)
                        except: continue

    log.info("Fetched Polymarket probs for %d teams.", len(result))
    return result


def load_existing(path: str) -> dict:
    return load_data(path)


def pct(v: float) -> str:
    return f"{round(v * 100, 1)}%" if v is not None else "--"


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
        changed = False

        if probs.get(home) is not None:
            match["polymarketWinProbHome"] = pct(probs[home])
            changed = True
        if probs.get(away) is not None:
            match["polymarketWinProbAway"] = pct(probs[away])
            changed = True

        if changed:
            updated += 1

    now = datetime.now(timezone.utc)
    existing["lastUpdated"]       = format_utc_display(now)
    existing["polymarketUpdated"] = format_utc_display(now)

    from standings import apply_eliminated_tournament_odds, compute_knockout_eliminated

    def _standings_input() -> list[dict]:
        seen = {str(m.get("id")) for m in matches if m.get("id")}
        merged = list(matches)
        try:
            with open("completed_matches.json", "r", encoding="utf-8") as f:
                archive = json.load(f)
            if isinstance(archive, list):
                for entry in archive:
                    mid = str(entry.get("id") or "")
                    if mid and mid not in seen:
                        merged.append(entry)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return merged

    eliminated = compute_knockout_eliminated(_standings_input())
    apply_eliminated_tournament_odds(matches, eliminated)
    existing["knockoutEliminated"] = sorted(eliminated)

    atomic_write(OUTPUT_FILE, existing)
    log.info("=== Done. Updated %d match records. ===", updated)


if __name__ == "__main__":
    main()
