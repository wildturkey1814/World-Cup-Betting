"""One-off audit: SRL / ghost records in data.json and completed_matches.json."""
from __future__ import annotations

import json
import sys

from data_utils import KNOWN_WC_TEAMS, VALID_FLAGS, filter_ghost_matches, is_ghost_match, is_srl_text


def audit_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    matches = raw if isinstance(raw, list) else raw.get("matches", [])

    ghosts: list[dict] = []
    srl_hits: list[dict] = []
    bad_flags: list[dict] = []
    unknown_teams: list[dict] = []

    for m in matches:
        if any(is_srl_text(m.get(f)) for f in ("home", "away", "favTeam", "undTeam", "id")):
            srl_hits.append(m)
        home = str(m.get("home") or "")
        away = str(m.get("away") or "")
        if home and home not in KNOWN_WC_TEAMS:
            unknown_teams.append(m)
        elif away and away not in KNOWN_WC_TEAMS:
            unknown_teams.append(m)
        for field in ("homeFlag", "awayFlag"):
            flag = str(m.get(field) or "").lower()
            if flag and flag not in VALID_FLAGS:
                bad_flags.append(m)
                break
        if is_ghost_match(m):
            ghosts.append(m)

    return {
        "path": path,
        "total": len(matches),
        "ghosts": ghosts,
        "srl_hits": srl_hits,
        "unknown_teams": unknown_teams,
        "bad_flags": bad_flags,
    }


def fmt(m: dict) -> str:
    return (
        f"id={m.get('id')} | {m.get('home')} vs {m.get('away')} | "
        f"type={m.get('type')} | group={m.get('group')} | kickoff={m.get('kickoff')}"
    )


def main() -> int:
    any_issue = False
    for path in ("data.json", "completed_matches.json"):
        try:
            r = audit_file(path)
        except FileNotFoundError:
            print(f"=== {path}: not found ===")
            continue

        print(f"\n=== {path} ===")
        print(f"Total records:     {r['total']}")
        print(f"Ghost (filtered):  {len(r['ghosts'])}")
        print(f"SRL text hits:     {len(r['srl_hits'])}")
        print(f"Unknown WC teams:  {len(r['unknown_teams'])}")
        print(f"Invalid flags:     {len(r['bad_flags'])}")

        if r["srl_hits"]:
            any_issue = True
            print("\nSRL-labelled records:")
            for m in r["srl_hits"]:
                print("  ", fmt(m))

        if r["ghosts"]:
            any_issue = True
            non_srl = [m for m in r["ghosts"] if m not in r["srl_hits"]]
            if non_srl:
                print("\nOther ghost records (unknown team / bad flag):")
                for m in non_srl:
                    print("  ", fmt(m))

        if not r["ghosts"]:
            print("  OK - No ghost records detected.")

    with open("data.json", encoding="utf-8") as f:
        data = json.load(f)
    before = len(data.get("matches", []))
    after = len(filter_ghost_matches(data.get("matches", [])))
    print(f"\n=== data.json after filter_ghost_matches ===")
    print(f"Would keep {after} / {before} (remove {before - after})")

    return 1 if any_issue else 0


if __name__ == "__main__":
    sys.exit(main())
