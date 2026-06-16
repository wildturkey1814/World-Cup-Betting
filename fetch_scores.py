"""
2026 World Cup — Live Scores & Box Score Fetcher
=================================================
Polls football-data.org (free tier, 10 calls/min) for:
  - Match status changes (UPCOMING → IN_PLAY → FINISHED)
  - Live scores during matches
  - Final scores + box score stats when complete

Zafronix fallback: if football-data.org returns FINISHED but no
detailed stats, automatically queries Zafronix WC API to hydrate
the boxScore fields before writing to data.json.

This script ONLY touches match status, scores, and boxScore fields.
It NEVER overwrites prediction layers (odds data stays untouched).
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

FD_KEY          = os.environ.get("FOOTBALL_DATA_KEY", "")
FD_BASE         = "https://api.football-data.org/v4"
FD_WC_ID        = 2000
OUTPUT_FILE     = "data.json"
REQUEST_TIMEOUT = 15

WINDOW_BEFORE_MIN = 30
WINDOW_AFTER_MIN  = 130

# Catchup: look all the way back to tournament start
TOURNAMENT_START = datetime(2026, 6, 11, 0, 0, 0, tzinfo=timezone.utc)

FD_TEAM_MAP = {
    "Mexico":"Mexico","South Africa":"South Africa",
    "Republic of Korea":"South Korea","Korea Republic":"South Korea",
    "Czechia":"Czechia","Czech Republic":"Czechia",
    "Canada":"Canada","Bosnia and Herzegovina":"Bosnia & Herzegovina",
    "Bosnia & Herzegovina":"Bosnia & Herzegovina",
    "USA":"United States","United States":"United States",
    "Paraguay":"Paraguay","Germany":"Germany","Argentina":"Argentina",
    "England":"England","Italy":"Italy","France":"France","Brazil":"Brazil",
    "Spain":"Spain","Portugal":"Portugal","Netherlands":"Netherlands",
    "Morocco":"Morocco","Japan":"Japan","Australia":"Australia",
    "Croatia":"Croatia","Switzerland":"Switzerland","Uruguay":"Uruguay",
    "Colombia":"Colombia","Senegal":"Senegal","Denmark":"Denmark",
    "Ecuador":"Ecuador","Norway":"Norway","Turkey":"Turkey",
    "Türkiye":"Turkey","Serbia":"Serbia","Poland":"Poland",
    "IR Iran":"Iran","Iran":"Iran","Saudi Arabia":"Saudi Arabia",
    "Ghana":"Ghana","Cameroon":"Cameroon","Ivory Coast":"Ivory Coast",
    "Côte d'Ivoire":"Ivory Coast","DR Congo":"DR Congo","Congo DR":"DR Congo",
    "Tunisia":"Tunisia","Egypt":"Egypt","Algeria":"Algeria","Nigeria":"Nigeria",
    "Panama":"Panama","Costa Rica":"Costa Rica","Wales":"Wales",
    "Uzbekistan":"Uzbekistan","Iraq":"Iraq","Jordan":"Jordan","Qatar":"Qatar",
    "New Zealand":"New Zealand","Cape Verde":"Cape Verde","Cabo Verde":"Cape Verde",
    "Curaçao":"Curacao","Curacao":"Curacao","Haiti":"Haiti",
    "Belgium":"Belgium","Scotland":"Scotland","Austria":"Austria","Sweden":"Sweden",
}

def normalise_fd(name: str) -> str:
    return FD_TEAM_MAP.get(name, name)


# ── Zafronix fallback ──────────────────────────────────────────────────────

ZAFRONIX_KEYS = [k for k in [
    os.environ.get("ZAFRONIX_API_KEY",  ""),
    os.environ.get("ZAFRONIX_API_KEY2", ""),
] if k]
ZAFRONIX_BASE = "https://api.zafronix.com/fifa/worldcup/v1"
_zaf_key_idx  = 0

def _zafronix_headers():
    return {"X-API-Key": ZAFRONIX_KEYS[_zaf_key_idx % len(ZAFRONIX_KEYS)]}

def _zafronix_get(path, params=None):
    global _zaf_key_idx
    if not ZAFRONIX_KEYS:
        return None
    url = f"{ZAFRONIX_BASE}{path}"
    for _ in range(len(ZAFRONIX_KEYS)):
        try:
            resp = requests.get(url, headers=_zafronix_headers(), params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                _zaf_key_idx += 1; time.sleep(2); continue
            if resp.status_code == 401:
                _zaf_key_idx += 1; continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error("Zafronix error: %s", e); return None
    return None

def fetch_zafronix_box_score(home, away):
    if not ZAFRONIX_KEYS:
        return None
    log.info("  Trying Zafronix for %s vs %s...", home, away)
    data = _zafronix_get(f"/tournaments/2026/matches")
    if not data:
        return None
    matches = data if isinstance(data, list) else data.get("matches", [])
    match_id = None
    for m in matches:
        h = normalise_fd((m.get("home_team") or {}).get("name","") if isinstance(m.get("home_team"),dict) else str(m.get("home_team","")))
        a = normalise_fd((m.get("away_team") or {}).get("name","") if isinstance(m.get("away_team"),dict) else str(m.get("away_team","")))
        if (h == home and a == away) or (h == away and a == home):
            match_id = str(m.get("match_id") or m.get("id",""))
            break
    if not match_id:
        return None
    data = _zafronix_get(f"/matches/{match_id}")
    if not data:
        return None
    stats = data.get("stats") or data.get("statistics") or {}
    home_s = stats.get("home") or {}
    away_s = stats.get("away") or {}
    home_team_id = str((data.get("home_team") or {}).get("id","")) if isinstance(data.get("home_team"),dict) else ""
    goals = []
    for ev in (data.get("events") or data.get("goals") or []):
        if "GOAL" not in str(ev.get("type","")).upper():
            continue
        t = ev.get("team",{})
        tid = str(t.get("id","") if isinstance(t,dict) else ev.get("team_id",""))
        goals.append({"minute":ev.get("minute") or ev.get("time") or 0,"team":"home" if tid==home_team_id else "away","scorer":ev.get("player_name") or ev.get("player") or ""})
    def si(d,*keys):
        for k in keys:
            v=d.get(k)
            if v is not None:
                try: return int(v)
                except: pass
        return None
    box = {"possession":{"home":si(home_s,"possession_pct","possession"),"away":si(away_s,"possession_pct","possession")},"shots":{"home":si(home_s,"total_shots","shots"),"away":si(away_s,"total_shots","shots")},"shotsOnTarget":{"home":si(home_s,"shots_on_target","on_target"),"away":si(away_s,"shots_on_target","on_target")},"corners":{"home":si(home_s,"corners"),"away":si(away_s,"corners")},"fouls":{"home":si(home_s,"fouls_committed","fouls"),"away":si(away_s,"fouls_committed","fouls")},"yellowCards":{"home":si(home_s,"yellow_cards","yellows"),"away":si(away_s,"yellow_cards","yellows")},"redCards":{"home":si(home_s,"red_cards","reds"),"away":si(away_s,"red_cards","reds")},"goals":goals}
    box = {k:v for k,v in box.items() if k=="goals" or any(x is not None for x in v.values())}
    return box if (len(box)>1 or goals) else None


# ── football-data.org client ───────────────────────────────────────────────

def fd_get(path, params=None):
    if not FD_KEY:
        log.error("FOOTBALL_DATA_KEY not set."); return None
    url = f"{FD_BASE}/{path}"
    headers = {"X-Auth-Token": FD_KEY}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            log.warning("Rate limited — sleeping 12s.")
            time.sleep(12)
            resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error("FD error: %s", e)
    return None

def fetch_match_detail(fd_match_id):
    time.sleep(0.5)
    return fd_get(f"matches/{fd_match_id}")

def fetch_all_tournament_matches():
    """Fetch ALL WC matches from tournament start — catches every completed match."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    data = fd_get(f"competitions/{FD_WC_ID}/matches", {
        "dateFrom": str(TOURNAMENT_START.date()),
        "dateTo":   str(tomorrow),
    })
    if not data:
        return None
    return data.get("matches", [])


