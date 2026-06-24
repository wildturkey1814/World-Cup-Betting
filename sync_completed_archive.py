"""
Sync completed match stats into data.json and completed_matches.json.
Run after fetch_scores or manually: python sync_completed_archive.py
"""

import json
import logging

from data_utils import OUTPUT_FILE, atomic_write, atomic_write_match_list, filter_ghost_matches, load_data
from match_stats import build_archive_entry, enrich_completed_match, pair_key

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ARCHIVE_FILE = "completed_matches.json"


def load_archive() -> list[dict]:
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data if isinstance(data, list) else []
        return filter_ghost_matches(rows)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main() -> None:
    data = load_data(OUTPUT_FILE)
    archive = load_archive()
    archive_by_pair = {pair_key(e["home"], e["away"]): e for e in archive}
    archive_by_id = {str(e.get("id")): e for e in archive if e.get("id")}

    updated_data = 0
    updated_archive = 0
    matches = data.get("matches") or []

    for i, match in enumerate(matches):
        if match.get("type") != "COMPLETED":
            continue

        enriched = enrich_completed_match(match)
        matches[i] = enriched
        updated_data += 1

        entry = build_archive_entry(enriched)
        pk = pair_key(entry["home"], entry["away"])
        mid = str(entry.get("id") or "")
        if mid and mid in archive_by_id:
            archive_by_id[mid] = entry
        archive_by_pair[pk] = entry
        updated_archive += 1

    data["matches"] = matches
    atomic_write(OUTPUT_FILE, data)

    merged_archive = sorted(archive_by_pair.values(), key=lambda m: m.get("kickoff") or "")
    atomic_write_match_list(ARCHIVE_FILE, merged_archive)

    log.info("Enriched %d completed match(es) in data.json.", updated_data)
    log.info("Archive now holds %d match(es) in %s.", len(merged_archive), ARCHIVE_FILE)

    from momentum_engine import run as apply_momentum
    apply_momentum(OUTPUT_FILE, ARCHIVE_FILE)


if __name__ == "__main__":
    main()
