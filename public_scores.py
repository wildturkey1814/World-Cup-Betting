"""
Public World Cup scoreboard helpers (ESPN — no API key required).

Used by reconcile_match_layers.py and audit_completed_matches.py to keep
Layer 1 (UPCOMING), Layer 2 (IN_PLAY), and Layer 3 (COMPLETED) aligned
with an independent public source through the tournament.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable

from data_utils import OUTPUT_FILE, load_data
from live_clients import ESPNClient, norm

ARCHIVE_FILE = "completed_matches.json"
TOURNAMENT_START = "20260611"
TOURNAMENT_END = "20260720"


def pair_key(home: str, away: str) -> str:
    a, b = sorted([norm(home), norm(away)])
    return f"{a}|{b}"


@dataclass
class LocalMatch:
    home: str
    away: str
    home_score: int
    away_score: int
    kickoff: str
    source: str
    match_id: str
    layer: str = "COMPLETED"


@dataclass
class AuditReport:
    generated_at: str
    espn_finished: int
    local_finished: int
    missing_locally: list[dict]
    extra_locally: list[dict]
    score_mismatches: list[dict]
    ok: int
    layer_counts: dict[str, int]


def load_espn_by_pair(date_from: str = TOURNAMENT_START, date_to: str = TOURNAMENT_END) -> dict[str, dict]:
    client = ESPNClient()
    out: dict[str, dict] = {}
    for row in client.fetch_finished_matches(date_from, date_to):
        key = pair_key(row["home"], row["away"])
        out[key] = row
    return out


def load_espn_live_by_pair(date_from: str = TOURNAMENT_START, date_to: str = TOURNAMENT_END) -> dict[str, dict]:
    """All ESPN fixtures in range (pre / in / post)."""
    client = ESPNClient()
    data = client._get("/scoreboard", {"dates": f"{date_from}-{date_to}"})
    out: dict[str, dict] = {}
    for row in client._parse_scoreboard(data):
        key = pair_key(row["home"], row["away"])
        out[key] = row
    return out


def load_local_completed() -> dict[str, LocalMatch]:
    out: dict[str, LocalMatch] = {}

    def ingest(match: dict, source: str) -> None:
        if match.get("type") not in (None, "COMPLETED"):
            return
        if match.get("homeScore") is None or match.get("awayScore") is None:
            return
        home = norm(match.get("home", ""))
        away = norm(match.get("away", ""))
        if not home or not away:
            return
        key = pair_key(home, away)
        out[key] = LocalMatch(
            home=home,
            away=away,
            home_score=int(match["homeScore"]),
            away_score=int(match["awayScore"]),
            kickoff=str(match.get("kickoff") or ""),
            source=source,
            match_id=str(match.get("id") or ""),
            layer="COMPLETED",
        )

    data = load_data(OUTPUT_FILE)
    for match in data.get("matches") or []:
        ingest(match, OUTPUT_FILE)

    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)
        if isinstance(archive, list):
            for match in archive:
                ingest(match, ARCHIVE_FILE)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return out


def layer_counts(matches: Iterable[dict]) -> dict[str, int]:
    counts = {"UPCOMING": 0, "IN_PLAY": 0, "COMPLETED": 0, "OTHER": 0}
    for match in matches:
        layer = match.get("type") or "OTHER"
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def map_scores_to_record(record: dict, espn: dict) -> tuple[int, int] | None:
    rec_home = norm(record.get("home", ""))
    rec_away = norm(record.get("away", ""))
    espn_home = norm(espn.get("home", ""))
    espn_away = norm(espn.get("away", ""))
    hs, as_ = espn.get("homeScore"), espn.get("awayScore")
    if hs is None or as_ is None:
        return None
    if rec_home == espn_home and rec_away == espn_away:
        return int(hs), int(as_)
    if rec_home == espn_away and rec_away == espn_home:
        return int(as_), int(hs)
    return None


def _scores_match(local: LocalMatch, espn: dict) -> bool:
    lh, la = local.home_score, local.away_score
    eh, ea = espn["homeScore"], espn["awayScore"]
    if local.home == espn["home"] and local.away == espn["away"]:
        return lh == eh and la == ea
    if local.home == espn["away"] and local.away == espn["home"]:
        return lh == ea and la == eh
    return lh == eh and la == ea


def run_audit(
    date_from: str = TOURNAMENT_START,
    date_to: str = TOURNAMENT_END,
    matches: list[dict] | None = None,
) -> AuditReport:
    local = load_local_completed()
    espn_finished = {
        k: v for k, v in load_espn_by_pair(date_from, date_to).items()
        if v.get("state") == "post"
    }

    missing = [ref for key, ref in sorted(espn_finished.items(), key=lambda x: x[1].get("kickoff", "")) if key not in local]
    extra = [asdict(loc) for key, loc in sorted(local.items(), key=lambda x: x[1].kickoff) if key not in espn_finished]

    mismatches = []
    ok = 0
    for key in sorted(set(local) & set(espn_finished)):
        loc = local[key]
        ref = espn_finished[key]
        if _scores_match(loc, ref):
            ok += 1
        else:
            mismatches.append({
                "home": loc.home,
                "away": loc.away,
                "localScore": f"{loc.home_score}-{loc.away_score}",
                "espnScore": f"{ref['homeScore']}-{ref['awayScore']}",
                "kickoff": loc.kickoff or ref.get("kickoff"),
                "matchId": loc.match_id,
            })

    if matches is None:
        matches = load_data(OUTPUT_FILE).get("matches") or []

    return AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        espn_finished=len(espn_finished),
        local_finished=len(local),
        missing_locally=missing,
        extra_locally=extra,
        score_mismatches=mismatches,
        ok=ok,
        layer_counts=layer_counts(matches),
    )


def print_audit_report(report: AuditReport) -> None:
    import logging
    log = logging.getLogger(__name__)
    lc = report.layer_counts
    log.info("=== Layer status (data.json) ===")
    log.info("  Layer 1 UPCOMING:  %d", lc.get("UPCOMING", 0))
    log.info("  Layer 2 IN_PLAY:    %d", lc.get("IN_PLAY", 0))
    log.info("  Layer 3 COMPLETED:  %d", lc.get("COMPLETED", 0))
    log.info("=== Completed audit (ESPN public scoreboard) ===")
    log.info("ESPN finished:  %d", report.espn_finished)
    log.info("Local finished: %d", report.local_finished)
    log.info("Matched scores: %d", report.ok)
    log.info("Missing locally: %d", len(report.missing_locally))
    for row in report.missing_locally:
        log.info(
            "  MISSING  %s %d-%d %s  (%s)",
            row["home"], row["homeScore"], row["awayScore"], row["away"],
            (row.get("kickoff") or "")[:10],
        )
    log.info("Extra locally (not on ESPN): %d", len(report.extra_locally))
    for row in report.extra_locally:
        log.info(
            "  EXTRA    %s %s-%s %s  [%s]",
            row["home"], row["home_score"], row["away_score"], row["away"], row["source"],
        )
    log.info("Score mismatches: %d", len(report.score_mismatches))
    for row in report.score_mismatches:
        log.info(
            "  MISMATCH %s vs %s  local %s  ESPN %s",
            row["home"], row["away"], row["localScore"], row["espnScore"],
        )