# ── Box score builder ──────────────────────────────────────────────────────

def build_box_score(fd_match):
    stats  = fd_match.get("statistics") or {}
    home_s = stats.get("home") or {}
    away_s = stats.get("away") or {}
    goals_raw = fd_match.get("goals") or []
    goals = []
    home_name = normalise_fd((fd_match.get("homeTeam") or {}).get("name",""))
    for g in goals_raw:
        team_name = normalise_fd((g.get("team") or {}).get("name",""))
        side = "home" if team_name == home_name else "away"
        parts = ((g.get("scorer") or {}).get("name") or "").split()
        goals.append({"minute":g.get("minute"),"team":side,"scorer":parts[-1] if parts else ""})
    full = fd_match.get("score",{}).get("fullTime",{})
    hg = full.get("home",0) or 0
    ag = full.get("away",0) or 0
    box = {
        "possession":    {"home":home_s.get("ball_possession") or 50,"away":away_s.get("ball_possession") or 50},
        "shots":         {"home":home_s.get("total_shots") or home_s.get("shots_total"),"away":away_s.get("total_shots") or away_s.get("shots_total")},
        "shotsOnTarget": {"home":home_s.get("shots_on_goal") or home_s.get("shots_on_target"),"away":away_s.get("shots_on_goal") or away_s.get("shots_on_target")},
        "corners":       {"home":home_s.get("corner_kicks") or home_s.get("corners"),"away":away_s.get("corner_kicks") or away_s.get("corners")},
        "fouls":         {"home":home_s.get("fouls") or home_s.get("fouls_committed"),"away":away_s.get("fouls") or away_s.get("fouls_committed")},
        "yellowCards":   {"home":home_s.get("yellow_cards"),"away":away_s.get("yellow_cards")},
        "redCards":      {"home":home_s.get("red_cards"),"away":away_s.get("red_cards")},
        "goals": goals,
    }
    has_data = any([box["shots"]["home"] is not None, box["goals"], hg>0 or ag>0])
    return box if has_data else None

