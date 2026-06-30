"""
Live Match Data Clients
=======================
Three clients used by fetch_scores.py and the frontend:

  1. ZafronixClient   — post-match box score fallback
  2. APIFootballClient — live events (goals/cards/subs) every 2 min
  3. ESPNClient        — live score/minute every 60s (keyless)

These are imported by fetch_scores.py, not run directly.
"""

import os
import time
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 15

# ── Team name normalisation (shared) ──────────────────────────────────────

TEAM_NORM = {
    "United States": "United States", "USA": "United States",
    "South Korea": "South Korea", "Korea Republic": "South Korea",
    "Iran": "Iran", "IR Iran": "Iran",
    "Bosnia & Herzegovina": "Bosnia & Herzegovina",
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Ivory Coast": "Ivory Coast", "Côte d'Ivoire": "Ivory Coast",
    "DR Congo": "DR Congo", "Congo DR": "DR Congo",
    "Czechia": "Czechia", "Czech Republic": "Czechia",
    "Turkey": "Turkey", "Türkiye": "Turkey",
    "Cape Verde": "Cape Verde",
    "Curacao": "Curacao", "Curaçao": "Curacao", "Cura\u00e7ao": "Curacao",
}

def norm(name: str) -> str:
    return TEAM_NORM.get(name.strip(), name.strip())


# ════════════════════════════════════════════════════════════════════════════
#  1. ZAFRONIX — post-match box score fallback
# ════════════════════════════════════════════════════════════════════════════

class ZafronixClient:
    """
    Fetches detailed post-match box scores from Zafronix WC API.
    Used as a fallback when football-data.org returns FINISHED
    without detailed stats.

    Secrets: ZAFRONIX_API_KEY, ZAFRONIX_API_KEY2
    Base URL: https://api.zafronix.com/fifa/worldcup/v1
    """

    BASE = "https://api.zafronix.com/fifa/worldcup/v1"
    KEYS = [k for k in [
        os.environ.get("ZAFRONIX_API_KEY",  ""),
        os.environ.get("ZAFRONIX_API_KEY2", ""),
    ] if k]

    def __init__(self):
        self._key_idx = 0

    def _headers(self) -> dict:
        if not self.KEYS:
            raise ValueError("No ZAFRONIX_API_KEY set.")
        return {"X-API-Key": self.KEYS[self._key_idx % len(self.KEYS)]}

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.BASE}{path}"
        for attempt in range(len(self.KEYS) or 1):
            try:
                resp = requests.get(
                    url, headers=self._headers(),
                    params=params, timeout=REQUEST_TIMEOUT
                )
                if resp.status_code == 429:
                    log.warning("Zafronix rate limited — rotating key.")
                    self._key_idx += 1
                    time.sleep(2)
                    continue
                if resp.status_code == 401:
                    log.error("Zafronix 401 — check ZAFRONIX_API_KEY.")
                    self._key_idx += 1
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.error("Zafronix request failed: %s", e)
                return None
        return None

    def find_match_id(self, home: str, away: str,
                      year: int = 2026) -> Optional[str]:
        """Find Zafronix match_id by team name matching."""
        data = self._get(f"/tournaments/{year}/matches")
        if not data:
            return None
        matches = data if isinstance(data, list) else data.get("matches", [])
        for m in matches:
            h = norm(m.get("home_team", {}).get("name", "")
                     if isinstance(m.get("home_team"), dict)
                     else str(m.get("home_team", "")))
            a = norm(m.get("away_team", {}).get("name", "")
                     if isinstance(m.get("away_team"), dict)
                     else str(m.get("away_team", "")))
            if (h == norm(home) and a == norm(away)) or \
               (h == norm(away) and a == norm(home)):
                mid = m.get("match_id") or m.get("id")
                log.info("Zafronix match_id=%s for %s vs %s", mid, home, away)
                return str(mid)
        log.warning("Zafronix: no match found for %s vs %s", home, away)
        return None

    def get_box_score(self, home: str, away: str,
                      year: int = 2026) -> Optional[dict]:
        """
        Returns a box_score dict matching our data.json schema, or None.
        Tries both /matches/{id} and /matches/{id}/stats endpoints.
        """
        match_id = self.find_match_id(home, away, year)
        if not match_id:
            return None

        data = self._get(f"/matches/{match_id}")
        if not data:
            return None

        # Zafronix may nest stats under different keys — try both
        stats = data.get("stats") or data.get("statistics") or {}
        home_s = stats.get("home") or stats.get(home) or {}
        away_s = stats.get("away") or stats.get(away) or {}

        # Events → goals
        events = data.get("events") or data.get("goals") or []
        home_team_id = (data.get("home_team", {}).get("id")
                        if isinstance(data.get("home_team"), dict)
                        else data.get("home_team_id"))

        goals = []
        for ev in events:
            ev_type = str(ev.get("type", "")).upper()
            if "GOAL" not in ev_type:
                continue
            team_id = ev.get("team_id") or ev.get("team", {}).get("id") \
                      if isinstance(ev.get("team"), dict) else ev.get("team_id")
            team_side = "home" if str(team_id) == str(home_team_id) else "away"
            goals.append({
                "minute": ev.get("minute") or ev.get("time") or 0,
                "team":   team_side,
                "scorer": ev.get("player_name") or ev.get("player") or "",
            })

        def safe_int(d, *keys):
            for k in keys:
                v = d.get(k)
                if v is not None:
                    try: return int(v)
                    except (ValueError, TypeError): pass
            return None

        box = {
            "possession": {
                "home": safe_int(home_s, "possession_pct", "possession"),
                "away": safe_int(away_s, "possession_pct", "possession"),
            },
            "shots": {
                "home": safe_int(home_s, "total_shots", "shots"),
                "away": safe_int(away_s, "total_shots", "shots"),
            },
            "shotsOnTarget": {
                "home": safe_int(home_s, "shots_on_target", "on_target"),
                "away": safe_int(away_s, "shots_on_target", "on_target"),
            },
            "corners": {
                "home": safe_int(home_s, "corners"),
                "away": safe_int(away_s, "corners"),
            },
            "fouls": {
                "home": safe_int(home_s, "fouls_committed", "fouls"),
                "away": safe_int(away_s, "fouls_committed", "fouls"),
            },
            "yellowCards": {
                "home": safe_int(home_s, "yellow_cards", "yellows"),
                "away": safe_int(away_s, "yellow_cards", "yellows"),
            },
            "redCards": {
                "home": safe_int(home_s, "red_cards", "reds"),
                "away": safe_int(away_s, "red_cards", "reds"),
            },
            "goals": goals,
        }

        # Remove keys where both home and away are None (no data)
        box = {k: v for k, v in box.items()
               if k == "goals" or
               any(x is not None for x in v.values())}

        if not box:
            log.warning("Zafronix: empty box score for %s vs %s", home, away)
            return None

        log.info("Zafronix box score fetched for %s vs %s (%d goals)",
                 home, away, len(goals))
        return box


