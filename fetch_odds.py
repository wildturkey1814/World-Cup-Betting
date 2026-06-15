"""
2026 World Cup Prediction Engine — Event-Driven Ingestion Script
================================================================
Fetches ALL World Cup fixtures + odds in a SINGLE API call per run,
but only triggers that call when something meaningful is about to
happen or just finished.

Player headshot images are served via statically.io CDN in index.html
— no downloading or file storage needed here.
"""

import os
import json
import math
import time
import logging
import tempfile
import shutil
from datetime import datetime, timezone, timedelta

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
