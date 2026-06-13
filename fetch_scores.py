"""
2026 World Cup — Live Scores & Box Score Fetcher
=================================================
Polls football-data.org (free tier, 10 calls/min) for:
  - Match status changes (UPCOMING → IN_PLAY → FINISHED)
  - Live scores during matches
  - Final scores + box score stats when complete

This script ONLY touches match status, scores, and boxScore fields.
It NEVER overwrites prediction layers (odds data stays untouched).

Triggers:
  - Runs every 5 minutes via GitHub Action
  - Exits immediately if no matches are live or recently finished
  - On quiet days with no upcoming matches, exits immediately

Run manually:   python fetch_scores.py
Requirements:   pip install requests
Environment:    FOOTBALL_DATA_KEY must be set
                (free at football-data.org — no call limit concerns)
"""

import os
import json
import logging
import tempfile
import shutil
import time
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

# ── Config ─────────────────────────────────────────────────────────────────
FD_KEY          = os.environ.get("FOOTBALL_DATA_KEY", "")
FD_BASE         = "https://api.football-data.org/v4"
FD_WC_ID        = 2000        # football-data.org competition ID for FIFA World Cup
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 15

# Match window: how long before/after kickoff to consider a match "active"
WINDOW_BEFORE_MIN = 30     # start watching 30 min before kickoff
WINDOW_AFTER_MIN  = 130    # live window ends 130 min after kickoff
CATCHUP_HOURS     = 48     # also check any UPCOMING match whose kickoff
                           # was within the past 48 hours (catches missed matches)

# Team name normalisation — football-data.org uses full official names
FD_TEAM_MAP = {
    "Mexico":                       "Mexico",
    "South Africa":                 "South Africa",
    "Republic of Korea":            "South Korea",
    "Korea Republic":               "South Korea",
    "Czechia":                      "Czechia",
    "Czech Republic":               "Czechia",
    "Canada":                       "Canada",
    "Bosnia and Herzegovina":       "Bosnia & Herzegovina",
    "Bosnia & Herzegovina":         "Bosnia & Herzegovina",
    "USA":                          "United States",
    "United States":                "United States",
    "Paraguay":                     "Paraguay",
    "Germany":                      "Germany",
    "Argentina":                    "Argentina",
    "England":                      "England",
    "Italy":                        "Italy",
    "France":                       "France",
    "Brazil":                       "Brazil",
    "Spain":                        "Spain",
    "Portugal":                     "Portugal",
    "Netherlands":                  "Netherlands",
    "Morocco":                      "Morocco",
    "Japan":                        "Japan",
    "Australia":                    "Australia",
    "Croatia":                      "Croatia",
    "Switzerland":                  "Switzerland",
    "Uruguay":                      "Uruguay",
    "Colombia":                     "Colombia",
    "Senegal":                      "Senegal",
    "Denmark":                      "Denmark",
    "Ecuador":                      "Ecuador",
    "Norway":                       "Norway",
    "Turkey":                       "Turkey",
    "Türkiye":                      "Turkey",
    "Serbia":                       "Serbia",
    "Poland":                       "Poland",
    "IR Iran":                      "Iran",
    "Iran":                         "Iran",
    "Saudi Arabia":                 "Saudi Arabia",
    "Ghana":                        "Ghana",
    "Cameroon":                     "Cameroon",
    "Ivory Coast":                  "Ivory Coast",
    "Côte d'Ivoire":                "Ivory Coast",
    "DR Congo":                     "DR Congo",
    "Congo DR":                     "DR Congo",
    "Tunisia":                      "Tunisia",
    "Egypt":                        "Egypt",
    "Algeria":                      "Algeria",
    "Nigeria":                      "Nigeria",
    "Panama":                       "Panama",
    "Costa Rica":                   "Costa Rica",
    "Wales":                        "Wales",
    "Uzbekistan":                   "Uzbekistan",
    "Iraq":                         "Iraq",
    "Jordan":                       "Jordan",
    "Qatar":                        "Qatar",
    "New Zealand":                  "New Zealand",
    "Cape Verde":                   "Cape Verde",
    "Curaçao":                      "Curacao",
    "Curacao":                      "Curacao",
    "Haiti":                        "Haiti",
    "Belgium":                      "Belgium",
    "Scotland":                     "Scotland",
    "Austria":                      "Austria",
    "Sweden":                       "Sweden",
}

