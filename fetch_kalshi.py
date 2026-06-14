"""
2026 World Cup — Kalshi Prediction Market Ingestion
====================================================
Pulls two data types from Kalshi's public REST API:

  1. Match-level odds (KXWCGAME series)
     NOTE: Kalshi resolves on 90-minute result only — no draw market.
     So we store home/away win probabilities and '--' for draw.
     → populates layers[2] "Prediction Markets (Kalshi)" on each match card

  2. Tournament winner probabilities per team (KXMENWORLDCUP series)
     → stored as kalshiWinProbHome / kalshiWinProbAway on each match record

Auth: Kalshi uses RSA key-pair signing.
Secrets required:
  KALSHI_API_KEY_ID   — the Key ID string from Kalshi dashboard
  KALSHI_PRIVATE_KEY  — the full PEM-encoded RSA private key (including headers)
"""

import os
import json
import time
import logging
import tempfile
import shutil
import base64
import re
from datetime import datetime, timezone
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
KALSHI_API_KEY_ID  = os.environ.get("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY = os.environ.get("KALSHI_PRIVATE_KEY", "")

BASE_URL        = "https://external-api.kalshi.com/trade-api/v2"
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 20

MATCH_SERIES      = "KXWCGAME"
TOURNAMENT_SERIES = "KXMENWORLDCUP"

# ── RSA Auth ───────────────────────────────────────────────────────────────

def build_auth_headers(method: str, path: str) -> dict:
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        raise ValueError("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set.")

    timestamp_ms = str(int(time.time() * 1000))
    msg = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")

    # Normalise the PEM key — handle escaped newlines from GitHub Secrets
    pem = KALSHI_PRIVATE_KEY.replace("\\n", "\n").strip()

    # If it doesn't have PEM headers, add them
    if "-----" not in pem:
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem}\n-----END RSA PRIVATE KEY-----"

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
    try:
        headers = build_auth_headers("GET", path)
    except Exception as e:
        log.error("Auth error: %s", e)
        return None

    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401:
            log.error("Kalshi 401 Unauthorized — check credentials.")
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

KALSHI_TEAM_MAP = {
    "MEX":"Mexico",       "RSA":"South Africa", "CAN":"Canada",
    "USA":"United States","PAR":"Paraguay",      "GER":"Germany",
    "ARG":"Argentina",    "ENG":"England",       "ITA":"Italy",
    "FRA":"France",       "BRA":"Brazil",        "ESP":"Spain",
    "POR":"Portugal",     "NED":"Netherlands",   "MAR":"Morocco",
    "JPN":"Japan",        "AUS":"Australia",     "CRO":"Croatia",
    "SUI":"Switzerland",  "URU":"Uruguay",       "COL":"Colombia",
    "SEN":"Senegal",      "DEN":"Denmark",       "ECU":"Ecuador",
    "NOR":"Norway",       "TUR":"Turkey",        "SRB":"Serbia",
    "POL":"Poland",       "IRN":"Iran",          "KSA":"Saudi Arabia",
    "GHA":"Ghana",        "CMR":"Cameroon",      "CIV":"Ivory Coast",
    "TUN":"Tunisia",      "EGY":"Egypt",         "ALG":"Algeria",
    "DZA":"Algeria",      "NGA":"Nigeria",       "PAN":"Panama",
    "CRC":"Costa Rica",   "WAL":"Wales",         "UZB":"Uzbekistan",
    "IRQ":"Iraq",         "JOR":"Jordan",        "QAT":"Qatar",
    "NZL":"New Zealand",  "CPV":"Cape Verde",    "CUW":"Curacao",
    "HTI":"Haiti",        "HAI":"Haiti",         "BEL":"Belgium",
    "SCO":"Scotland",     "COD":"DR Congo",      "AUT":"Austria",
    "SWE":"Sweden",       "KOR":"South Korea",   "CZE":"Czechia",
    "BIH":"Bosnia & Herzegovina",
    "IRI":"Iran",
    # Full name variants
    "United States":          "United States",
    "South Africa":           "South Africa",
    "South Korea":            "South Korea",
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Bosnia & Herzegovina":   "Bosnia & Herzegovina",
    "DR Congo":               "DR Congo",
    "Ivory Coast":            "Ivory Coast",
    "Cape Verde":             "Cape Verde",
    "New Zealand":            "New Zealand",
    "Saudi Arabia":           "Saudi Arabia",
    "Costa Rica":             "Costa Rica",
}

