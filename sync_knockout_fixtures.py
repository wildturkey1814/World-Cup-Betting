"""
Import Round of 32 fixtures from ESPN into data.json (upcoming + completed).

Run after group stage ends:
    python sync_knockout_fixtures.py
"""

from __future__ import annotations

import logging

from data_utils import OUTPUT_FILE, atomic_write, load_data
from match_stats import FLAG_CODES, enrich_completed_match, infer_fav_team
from matchday_utils import group_stage_progress, tag_matchdays
from public_scores import TOURNAMENT_END, TOURNAMENT_START, load_espn_live_by_pair, pair_key
from stage_utils import GROUP_STAGE, R32_WINDOW_END, R32_WINDOW_START, ROUND_OF_32, infer_stage, is_placeholder_team, parse_penalty_winner

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def _score_line(home: str, away: str, hg: int, ag: int) -> str:
    return f"{home.upper()} {hg} - {ag} {away.upper()}"


def _build_knockout_record(espn: dict, match_type: str) -> dict | None:
    home, away = espn["home"], espn["away"]
    if is_placeholder_team(home) or is_placeholder_team(away):
        return None

    stage = infer_stage(home, away, espn.get("kickoff") or "")
    if stage != ROUND_OF_32:
        return None

    hg = espn.get("homeScore")
    ag = espn.get("awayScore")
    pen = espn.get("penaltyNote") or ""
    record: dict = {
        "id": f"espn-{espn.get('espnId') or pair_key(home, away)}",
        "type": match_type,
        "home": home,
        "away": away,
        "stage": ROUND_OF_32,
        "group": ROUND_OF_32,
        "kickoff": espn.get("kickoff") or "",
        "homeFlag": FLAG_CODES.get(home, home[:3].lower()),
        "awayFlag": FLAG_CODES.get(away, away[:3].lower()),
        "layers": [],
        "importedFrom": "ESPN",
    }
    if pen:
        record["penaltyNote"] = pen

    if match_type == "COMPLETED":
        if hg is None or ag is None:
            return None
        hg, ag = int(hg), int(ag)
        record["homeScore"] = hg
        record["awayScore"] = ag
        record["score"] = _score_line(home, away, hg, ag)
        winner = parse_penalty_winner(pen, home, away)
        if winner:
            record["insight"] = f"{winner} advanced ({hg}-{ag}, penalties)."
        elif hg == ag:
            record["insight"] = f"The match ended level at {hg}-{ag}."
        elif hg > ag:
            record["insight"] = f"{home} won {hg}-{ag}."
        else:
            record["insight"] = f"{away} won {ag}-{hg}."
        record = enrich_completed_match(record)
        fav = infer_fav_team(record)
        if fav:
            record["favTeam"] = fav
    else:
        record["meta"] = f"Round of 32 · {home} vs {away}"

    return record


def sync_knockout_fixtures(matches: list[dict], espn_by_pair: dict[str, dict]) -> int:
    by_pair = {pair_key(m.get("home", ""), m.get("away", "")): i for i, m in enumerate(matches)}
    touched = 0

    for _key, espn in sorted(espn_by_pair.items(), key=lambda x: x[1].get("kickoff", "")):
        state = espn.get("state")
        if state not in ("pre", "in", "post"):
            continue
        match_type = "COMPLETED" if state == "post" else "UPCOMING"
        incoming = _build_knockout_record(espn, match_type)
        if not incoming:
            continue

        key = pair_key(incoming["home"], incoming["away"])
        if key in by_pair:
            idx = by_pair[key]
            existing = matches[idx]
            merged = {**existing, **incoming}
            if existing.get("layers"):
                merged["layers"] = existing["layers"]
            if existing.get("favTeam") and not merged.get("favTeam"):
                merged["favTeam"] = existing["favTeam"]
            if existing.get("type") == "COMPLETED":
                merged["type"] = "COMPLETED"
            matches[idx] = merged
        else:
            matches.append(incoming)
            by_pair[key] = len(matches) - 1
        touched += 1
        log.info("  R32  %s vs %s (%s)", incoming["home"], incoming["away"], match_type)

    return touched


def main() -> None:
    log.info("=== Syncing Round of 32 fixtures from ESPN ===")
    data = load_data(OUTPUT_FILE)
    matches = data.get("matches") or []
    espn = load_espn_live_by_pair(TOURNAMENT_START, TOURNAMENT_END)
    log.info("ESPN returned %d fixture(s).", len(espn))

    count = sync_knockout_fixtures(matches, espn)
    tag_matchdays(matches)

    # Drop fixtures mis-tagged as R32 outside the knockout window (e.g. R16 on Jul 4+).
    before = len(matches)
    matches = [
        m
        for m in matches
        if (m.get("stage") or GROUP_STAGE) != ROUND_OF_32
        or (R32_WINDOW_START <= (m.get("kickoff") or "")[:10] <= R32_WINDOW_END)
    ]
    if len(matches) < before:
        log.info("Removed %d fixture(s) outside Round of 32 date window.", before - len(matches))

    r32 = [m for m in matches if (m.get("stage") or GROUP_STAGE) == ROUND_OF_32]
    data["matches"] = matches
    data["currentStage"] = ROUND_OF_32 if r32 else data.get("currentStage", GROUP_STAGE)
    data["groupStageProgress"] = group_stage_progress(matches)
    data["lastUpdated"] = data.get("lastUpdated") or ""

    atomic_write(OUTPUT_FILE, data)
    log.info("Synced %d Round of 32 fixture(s). Total R32 rows: %d.", count, len(r32))


if __name__ == "__main__":
    main()
