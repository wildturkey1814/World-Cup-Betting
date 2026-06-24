"""
Audit local completed matches against ESPN's public World Cup scoreboard.

Usage:
    python audit_completed_matches.py
    python audit_completed_matches.py --json report.json

Compares completed_matches.json + data.json COMPLETED rows to ESPN finished
fixtures from 2026-06-11 (tournament day 1) onward. No API key required.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from data_utils import OUTPUT_FILE, load_data
from live_clients import ESPNClient, norm

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ARCHIVE_FILE = "completed_matches.json"
TOURNAMENT_START = "20260611"
DEFAULT_END = "20260720"


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


@dataclass
class AuditReport:
    generated_at: str
    espn_finished: int
    local_finished: int
    missing_locally: list[dict]
    extra_locally: list[dict]
    score_mismatches: list[dict]
    ok: int


def load_local_completed() -> dict[str, LocalMatch]:
    """Merge archive + data.json completed; dedupe by team pair."""
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


def load_espn_finished(date_from: str, date_to: str) -> dict[str, dict]:
    client = ESPNClient()
    rows = client.fetch_finished_matches(date_from, date_to)
    out: dict[str, dict] = {}
    for row in rows:
        home, away = row["home"], row["away"]
        key = pair_key(home, away)
        out[key] = {
            "home": home,
            "away": away,
            "homeScore": row["homeScore"],
            "awayScore": row["awayScore"],
            "kickoff": row.get("kickoff", ""),
            "espnId": row.get("espnId"),
        }
    return out


def _scores_match(local: LocalMatch, espn: dict) -> bool:
    """Compare scores regardless of home/away orientation."""
    lh, la = local.home_score, local.away_score
    eh, ea = espn["homeScore"], espn["awayScore"]
    if local.home == espn["home"] and local.away == espn["away"]:
        return lh == eh and la == ea
    if local.home == espn["away"] and local.away == espn["home"]:
        return lh == ea and la == eh
    return lh == eh and la == ea


def run_audit(date_from: str = TOURNAMENT_START, date_to: str = DEFAULT_END) -> AuditReport:
    local = load_local_completed()
    espn = load_espn_finished(date_from, date_to)

    missing = []
    for key, ref in sorted(espn.items(), key=lambda x: x[1].get("kickoff", "")):
        if key not in local:
            missing.append(ref)

    extra = []
    for key, loc in sorted(local.items(), key=lambda x: x[1].kickoff):
        if key not in espn:
            extra.append(asdict(loc))

    mismatches = []
    ok = 0
    for key in sorted(set(local) & set(espn)):
        loc = local[key]
        ref = espn[key]
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

    return AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        espn_finished=len(espn),
        local_finished=len(local),
        missing_locally=missing,
        extra_locally=extra,
        score_mismatches=mismatches,
        ok=ok,
    )


def print_report(report: AuditReport) -> None:
    log.info("=== Completed Match Audit (ESPN public scoreboard) ===")
    log.info("ESPN finished:  %d", report.espn_finished)
    log.info("Local finished: %d", report.local_finished)
    log.info("Matched scores: %d", report.ok)
    log.info("Missing locally: %d", len(report.missing_locally))
    for row in report.missing_locally:
        log.info(
            "  MISSING  %s %d-%d %s  (%s)",
            row["home"],
            row["homeScore"],
            row["awayScore"],
            row["away"],
            (row.get("kickoff") or "")[:10],
        )
    log.info("Extra locally (not on ESPN): %d", len(report.extra_locally))
    for row in report.extra_locally:
        log.info(
            "  EXTRA    %s %s-%s %s  [%s]",
            row["home"],
            row["home_score"],
            row["away_score"],
            row["away"],
            row["source"],
        )
    log.info("Score mismatches: %d", len(report.score_mismatches))
    for row in report.score_mismatches:
        log.info(
            "  MISMATCH %s vs %s  local %s  ESPN %s",
            row["home"],
            row["away"],
            row["localScore"],
            row["espnScore"],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit completed matches vs ESPN.")
    parser.add_argument("--from", dest="date_from", default=TOURNAMENT_START)
    parser.add_argument("--to", dest="date_to", default=DEFAULT_END)
    parser.add_argument("--json", dest="json_out", help="Write JSON report to path")
    args = parser.parse_args()

    report = run_audit(args.date_from, args.date_to)
    print_report(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False)
            f.write("\n")
        log.info("Wrote %s", args.json_out)

    if report.missing_locally or report.score_mismatches:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
