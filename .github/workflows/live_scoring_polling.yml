import os
import json
import logging
from datetime import datetime
import requests

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# Configuration
DATA_FILE = "data.json"
API_URL = "https://api.football-data.org/v4/competitions/WC/matches"
API_TOKEN = os.environ.get("FOOTBALL_DATA_API_TOKEN", "YOUR_API_TOKEN_HERE")

def fetch_api_matches():
    """
    Fetches matches directly from the sports API client.
    """
    headers = {"X-Auth-Token": API_TOKEN}

    # Covers full 2026 World Cup: June 11 group stage through July 19 final
    params = {
        "dateFrom": "2026-06-11",
        "dateTo": "2026-07-20"
    }

    try:
        log.info("Querying sports API client for match updates...")
        response = requests.get(API_URL, headers=headers, params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("matches", [])
        else:
            log.error("API returned status code %d: %s", response.status_code, response.text)
            return []
    except Exception as e:
        log.error("Failed to fetch matches from sports API: %s", str(e))
        return []

def recalculate_fav(record):
    """
    Dynamically averages all available data layers to select the consensus favorite.
    Prevents infinite inversion loops by anchoring calculations to absolute team names.
    """
    layers = record.get("layers", [])
    if not layers:
        return

    def parse_percentage(val):
        try:
            return float(str(val).replace("%", ""))
        except (ValueError, TypeError):
            return 0.0

    home_probs_sum = 0.0
    away_probs_sum = 0.0
    valid_layers_count = 0

    # Determine if the current UI presentation layout expects Home to be the favorite
    fav_is_home = record.get("favTeam") == record.get("home")

    for layer in layers:
        fav_val = parse_percentage(layer.get("fav"))
        und_val = parse_percentage(layer.get("und"))

        # Map values back to their true geography regardless of mutable column headers
        if fav_is_home:
            home_prob = fav_val
            away_prob = und_val
        else:
            home_prob = und_val
            away_prob = fav_val

        home_probs_sum += home_prob
        away_probs_sum += away_prob
        valid_layers_count += 1

    if valid_layers_count == 0:
        return

    # Calculate global consensus averages across all active sources
    avg_home = home_probs_sum / valid_layers_count
    avg_away = away_probs_sum / valid_layers_count

    # Determine consensus favorite based on combined layer metrics
    if avg_home >= avg_away:
        consensus_fav = record.get("home")
        should_be_home = True
    else:
        consensus_fav = record.get("away")
        should_be_home = False

    # If consensus favorite differs from current layout state, flip the display columns safely
    if should_be_home != fav_is_home:
        record["favTeam"] = consensus_fav
        for layer in layers:
            layer["fav"], layer["und"] = layer["und"], layer["fav"]

        log.info("Consensus correction applied for %s vs %s: updated favorite to %s (Home Avg: %.1f%%, Away Avg: %.1f%%)",
                 record.get("home"), record.get("away"), consensus_fav, avg_home, avg_away)

def map_api_status_to_schema(api_status):
    """
    Normalizes external API match states to the application's internal enum tracking.
    """
    mapping = {
        "TIMED": "UPCOMING",
        "SCHEDULED": "UPCOMING",
        "LIVE": "IN_PLAY",
        "IN_PLAY": "IN_PLAY",
        "PAUSED": "IN_PLAY",
        "FINISHED": "COMPLETED",
        "AWARDED": "COMPLETED"
    }
    return mapping.get(api_status, "UPCOMING")

def process_and_merge(existing_matches, api_matches):
    """
    Merges newly polled API payloads into the local data array.
    Locks down COMPLETED matches permanently so they can never be dropped or overwritten.
    """
    # Map current database using unique match identifiers
    db_map = {str(m["id"]): m for m in existing_matches}

    log.info("Processing updates against %d existing records...", len(existing_matches))

    for api_match in api_matches:
        match_id = str(api_match.get("id"))
        api_status = api_match.get("status")
        mapped_type = map_api_status_to_schema(api_status)

        # LOCK: If match is already COMPLETED in our DB with a score, never overwrite it.
        existing = db_map.get(match_id)
        if existing and existing.get("type") == "COMPLETED" and existing.get("score"):
            log.info("Skipping locked COMPLETED match: %s vs %s", existing.get("home"), existing.get("away"))
            continue

        # Extract operational values from API payload
        home_team = api_match.get("homeTeam", {}).get("name")
        away_team = api_match.get("awayTeam", {}).get("name")
        score_data = api_match.get("score", {})

        home_score = score_data.get("fullTime", {}).get("home")
        away_score = score_data.get("fullTime", {}).get("away")

        # Build or update the structural record
        if match_id in db_map:
            record = db_map[match_id]
        else:
            # Fallback initialization if a match was entirely missing from local storage
            record = {
                "id": match_id,
                "home": home_team,
                "away": away_team,
                "group": api_match.get("group", "Group Stage"),
                "stage": "Group Stage",
                "kickoff": api_match.get("utcDate"),
                "favTeam": home_team,
                "layers": [
                    {"source": "ELO/Poisson Model", "fav": "34.0%", "draw": "33.0%", "und": "33.0%"}
                ]
            }

        # Update type and scores
        record["type"] = mapped_type

        if home_score is not None:
            record["homeScore"] = home_score
        if away_score is not None:
            record["awayScore"] = away_score

        # When a match finishes, write the human-readable score string and lock it
        if mapped_type == "COMPLETED" and home_score is not None and away_score is not None:
            home_name = record.get("home", "Home").upper()
            away_name = record.get("away", "Away").upper()
            record["score"] = f"{home_name} {home_score} - {away_score} {away_name}"
            log.info("Match completed and locked: %s", record["score"])

        # Recalculate favorite dynamically based on layer consensus strings
        recalculate_fav(record)

        # Update map target
        db_map[match_id] = record

    return list(db_map.values())

def main():
    # 1. Load your central database file
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                database = json.load(f)
        except Exception as e:
            log.error("Failed to read %s: %s. Initializing empty database structure.", DATA_FILE, str(e))
            database = {"matches": []}
    else:
        database = {"matches": []}

    # 2. Extract matches array from schema root
    existing_matches = database.get("matches", [])

    # 3. Pull fresh payloads from external API client
    api_matches = fetch_api_matches()
    if not api_matches:
        log.warning("No matches returned from external API. Database write skipped to protect state integrity.")
        return

    # 4. Filter, process, and merge under the mutation rules
    updated_matches = process_and_merge(existing_matches, api_matches)
    database["matches"] = updated_matches

    # 5. Flush state changes permanently back to storage disk
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=2, ensure_ascii=False)
        log.info("Database file successfully synchronized and saved. Processing cycle complete.")
    except Exception as e:
        log.critical("Failed to write updated structural array back to %s: %s", DATA_FILE, str(e))

if __name__ == "__main__":
    main()