def normalise_kalshi_team(raw: str) -> str:
    return KALSHI_TEAM_MAP.get(raw.strip(), raw.strip())


# ── Tournament winner markets ──────────────────────────────────────────────

def fetch_tournament_winner_probs() -> dict:
    """
    Returns { "France": 0.161, "Spain": 0.168, ... }
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
            title     = m.get("title", "")
            # Try yes_bid first, then last_price, then yes_ask
            yes_price = (m.get("yes_bid_dollars") or m.get("last_price_dollars") or
                         m.get("yes_ask_dollars") or 0)

            match = re.search(r"Will (.+?) win", title, re.IGNORECASE)
            if match:
                raw_team = match.group(1).strip().lstrip("the ").strip()
                team = normalise_kalshi_team(raw_team)
                if yes_price and float(yes_price) > 0:
                    result[team] = round(float(yes_price), 4)
                    log.info("  Tournament: %s → %.1f%%", team, float(yes_price)*100)

        cursor = data.get("cursor")
        if not cursor or not markets:
            break

    log.info("Fetched tournament winner probs for %d teams.", len(result))
    return result


# ── Match-level markets ────────────────────────────────────────────────────

def parse_match_ticker(ticker: str) -> Optional[tuple]:
    """
    Parse KXWCGAME-26MMMDDXXXXXX-OUTCOME
    Real format: KXWCGAME-26JUN27CODUZB-UZB
      event part = CODUZB = home(COD) + away(UZB)
      outcome    = UZB | COD | TIE

    Returns (month, day, home_code, away_code, outcome_code)
    """
    m = re.match(r"KXWCGAME-26([A-Z]{3})(\d{2})([A-Z]{6})-([A-Z]{2,4})$", ticker)
    if m:
        event     = m.group(3)
        home_code = event[:3]
        away_code = event[3:]
        outcome   = m.group(4)
        return m.group(1), int(m.group(2)), home_code, away_code, outcome
    return None


def fetch_match_odds() -> dict:
    """
    Fetches all KXWCGAME markets.

    IMPORTANT: Kalshi resolves World Cup matches on 90-minute result only.
    They offer Home Win and Away Win markets — NO draw market.
    We store home/away probabilities and '--' for draw.

    Returns:
      { (home_team, away_team): {"home": 0.62, "draw": None, "away": 0.38} }
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
        # Log first 5 tickers so we can see the actual format
        for m in markets[:5]:
            log.info("  RAW TICKER: %s | title: %s | no_sub_title: %s",
                     m.get("ticker","?"),
                     m.get("title","?")[:40],
                     m.get("no_sub_title","?"))
        for m in markets:
            ticker = m.get("ticker", "")
            parsed = parse_match_ticker(ticker)
            if not parsed:
                log.debug("  Could not parse ticker: %s", ticker)
                continue

            _, _, home_code, away_code = parsed
            home = normalise_kalshi_team(home_code)
            away = normalise_kalshi_team(away_code)
            key  = (home, away)

            yes_price = (m.get("yes_bid_dollars") or m.get("last_price_dollars") or
                         m.get("yes_ask_dollars") or 0)

            if key not in raw_markets:
                raw_markets[key] = {
                    "home": None, "away": None, "draw": None,
                    "home_code": home_code, "away_code": away_code
                }

            # Use the ticker suffix to identify outcome — most reliable method.
            # Format: KXWCGAME-26JUN12USAPAR-USA / -TIE / -PRY
            ticker_parts = ticker.split("-")
            outcome_code = ticker_parts[-1].upper() if ticker_parts else ""

            if outcome_code in ("TIE", "DRAW"):
                raw_markets[key]["draw"] = float(yes_price)
            elif outcome_code == home_code.upper():
                raw_markets[key]["home"] = float(yes_price)
            elif outcome_code == away_code.upper():
                raw_markets[key]["away"] = float(yes_price)
            else:
                # Fallback: check no_sub_title / subtitle
                title = m.get("no_sub_title","") or m.get("subtitle","") or m.get("title","")
                title_lower = title.lower()
                if "tie" in title_lower or "draw" in title_lower:
                    raw_markets[key]["draw"] = float(yes_price)
                elif home_code.lower() in title_lower or home.lower() in title_lower:
                    raw_markets[key]["home"] = float(yes_price)
                elif away_code.lower() in title_lower or away.lower() in title_lower:
                    raw_markets[key]["away"] = float(yes_price)

        cursor = data.get("cursor")
        if not cursor or not markets:
            break

    # Build result — accept matches with at least home + away
    # Normalise so home + away (+ draw if present) sum to 1.0
    result = {}
    for (home, away), probs in raw_markets.items():
        h = probs.get("home")
        a = probs.get("away")
        d = probs.get("draw")  # Will be None for most Kalshi WC markets

        if h is not None and a is not None:
            if d is not None:
                total = h + d + a
                {
                    "home": round(h / total, 4),
                    "draw": round(d / total, 4),
                    "away": round(a / total, 4),
                }
            else:
                # No draw — normalise home/away only, store '--' for draw
                total = h + a
                if total > 0:
                    {
                        "home": round(h / total, 4),
                        "draw": None,  # rendered as '--' in frontend
                        "away": round(a / total, 4),
                    }
            log.info("  Match: %s vs %s → H:%.1f%% D:%s A:%.1f%%",
                     home, away,
                     result[(home,away)]["home"]*100,
                     f"{result[(home,away)]['draw']*100:.1f}%" if result[(home,away)]["draw"] else "--",
                     result[(home,away)]["away"]*100)
        else:
            log.warning("  Missing home or away odds for %s vs %s — skipping.", home, away)

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
        log.warning("Could not read %s (%s)", path, e)
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
        try: os.unlink(tmp)
        except OSError: pass
        raise


