"""
Debug script — dumps raw Kalshi API responses to identify correct series tickers.
Run once, check the logs, then remove.
"""

import os
import json
import time
import base64
import logging
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

KALSHI_API_KEY_ID  = os.environ.get("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY = os.environ.get("KALSHI_PRIVATE_KEY", "")
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

def build_auth_headers(method, path):
    timestamp_ms = str(int(time.time() * 1000))
    msg = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
    pem = KALSHI_PRIVATE_KEY.replace("\\n", "\n").strip()
    if "-----" not in pem:
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem}\n-----END RSA PRIVATE KEY-----"
    private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    sig = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type": "application/json",
    }

def kalshi_get(path, params=None):
    headers = build_auth_headers("GET", path)
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    log.info("GET %s → HTTP %d", path, resp.status_code)
    return resp.json() if resp.ok else {"error": resp.text}

# 1. Check exchange status (no auth needed, confirms connectivity)
log.info("=== Checking exchange status ===")
r = requests.get(f"{BASE_URL}/exchange/status", timeout=10)
log.info("Exchange status: %s", r.json())

# 2. List ALL series — find World Cup ones
log.info("=== Fetching series list ===")
data = kalshi_get("/series", {"limit": 200})
series_list = data.get("series", data if isinstance(data, list) else [])
wc_series = [s for s in series_list if isinstance(s, dict) and
             any(kw in str(s).upper() for kw in ["WORLD","SOCCER","FIFA","WC","FOOTBALL"])]
log.info("World Cup related series: %s", json.dumps(wc_series, indent=2))

# 3. Try fetching first page of markets with no filter — see what's there
log.info("=== Fetching first 5 open markets (no filter) ===")
data = kalshi_get("/markets", {"limit": 5, "status": "open"})
markets = data.get("markets", [])
for m in markets:
    log.info("  ticker=%s title=%s", m.get("ticker","?"), m.get("title","?")[:60])

# 4. Try the KXWCGAME series directly
log.info("=== Fetching KXWCGAME series info ===")
data = kalshi_get("/series/KXWCGAME")
log.info("KXWCGAME series: %s", json.dumps(data, indent=2)[:500])

# 5. Try fetching events with World Cup keyword
log.info("=== Fetching events (soccer) ===")
data = kalshi_get("/events", {"limit": 10, "series_ticker": "KXWCGAME"})
log.info("KXWCGAME events: %s", json.dumps(data, indent=2)[:1000])

# 6. Try fetching markets by event ticker directly
log.info("=== Fetching markets for known event KXWCGAME-26JUN12USAPAR ===")
data = kalshi_get("/markets", {"event_ticker": "KXWCGAME-26JUN12USAPAR"})
log.info("Markets for USA vs PAR: %s", json.dumps(data, indent=2)[:1000])

