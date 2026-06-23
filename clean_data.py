"""
Removes SRL simulator/ghost matches from data.json.
Safe to run after every sync — idempotent.
"""
import json
import logging

from data_utils import OUTPUT_FILE, atomic_write, filter_ghost_matches, repair_raw_json

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    raw = repair_raw_json(raw)
    data = json.loads(raw)
    before = len(data.get("matches", []))
    data["matches"] = filter_ghost_matches(data.get("matches", []))
    after = len(data["matches"])

    atomic_write(OUTPUT_FILE, data)
    log.info("Sanitized data.json: %d ghost match(es) removed, %d remain.", before - after, after)


if __name__ == "__main__":
    main()