def pct(v) -> str:
    return f"{round(v * 100, 1)}%" if v is not None else "--"


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Kalshi ingestion starting ===")

    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY:
        log.error("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY must be set.")
        raise SystemExit(1)

    tournament_probs = fetch_tournament_winner_probs()
    match_odds       = fetch_match_odds()

    if not tournament_probs and not match_odds:
        log.warning("No data returned from Kalshi — leaving data.json unchanged.")
        raise SystemExit(0)

    existing = load_existing(OUTPUT_FILE)
    matches  = existing.get("matches", [])
    updated  = 0

    for match in matches:
        home = match.get("home", "")
        away = match.get("away", "")
        changed = False

        # ── Tournament winner probs ────────────────────────────────────
        if tournament_probs.get(home) is not None:
            match["kalshiWinProbHome"] = pct(tournament_probs[home])
            changed = True
        if tournament_probs.get(away) is not None:
            match["kalshiWinProbAway"] = pct(tournament_probs[away])
            changed = True

        # ── Match-level 1X2 odds ───────────────────────────────────────
        odds = match_odds.get((home, away))
        # Try reversed (in case Kalshi has them flipped)
        if odds is None:
            rev = match_odds.get((away, home))
            if rev:
                odds = {
                    "home": rev["away"],
                    "draw": rev["draw"],
                    "away": rev["home"],
                }

        if odds:
            kalshi_layer = {
                "source": "Prediction Markets (Kalshi)",
                "fav":    pct(odds["home"]),
                "draw":   pct(odds["draw"]),
                "und":    pct(odds["away"]),
            }
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
            changed = True

        if changed:
            updated += 1

    now = datetime.now(timezone.utc)
    existing["lastUpdated"]    = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
    existing["kalshiUpdated"]  = now.strftime("%B %-d, %Y · %-I:%M %p UTC")

    atomic_write(OUTPUT_FILE, existing)
    log.info("=== Done. Updated %d match records. ===", updated)


if __name__ == "__main__":
    main()
