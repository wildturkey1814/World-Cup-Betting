"""
Reconcile match layers against ESPN's public World Cup scoreboard.

Three-layer architecture (data.json `type` field):
  Layer 1 — UPCOMING   upcoming match cards
  Layer 2 — IN_PLAY    live match cards
  Layer 3 — COMPLETED  completed archive view

This script uses ESPN (no API key) as an independent public check. It:
  1. Promotes UPCOMING/IN_PLAY fixtures to IN_PLAY or COMPLETED when ESPN says so
  2. Fixes scores on COMPLETED rows when they drift from ESPN
  3. Prints an audit of ESPN finished games vs our Layer 3 archive

Run after fetch_scores.py (football-data.org) and before sync_completed_archive.py:

    python reconcile_match_layers.py
    python reconcile_match_layers.py --audit-only
    python reconcile_match_layers.py --dry-run

Designed to run on every live polling cycle through the end of the tournament.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from data_utils import OUTPUT_FILE, atomic_write, format_utc_display, load_data
from match_stats import FLAG_CODES, enrich_completed_match
from public_scores import (
    TOURNAMENT_END,
    TOURNAMENT_START,
    load_espn_live_by_pair,
    map_scores_to_record,
    pair_key,
    print_audit_report,
    run_audit,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def _score_line(home: str, away: str, hg: int, ag: int) -> str:
    return f"{home.upper()} {hg} - {ag} {away.upper()}"


def _source_accuracy(record: dict, home_won: bool, is_draw: bool) -> dict:
    accuracy = {}
    fav_is_home = record.get("favTeam") == record.get("home")
    for layer in record.get("layers") or []:
        source = layer.get("source", "")
        if not source:
            continue
        if is_draw:
            accuracy[source] = False
            continue
        fav_prob = float(str(layer.get("fav", "0")).replace("%", "") or 0)
        und_prob = float(str(layer.get("und", "0")).replace("%", "") or 0)
        picks_home = (fav_is_home and fav_prob > und_prob) or (not fav_is_home and und_prob > fav_prob)
        accuracy[source] = picks_home == home_won
    return accuracy


def reconcile_layers(matches: list[dict], espn_by_pair: dict[str, dict], dry_run: bool = False) -> dict:
    stats = {"promoted_live": 0, "promoted_completed": 0, "score_fixes": 0, "skipped": 0}

    for record in matches:
        home = record.get("home", "")
        away = record.get("away", "")
        key = pair_key(home, away)
        espn = espn_by_pair.get(key)
        if not espn:
            continue

        layer = record.get("type")
        state = espn.get("state")
        scores = map_scores_to_record(record, espn)

        if state == "in" and layer in ("UPCOMING", "IN_PLAY"):
            if scores is None:
                continue
            hg, ag = scores
            if not dry_run:
                record["type"] = "IN_PLAY"
                record["liveScore"] = f"{hg} - {ag}"
            stats["promoted_live"] += 1
            log.info("  LIVE  %s vs %s → %d-%d", home, away, hg, ag)
            continue

        if state != "post":
            continue

        if scores is None:
            stats["skipped"] += 1
            continue

        hg, ag = scores
        is_draw = hg == ag
        home_won = hg > ag

        if layer == "COMPLETED":
            if record.get("homeScore") == hg and record.get("awayScore") == ag:
                continue
            if not dry_run:
                record["homeScore"] = hg
                record["awayScore"] = ag
                record["score"] = _score_line(home, away, hg, ag)
                record["sourceAccuracy"] = _source_accuracy(record, home_won, is_draw)
                enrich_completed_match(record)
            stats["score_fixes"] += 1
            log.info("  FIX   %s vs %s → %d-%d (was Layer 3)", home, away, hg, ag)
            continue

        if layer in ("UPCOMING", "IN_PLAY"):
            if not dry_run:
                record["type"] = "COMPLETED"
                record["homeScore"] = hg
                record["awayScore"] = ag
                record["score"] = _score_line(home, away, hg, ag)
                record["sourceAccuracy"] = _source_accuracy(record, home_won, is_draw)
                record.pop("liveScore", None)
                if is_draw:
                    record["insight"] = f"The match ended level at {hg}-{ag}."
                elif home_won:
                    record["insight"] = f"{home} won {hg}-{ag}."
                else:
                    record["insight"] = f"{away} won {ag}-{hg}."
                enrich_completed_match(record)
            stats["promoted_completed"] += 1
            log.info("  DONE  %s vs %s → %d-%d (was %s)", home, away, hg, ag, layer)

    return stats


def _build_record_from_espn(espn: dict) -> dict:
    home, away = espn["home"], espn["away"]
    hg, ag = int(espn["homeScore"]), int(espn["awayScore"])
    is_draw = hg == ag
    home_won = hg > ag
    record: dict = {
        "id": f"espn-{espn.get('espnId') or pair_key(home, away)}",
        "type": "COMPLETED",
        "home": home,
        "away": away,
        "stage": "Group Stage",
        "group": "Group Stage",
        "kickoff": espn.get("kickoff") or "",
        "homeScore": hg,
        "awayScore": ag,
        "score": _score_line(home, away, hg, ag),
        "homeFlag": FLAG_CODES.get(home, home[:3].lower()),
        "awayFlag": FLAG_CODES.get(away, away[:3].lower()),
        "layers": [],
        "importedFrom": "ESPN",
    }
    if is_draw:
        record["insight"] = f"The match ended level at {hg}-{ag}."
    elif home_won:
        record["insight"] = f"{home} won {hg}-{ag}."
    else:
        record["insight"] = f"{away} won {ag}-{hg}."
    return enrich_completed_match(record)


def import_missing_completed(
    matches: list[dict],
    espn_by_pair: dict[str, dict],
    dry_run: bool = False,
) -> int:
    """Layer 3 backfill: add ESPN-finished fixtures missing from our database."""
    existing = {pair_key(m.get("home", ""), m.get("away", "")) for m in matches}
    imported = 0
    for key, espn in sorted(espn_by_pair.items(), key=lambda x: x[1].get("kickoff", "")):
        if espn.get("state") != "post":
            continue
        if key in existing:
            continue
        home, away = espn["home"], espn["away"]
        hg, ag = espn.get("homeScore"), espn.get("awayScore")
        if hg is None or ag is None:
            continue
        if not dry_run:
            matches.append(_build_record_from_espn(espn))
        imported += 1
        log.info("  IMPORT %s vs %s → %s-%s (new Layer 3)", home, away, hg, ag)
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile match layers vs ESPN public scoreboard.")
    parser.add_argument("--audit-only", action="store_true", help="Audit Layer 3 only; do not write data.json")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing data.json")
    parser.add_argument(
        "--no-import-missing",
        action="store_true",
        help="Do not create Layer 3 records for ESPN results missing from data.json",
    )
    parser.add_argument("--from", dest="date_from", default=TOURNAMENT_START)
    parser.add_argument("--to", dest="date_to", default=TOURNAMENT_END)
    parser.add_argument("--json", dest="json_out", help="Write audit JSON report to path")
    args = parser.parse_args()

    data = load_data(OUTPUT_FILE)
    matches = data.get("matches") or []

    if not args.audit_only:
        log.info("=== Reconciling layers 1–3 vs ESPN (%s–%s) ===", args.date_from, args.date_to)
        espn = load_espn_live_by_pair(args.date_from, args.date_to)
        log.info("ESPN returned %d fixture(s) in range.", len(espn))
        stats = reconcile_layers(matches, espn, dry_run=args.dry_run)
        imported = 0
        if not args.no_import_missing:
            imported = import_missing_completed(matches, espn, dry_run=args.dry_run)
        log.info(
            "Reconcile: %d → LIVE, %d → COMPLETED, %d score fix(es), %d imported, %d skipped.",
            stats["promoted_live"],
            stats["promoted_completed"],
            stats["score_fixes"],
            imported,
            stats["skipped"],
        )
        if not args.dry_run and (
            any(stats[k] for k in ("promoted_live", "promoted_completed", "score_fixes"))
            or imported
        ):
            data["matches"] = matches
            data["lastUpdated"] = format_utc_display(datetime.now(timezone.utc))
            atomic_write(OUTPUT_FILE, data)
            log.info("Wrote %s", OUTPUT_FILE)

    report = run_audit(args.date_from, args.date_to, matches=matches)
    print_audit_report(report)

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