# ════════════════════════════════════════════════════════════════════════════
#  2. API-FOOTBALL — live events every 2 minutes (client-side keys exposed)
# ════════════════════════════════════════════════════════════════════════════

class APIFootballClient:
    """
    Fetches live match events and statistics from api-sports.io.
    Used server-side only for pre/post-match; client-side JS handles
    live polling (keys are safely passed to the frontend key array).

    Secrets: API_FOOTBALL_KEY, API_FOOTBALL_KEY2
    Base URL: https://v3.football.api-sports.io
    League: 1 (FIFA World Cup), Season: 2026
    """

    BASE   = "https://v3.football.api-sports.io"
    LEAGUE = 1
    SEASON = 2026
    KEYS   = [k for k in [
        os.environ.get("API_FOOTBALL_KEY",  ""),
        os.environ.get("API_FOOTBALL_KEY2", ""),
    ] if k]

    def __init__(self):
        self._key_idx = 0

    def _headers(self) -> dict:
        if not self.KEYS:
            raise ValueError("No API_FOOTBALL_KEY set.")
        return {
            "x-rapidapi-key":  self.KEYS[self._key_idx % len(self.KEYS)],
            "x-rapidapi-host": "v3.football.api-sports.io",
        }

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.BASE}{path}"
        for attempt in range(len(self.KEYS) or 1):
            try:
                resp = requests.get(
                    url, headers=self._headers(),
                    params=params, timeout=REQUEST_TIMEOUT
                )
                if resp.status_code in (429, 403):
                    log.warning("API-Football key %d exhausted — rotating.",
                                self._key_idx + 1)
                    self._key_idx += 1
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                errors = data.get("errors", {})
                if errors:
                    log.warning("API-Football errors: %s", errors)
                    return None
                return data
            except requests.exceptions.RequestException as e:
                log.error("API-Football request failed: %s", e)
                return None
        return None

    def find_fixture_id(self, home: str, away: str) -> Optional[int]:
        """Find fixture ID by team name for the 2026 World Cup."""
        data = self._get("/fixtures", {
            "league": self.LEAGUE, "season": self.SEASON,
        })
        if not data:
            return None
        for fix in data.get("response", []):
            teams = fix.get("teams", {})
            h = norm(teams.get("home", {}).get("name", ""))
            a = norm(teams.get("away", {}).get("name", ""))
            if (h == norm(home) and a == norm(away)) or \
               (h == norm(away) and a == norm(home)):
                fid = fix.get("fixture", {}).get("id")
                log.info("API-Football fixture_id=%s for %s vs %s",
                         fid, home, away)
                return fid
        return None

    def get_live_events(self, fixture_id: int) -> list:
        """
        Returns list of events for a live/finished fixture.
        Each event: {minute, type, team, player, detail}
        """
        data = self._get("/fixtures/events",
                         {"fixture": fixture_id})
        if not data:
            return []
        events = []
        for ev in data.get("response", []):
            events.append({
                "minute": ev.get("time", {}).get("elapsed", 0),
                "type":   ev.get("type", ""),
                "detail": ev.get("detail", ""),
                "team":   ev.get("team", {}).get("name", ""),
                "player": ev.get("player", {}).get("name", ""),
                "assist": (ev.get("assist") or {}).get("name", ""),
            })
        return events

    def get_live_stats(self, fixture_id: int) -> dict:
        """
        Returns stats dict {home: {...}, away: {...}} for a live fixture.
        """
        data = self._get("/fixtures/statistics",
                         {"fixture": fixture_id})
        if not data:
            return {}
        result = {}
        for team_data in data.get("response", []):
            team_name = norm(team_data.get("team", {}).get("name", ""))
            side = "home" if len(result) == 0 else "away"
            stats_raw = {s["type"]: s["value"]
                         for s in team_data.get("statistics", [])}
            result[side] = {
                "team":        team_name,
                "possession":  _parse_pct(stats_raw.get("Ball Possession")),
                "shots":       _safe_int(stats_raw.get("Total Shots")),
                "shotsOnTarget": _safe_int(stats_raw.get("Shots on Goal")),
                "corners":     _safe_int(stats_raw.get("Corner Kicks")),
                "fouls":       _safe_int(stats_raw.get("Fouls")),
                "yellowCards": _safe_int(stats_raw.get("Yellow Cards")),
                "redCards":    _safe_int(stats_raw.get("Red Cards")),
                "offsides":    _safe_int(stats_raw.get("Offsides")),
                "saves":       _safe_int(stats_raw.get("Goalkeeper Saves")),
            }
        return result


