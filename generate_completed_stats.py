"""
Build / refresh completed_matches.json from data.json completed fixtures.
Prefer sync_completed_archive.py for routine updates (also patches data.json).
"""

import json

from match_stats import build_archive_entry

from data_utils import load_data


def main() -> None:
    db = load_data("data.json")
    completed_raw = [m for m in db.get("matches", []) if m.get("type") == "COMPLETED"]
    print(f"Found {len(completed_raw)} completed matches.")

    archive = []
    for m in completed_raw:
        entry = build_archive_entry(m)
        archive.append(entry)
        print(f"  ✓ {entry['home']} {entry['homeScore']}-{entry['awayScore']} {entry['away']} [{entry['metrics']['archetype']}]")

    with open("completed_matches.json", "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nWrote {len(archive)} matches to completed_matches.json")


if __name__ == "__main__":
    main()
