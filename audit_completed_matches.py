"""
Audit local completed matches against ESPN's public World Cup scoreboard.

Usage:
    python audit_completed_matches.py
    python audit_completed_matches.py --json report.json

Thin wrapper around public_scores.run_audit — see reconcile_match_layers.py
to backfill Layer 3 from ESPN automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from public_scores import TOURNAMENT_END, TOURNAMENT_START, print_audit_report, run_audit

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Layer 3 completed matches vs ESPN.")
    parser.add_argument("--from", dest="date_from", default=TOURNAMENT_START)
    parser.add_argument("--to", dest="date_to", default=TOURNAMENT_END)
    parser.add_argument("--json", dest="json_out", help="Write JSON report to path")
    args = parser.parse_args()

    report = run_audit(args.date_from, args.date_to)
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
