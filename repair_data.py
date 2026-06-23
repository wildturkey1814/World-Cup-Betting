"""
Emergency repair for corrupted data.json.
Handles the } { concatenation bug where two JSON objects were merged.
"""
import logging

from data_utils import OUTPUT_FILE, atomic_write, filter_ghost_matches, load_data, repair_raw_json
import json
import os

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    if not os.path.exists(OUTPUT_FILE):
        log.error("%s not found.", OUTPUT_FILE)
        return

    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        raw = f.read()

    fixed = repair_raw_json(raw)
    if fixed != raw:
        log.info("Repaired concatenated JSON blocks.")

    try:
        data = json.loads(fixed)
        log.info("JSON parsed successfully.")
    except json.JSONDecodeError as exc:
        log.error("Parse failed after repair: %s", exc)
        data = {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}

    before = len(data.get("matches", []))
    data["matches"] = filter_ghost_matches(data.get("matches", []))
    after = len(data["matches"])
    log.info("Removed %d bad matches. %d clean matches remain.", before - after, after)

    atomic_write(OUTPUT_FILE, data)
    log.info("%s repaired and written successfully.", OUTPUT_FILE)


if __name__ == "__main__":
    main()