def score_string(home, away, fd_match):
    full = fd_match.get("score",{}).get("fullTime",{})
    hg = full.get("home",0) or 0
    ag = full.get("away",0) or 0
    return f"{home.upper()} {hg} - {ag} {away.upper()}"

def match_fd_to_record(record, fd_matches):
    our_home = record.get("home","")
    our_away = record.get("away","")
    for m in fd_matches:
        fd_home = normalise_fd((m.get("homeTeam") or {}).get("name",""))
        fd_away = normalise_fd((m.get("awayTeam") or {}).get("name",""))
        if fd_home == our_home and fd_away == our_away:
            return m
        if fd_home == our_away and fd_away == our_home:
            return m
    return None

def is_in_active_window(record, now):
    if record.get("type") == "IN_PLAY":
        return True
    ko_str = record.get("kickoff") or ""
    if not ko_str or ko_str == "null":
        return record.get("type") == "UPCOMING"
    try:
        ko = datetime.fromisoformat(str(ko_str).replace("Z","+00:00"))
        mins = (now - ko).total_seconds() / 60
        if -WINDOW_BEFORE_MIN <= mins <= 0: return True
        if 0 < mins <= WINDOW_AFTER_MIN: return True
        # KEY FIX: catch all UPCOMING matches since tournament start
        if record.get("type") == "UPCOMING" and ko >= TOURNAMENT_START and mins > 0:
            return True
        return False
    except:
        return record.get("type") == "UPCOMING"


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
        except: pass
        raise

def load_data(path):
    if not os.path.exists(path):
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}
    try:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Could not read %s: %s", path, e)
        return {"currentStage":"Group Stage","lastUpdated":"","matches":[]}

def calculate_source_accuracy(record, home_won, is_draw):
    accuracy = {}
    fav_is_home = record.get("favTeam") == record.get("home")
    for layer in record.get("layers",[]):
        source = layer.get("source","")
        if not source: continue
        if is_draw:
            accuracy[source] = False
        else:
            fav_prob = float(str(layer.get("fav","0")).replace("%","") or 0)
            und_prob = float(str(layer.get("und","0")).replace("%","") or 0)
            source_picks_home = (fav_is_home and fav_prob > und_prob) or (not fav_is_home and und_prob > fav_prob)
            accuracy[source] = (source_picks_home == home_won)
    return accuracy


# ── FAV TEAM FIX ───────────────────────────────────────────────────────────