def normalise_fd(name: str) -> str:
    return FD_TEAM_MAP.get(name, name)


# ── API helpers ─────────────────────────────────────────────────────────────

def fd_get(path: str, params: dict = None) -> Optional[dict]:
    """GET from football-data.org with error isolation."""
    if not FD_KEY:
        log.error("FOOTBALL_DATA_KEY not set.")
        return None
    url = f"{FD_BASE}/{path}"
    headers = {"X-Auth-Token": FD_KEY}
    try:
        resp = requests.get(url, headers=headers, params=params or {},
                            timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            log.warning("Rate limited — sleeping 12s.")
            time.sleep(12)
            resp = requests.get(url, headers=headers, params=params or {},
                                timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        log.error("HTTP %s: %s", e.response.status_code, url)
    except requests.exceptions.Timeout:
        log.error("Timeout: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("Request error: %s", e)
    except ValueError:
        log.error("Non-JSON from %s", url)
    return None


def fetch_match_detail(fd_match_id: int) -> Optional[dict]:
    """Fetch full match detail including stats."""
    time.sleep(0.5)   # stay within 10 calls/min free tier
    return fd_get(f"matches/{fd_match_id}")


def fetch_today_matches() -> Optional[list]:
    """Fetch all World Cup matches from the past 3 days to catch any missed completions."""
    now   = datetime.now(timezone.utc)
    three_days_ago = (now - timedelta(days=3)).date()
    tomorrow = (now + timedelta(days=1)).date()
    data  = fd_get(f"competitions/{FD_WC_ID}/matches", {
        "dateFrom": str(three_days_ago),
        "dateTo":   str(tomorrow),
    })
    if not data:
        return None
    return data.get("matches", [])


# ── Box score builder ────────────────────────────────────────────────────────

def build_box_score(fd_match: dict) -> Optional[dict]:
    """
    Extract box score from a football-data.org match detail response.
    Returns None if stats aren't available yet.
    """
    stats  = fd_match.get("statistics") or {}
    home_s = stats.get("home") or {}
    away_s = stats.get("away") or {}

    goals_raw = fd_match.get("goals") or []
    goals = []
    home_name = normalise_fd(
        (fd_match.get("homeTeam") or {}).get("name", ""))
    for g in goals_raw:
        team_name = normalise_fd(
            (g.get("team") or {}).get("name", ""))
        side = "home" if team_name == home_name else "away"
        scorer = ""
        scorer_obj = g.get("scorer") or {}
        if scorer_obj.get("name"):
            # Shorten to last name for display
            parts = scorer_obj["name"].split()
            scorer = parts[-1] if parts else scorer_obj["name"]
        goals.append({
            "minute": g.get("minute"),
            "team":   side,
            "scorer": scorer,
        })

    score = fd_match.get("score", {})
    full  = score.get("fullTime", {})
    h_goals = full.get("home", 0) or 0
    a_goals = full.get("away", 0) or 0

    box = {
        "possession": {
            "home": home_s.get("ball_possession") or home_s.get("possession") or 50,
            "away": away_s.get("ball_possession") or away_s.get("possession") or 50,
        },
        "shots": {
            "home": home_s.get("total_shots") or home_s.get("shots_total"),
            "away": away_s.get("total_shots") or away_s.get("shots_total"),
        },
        "shotsOnTarget": {
            "home": home_s.get("shots_on_goal") or home_s.get("shots_on_target"),
            "away": away_s.get("shots_on_goal") or away_s.get("shots_on_target"),
        },
        "corners": {
            "home": home_s.get("corner_kicks") or home_s.get("corners"),
            "away": away_s.get("corner_kicks") or away_s.get("corners"),
        },
        "fouls": {
            "home": home_s.get("fouls") or home_s.get("fouls_committed"),
            "away": away_s.get("fouls") or away_s.get("fouls_committed"),
        },
        "yellowCards": {
            "home": home_s.get("yellow_cards"),
            "away": away_s.get("yellow_cards"),
        },
        "redCards": {
            "home": home_s.get("red_cards"),
            "away": away_s.get("red_cards"),
        },
        "goals": goals,
    }

    # Only return if we have at least some real data
    has_data = any([
        box["shots"]["home"] is not None,
        box["goals"],
        h_goals > 0 or a_goals > 0,
    ])
    return box if has_data else None


def score_string(home: str, away: str, fd_match: dict) -> str:
    """Build the score string in our format: 'MEXICO 2 - 1 SOUTH AFRICA'."""
    full = fd_match.get("score", {}).get("fullTime", {})
    hg   = full.get("home", 0) or 0
    ag   = full.get("away", 0) or 0
    return f"{home.upper()} {hg} - {ag} {away.upper()}"


# ── Match the data.json record to a football-data match ─────────────────────

def match_fd_to_record(record: dict, fd_matches: list) -> Optional[dict]:
    """
    Find the football-data.org match entry corresponding to our data.json record.
    Matches by home/away team name normalisation.
    """
    our_home = record.get("home", "")
    our_away = record.get("away", "")
    for m in fd_matches:
        fd_home = normalise_fd((m.get("homeTeam") or {}).get("name", ""))
        fd_away = normalise_fd((m.get("awayTeam") or {}).get("name", ""))
        if fd_home == our_home and fd_away == our_away:
            return m
        # Also match reversed (rare but possible with neutral venues)
        if fd_home == our_away and fd_away == our_home:
            return m
    return None


# ── Active window check ──────────────────────────────────────────────────────

def is_in_active_window(record: dict, now: datetime) -> bool:
    """
    True if this match should be checked for score updates.
    If kickoff is missing or null, checks all UPCOMING matches
    (since the football-data.org API is free and generous).
    """
    ko_str = record.get("kickoff") or ""
    if not ko_str or ko_str == "null":
        # No kickoff time — check if UPCOMING (may already be done)
        return record.get("type") == "UPCOMING"

    try:
        ko = datetime.fromisoformat(str(ko_str).replace("Z", "+00:00"))
        mins_since_ko = (now - ko).total_seconds() / 60

        # Pre-match window
        if -WINDOW_BEFORE_MIN <= mins_since_ko <= 0:
            return True
        # Live window
        if 0 < mins_since_ko <= WINDOW_AFTER_MIN:
            return True
        # Catchup: UPCOMING match whose kickoff was in the past 48 hours
        if record.get("type") == "UPCOMING" and 0 < mins_since_ko <= CATCHUP_HOURS * 60:
            return True
        return False
    except (ValueError, TypeError):
        # Can't parse kickoff — check all UPCOMING matches
        return record.get("type") == "UPCOMING"


# ── File helpers ──────────────────────────────────────────────────────────────

def atomic_write(path: str, data: dict) -> None:
    d  = os.path.dirname(os.path.abspath(path)) or "."
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


def load_data(path: str) -> dict:
    if not os.path.exists(path):
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s: %s", path, e)
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}


# ── Source accuracy audit ─────────────────────────────────────────────────────

def calculate_source_accuracy(record: dict, home_won: bool, is_draw: bool) -> dict:
    """
    For each prediction layer, check if the source's favourite call was correct.
    home_won: True if home team won, False if away won, is_draw overrides both.
    """
    accuracy = {}
    fav_is_home = record.get("favTeam") == record.get("home")

    for layer in record.get("layers", []):
        source = layer.get("source", "")
        if not source:
            continue
        if is_draw:
            # Draw: favourite prediction was wrong for all sources
            accuracy[source] = False
        else:
            # Check if source's favourite actually won
            fav_prob  = float(str(layer.get("fav",  "0")).replace("%","") or 0)
            und_prob  = float(str(layer.get("und",  "0")).replace("%","") or 0)
            # Source predicted home as fav if fav_prob > und_prob and fav is home
            source_picks_home = (fav_is_home and fav_prob > und_prob) or \
                                (not fav_is_home and und_prob > fav_prob)
            accuracy[source] = (source_picks_home == home_won)

    return accuracy


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Score fetcher starting ===")

    if not FD_KEY:
        log.error("FOOTBALL_DATA_KEY is not set.")
        raise SystemExit(1)

    now  = datetime.now(timezone.utc)
    data = load_data(OUTPUT_FILE)
    matches = data.get("matches", [])

    # Find records that are in the active watch window
    active = [m for m in matches
              if m.get("type") in ("UPCOMING", "IN_PLAY")
              and is_in_active_window(m, now)]

    if not active:
        log.info("No matches in active window — nothing to do.")
        raise SystemExit(0)

    log.info("%d match(es) in active window.", len(active))

    # Fetch today's World Cup matches from football-data.org
    fd_matches = fetch_today_matches()
    if fd_matches is None:
        log.warning("Could not fetch from football-data.org — keeping existing data.")
        raise SystemExit(0)

    log.info("football-data.org returned %d match(es).", len(fd_matches))

    changed = False

    for record in active:
        fd_m = match_fd_to_record(record, fd_matches)
        if not fd_m:
            log.info("  %s vs %s — no FD match found yet.",
                     record.get("home"), record.get("away"))
            continue

        fd_status = fd_m.get("status", "")
        home      = record.get("home", "")
        away      = record.get("away", "")

        log.info("  %s vs %s — FD status: %s", home, away, fd_status)

        if fd_status == "IN_PLAY" or fd_status == "PAUSED":
            # Update live score
            curr = fd_m.get("score", {}).get("fullTime", {})
            hg   = curr.get("home", 0) or 0
            ag   = curr.get("away", 0) or 0
            record["status"]    = "IN_PLAY"
            record["liveScore"] = f"{hg} - {ag}"
            changed = True
            log.info("    LIVE: %s %d - %d %s", home, hg, ag, away)

        elif fd_status == "FINISHED":
            # Fetch full detail for box score
            fd_id  = fd_m.get("id")
            detail = fetch_match_detail(fd_id) if fd_id else fd_m
            fd_match_full = detail.get("match", detail) if detail else fd_m

            # Build score string
            full   = fd_match_full.get("score", {}).get("fullTime", {})
            hg     = full.get("home", 0) or 0
            ag     = full.get("away", 0) or 0
            is_draw = (hg == ag)
            home_won = hg > ag

            record["type"]   = "COMPLETED"
            record["status"] = "FINISHED"
            record["score"]  = score_string(home, away, fd_match_full)
            record["sourceAccuracy"] = calculate_source_accuracy(
                record, home_won, is_draw)

            box = build_box_score(fd_match_full)
            if box:
                record["boxScore"] = box
                log.info("    FINISHED: %s — box score captured.", record["score"])
            else:
                log.info("    FINISHED: %s — no stats available yet.", record["score"])

            # Generate insight
            if not record.get("insight"):
                if is_draw:
                    record["insight"] = f"The match ended level at {hg}-{ag}."
                elif home_won:
                    record["insight"] = f"{home} won {hg}-{ag}."
                else:
                    record["insight"] = f"{away} won {ag}-{hg}."

            # Remove live indicator
            record.pop("liveScore", None)
            changed = True

    if not changed:
        log.info("No status changes — data.json unchanged.")
        raise SystemExit(0)

    data["lastUpdated"] = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
    atomic_write(OUTPUT_FILE, data)
    log.info("=== Done — updated %d match(es) ===",
             sum(1 for m in active if m.get("status") == "FINISHED"))


if __name__ == "__main__":
    main()
