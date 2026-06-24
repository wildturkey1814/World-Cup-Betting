"""
Removes SRL simulator/ghost matches from data.json and completed_matches.json.
Safe to run after every sync — idempotent.
"""
import json
import logging

from data_utils import (
    OUTPUT_FILE,
    atomic_write,
    filter_ghost_matches,
    is_ghost_match,
    repair_raw_json,
    sanitize_match_archive,
)
from standings import apply_eliminated_tournament_odds, compute_knockout_eliminated

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ARCHIVE_FILE = "completed_matches.json"


def _matches_for_standings(data: dict) -> list[dict]:
    matches = list(data.get("matches") or [])
    seen = {str(m.get("id")) for m in matches if m.get("id")}
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)
        if isinstance(archive, list):
            for entry in archive:
                if is_ghost_match(entry):
                    continue
                mid = str(entry.get("id") or "")
                if mid and mid not in seen:
                    matches.append(entry)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return matches


def main() -> None:
    arch_before, arch_after = sanitize_match_archive(ARCHIVE_FILE)
    if arch_before == arch_after and arch_before:
        log.info("Archive already clean (%d records).", arch_after)

    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    raw = repair_raw_json(raw)
    data = json.loads(raw)
    before = len(data.get("matches", []))
    matches = filter_ghost_matches(data.get("matches", []))
    data["matches"] = matches
    after = len(matches)

    eliminated = compute_knockout_eliminated(_matches_for_standings(data))
    if eliminated:
        n = apply_eliminated_tournament_odds(matches, eliminated)
        log.info(
            "Knockout eliminated (%d): %s",
            len(eliminated),
            ", ".join(sorted(eliminated)),
        )
        if n:
            log.info("Zeroed tournament winner odds on %d field(s).", n)
    data["knockoutEliminated"] = sorted(eliminated)

    from matchday_utils import group_stage_progress, tag_matchdays
    tag_matchdays(matches)
    data["groupStageProgress"] = group_stage_progress(matches)

    atomic_write(OUTPUT_FILE, data)
    log.info("Sanitized data.json: %d ghost match(es) removed, %d remain.", before - after, after)


if __name__ == "__main__":
    main()