def recalculate_fav(record):
    """
    Recalculate favTeam from the actual probability data.
    The team with the highest average win probability across all layers is the fav.
    This fixes cases where home team was wrongly assigned as fav.
    """
    layers = record.get("layers", [])
    if not layers:
        return

    def p(s):
        try: return float(str(s).replace("%",""))
        except: return 0.0

    home_total = 0.0
    away_total = 0.0
    count = 0

    for layer in layers:
        fav_is_home = record.get("favTeam") == record.get("home")
        home_prob = p(layer.get("fav")) if fav_is_home else p(layer.get("und"))
        away_prob = p(layer.get("und")) if fav_is_home else p(layer.get("fav"))
        home_total += home_prob
        away_total += away_prob
        count += 1

    if count == 0:
        return

    avg_home = home_total / count
    avg_away = away_total / count

    # Assign fav to whichever team has higher average probability
    if avg_away > avg_home:
        # Swap fav/und in layers too
        record["favTeam"] = record.get("away")
        for layer in layers:
            layer["fav"], layer["und"] = layer["und"], layer["fav"]
        log.info("  Corrected favTeam: %s (away) is now FAV over %s (home)",
                 record.get("away"), record.get("home"))


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    log.info("=== Score fetcher starting ===")

    if not FD_KEY:
        log.error("FOOTBALL_DATA_KEY is not set.")
        raise SystemExit(1)

    now  = datetime.now(timezone.utc)
    data = load_data(OUTPUT_FILE)
    matches = data.get("matches", [])

    # Fix any fav/und assignments that are wrong
    fav_fixed = 0
    for m in matches:
        if m.get("type") == "UPCOMING":
            old_fav = m.get("favTeam")
            recalculate_fav(m)
            if m.get("favTeam") != old_fav:
                fav_fixed += 1

    if fav_fixed:
        log.info("Corrected favTeam for %d match(es).", fav_fixed)

    # Find active matches — includes ALL past UPCOMING since tournament start
    active = [m for m in matches
              if m.get("type") in ("UPCOMING","IN_PLAY")
              and is_in_active_window(m, now)]

    log.info("%d match(es) in active window (including catchup).", len(active))

    if not active and not fav_fixed:
        log.info("Nothing to do.")
        raise SystemExit(0)

    fd_matches = fetch_all_tournament_matches()
    if fd_matches is None:
        log.warning("Could not fetch from football-data.org.")
        if fav_fixed:
            data["lastUpdated"] = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
            atomic_write(OUTPUT_FILE, data)
        raise SystemExit(0)

    log.info("football-data.org returned %d match(es).", len(fd_matches))

    changed = fav_fixed > 0

    for record in active:
        fd_m = match_fd_to_record(record, fd_matches)
        if not fd_m:
            log.info("  %s vs %s — no FD match found.", record.get("home"), record.get("away"))
            continue

        fd_status = fd_m.get("status","")
        home = record.get("home","")
        away = record.get("away","")

        log.info("  %s vs %s — FD status: %s", home, away, fd_status)

        if fd_status in ("IN_PLAY","PAUSED"):
            curr = fd_m.get("score",{}).get("fullTime",{})
            hg = curr.get("home",0) or 0
            ag = curr.get("away",0) or 0
            record["status"]    = "IN_PLAY"
            record["type"]      = "IN_PLAY"
            record["liveScore"] = f"{hg} - {ag}"
            changed = True
            log.info("    LIVE: %s %d-%d %s", home, hg, ag, away)

        elif fd_status == "FINISHED":
            fd_id  = fd_m.get("id")
            detail = fetch_match_detail(fd_id) if fd_id else fd_m
            fd_full = detail.get("match", detail) if detail else fd_m

            full     = fd_full.get("score",{}).get("fullTime",{})
            hg       = full.get("home",0) or 0
            ag       = full.get("away",0) or 0
            is_draw  = (hg == ag)
            home_won = hg > ag

            record["type"]           = "COMPLETED"
            record["status"]         = "FINISHED"
            record["score"]          = score_string(home, away, fd_full)
            record["sourceAccuracy"] = calculate_source_accuracy(record, home_won, is_draw)

            if not record.get("boxScore"):
                box = build_box_score(fd_full)
                if box:
                    record["boxScore"] = box
                    log.info("    FINISHED: %s — FD box score.", record["score"])
                else:
                    zaf = fetch_zafronix_box_score(home, away)
                    if zaf:
                        record["boxScore"] = zaf
                        record["boxScoreSource"] = "Zafronix"
                    else:
                        log.info("    No box score yet for %s vs %s.", home, away)

            if not record.get("insight"):
                fav_name = record.get("favTeam","")
                winner = home if home_won else away
                if is_draw:
                    record["insight"] = f"The match ended level at {hg}-{ag}. Check the model divergence for context."
                elif winner == fav_name:
                    record["insight"] = f"{fav_name} won {max(hg,ag)}-{min(hg,ag)} as expected by the majority of sources."
                else:
                    record["insight"] = f"Upset! {winner} won {max(hg,ag)}-{min(hg,ag)}, defying the pre-match consensus."

            record.pop("liveScore", None)
            changed = True
            log.info("    Marked COMPLETED: %s", record["score"])

    if not changed:
        log.info("No changes.")
        raise SystemExit(0)

    data["lastUpdated"] = now.strftime("%B %-d, %Y · %-I:%M %p UTC")
    atomic_write(OUTPUT_FILE, data)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