# ════════════════════════════════════════════════════════════════════════════
#  3. ESPN — live score/status every 60s (completely keyless)
# ════════════════════════════════════════════════════════════════════════════

class ESPNClient:
    """
    Fetches live scores and match status from ESPN's undocumented
    public JSON endpoints. No API key required.

    Used as the primary 60-second heartbeat during live matches —
    gives us score + clock + match status without burning API-Football calls.

    Endpoint: https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard
    """

    BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.BASE}{path}"
        try:
            resp = requests.get(url, params=params,
                                timeout=REQUEST_TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error("ESPN request failed: %s", e)
            return None

    def get_live_scores(self) -> list:
        """
        Returns list of live/recent matches with score and status.
        Each item: {home, away, homeScore, awayScore, minute, status, espnId}
        """
        return self._parse_scoreboard(self._get("/scoreboard"))

    def fetch_finished_matches(
        self,
        date_from: str = "20260611",
        date_to: str = "20260720",
    ) -> list[dict]:
        """
        Fetch completed World Cup matches from ESPN's public scoreboard API.
        date_from/date_to: YYYYMMDD strings (inclusive range).
        """
        data = self._get("/scoreboard", {"dates": f"{date_from}-{date_to}"})
        if not data:
            return []
        finished = []
        for item in self._parse_scoreboard(data):
            if item.get("state") != "post":
                continue
            if item.get("homeScore") is None or item.get("awayScore") is None:
                continue
            finished.append(item)
        return finished

    def _parse_scoreboard(self, data: Optional[dict]) -> list:
        if not data:
            return []
        results = []
        for event in data.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors", [])
            status_obj  = competition.get("status", {})
            status_type = status_obj.get("type", {})

            home = next((c for c in competitors
                         if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors
                         if c.get("homeAway") == "away"), {})

            notes = competition.get("notes") or []
            pen_note = ""
            for note in notes:
                text = str(note.get("text") or note.get("headline") or "")
                if "penalt" in text.lower():
                    pen_note = text
                    break

            results.append({
                "espnId":    event.get("id"),
                "kickoff":   event.get("date", ""),
                "home":      norm(home.get("team", {}).get("displayName", "")),
                "away":      norm(away.get("team", {}).get("displayName", "")),
                "homeScore": _safe_int(home.get("score")),
                "awayScore": _safe_int(away.get("score")),
                "minute":    status_obj.get("displayClock", ""),
                "period":    status_obj.get("period"),
                "status":    status_type.get("name", ""),
                "statusDetail": status_type.get("detail", ""),
                "state":     status_type.get("state", ""),
                "penaltyNote": pen_note,
            })
        return results

    def find_match(self, home: str, away: str) -> Optional[dict]:
        """Find a specific match from live scoreboard."""
        for m in self.get_live_scores():
            if (norm(m["home"]) == norm(home) and
                    norm(m["away"]) == norm(away)) or \
               (norm(m["home"]) == norm(away) and
                    norm(m["away"]) == norm(home)):
                return m
        return None


# ── Private helpers ────────────────────────────────────────────────────────

def _safe_int(v) -> Optional[int]:
    if v is None: return None
    try: return int(v)
    except (ValueError, TypeError): return None

def _parse_pct(v) -> Optional[int]:
    if v is None: return None
    try: return int(str(v).replace("%", "").strip())
    except (ValueError, TypeError): return None
