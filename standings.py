"""
Group-stage standings and knockout elimination checks.

A team is marked eliminated when it cannot mathematically finish in the
top two of its group (Round of 32 via top-two qualification).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

TEAM_ALIASES: dict[str, str] = {
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Curaçao": "Curacao",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
}


def normalize_team(name: str) -> str:
    if not name:
        return ""
    cleaned = name.strip()
    return TEAM_ALIASES.get(cleaned, cleaned)


def _is_group_code(group: str) -> bool:
    return bool(re.match(r"^GROUP_[A-L]$", str(group or "").upper()))


@dataclass
class TeamRecord:
    team: str
    points: int = 0
    gf: int = 0
    ga: int = 0
    played: int = 0
    remaining: int = 0

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def max_points(self) -> int:
        return self.points + 3 * self.remaining


def _result_points(home_score: int, away_score: int, for_home: bool) -> int:
    if home_score == away_score:
        return 1
    home_won = home_score > away_score
    if for_home:
        return 3 if home_won else 0
    return 0 if home_won else 3


def build_group_membership(matches: Iterable[dict]) -> dict[str, set[str]]:
    """Map GROUP_X -> set of canonical team names."""
    team_group: dict[str, str] = {}
    groups: dict[str, set[str]] = defaultdict(set)

    for match in matches:
        group = str(match.get("group") or "")
        home = normalize_team(match.get("home", ""))
        away = normalize_team(match.get("away", ""))
        if not home or not away:
            continue
        if _is_group_code(group):
            team_group[home] = group
            team_group[away] = group
            groups[group].update({home, away})

    changed = True
    while changed:
        changed = False
        for match in matches:
            home = normalize_team(match.get("home", ""))
            away = normalize_team(match.get("away", ""))
            gh, ga = team_group.get(home), team_group.get(away)
            if gh and not ga:
                team_group[away] = gh
                groups[gh].add(away)
                changed = True
            elif ga and not gh:
                team_group[home] = ga
                groups[ga].add(home)
                changed = True

    return dict(groups)


def _group_fixtures(matches: Iterable[dict], group_teams: set[str]) -> list[dict]:
    out = []
    for match in matches:
        home = normalize_team(match.get("home", ""))
        away = normalize_team(match.get("away", ""))
        if home in group_teams and away in group_teams:
            stage = str(match.get("stage") or "Group Stage")
            if stage == "Group Stage":
                out.append(match)
    return out


def _build_records(group_teams: set[str], fixtures: list[dict]) -> dict[str, TeamRecord]:
    records = {team: TeamRecord(team=team) for team in group_teams}

    for match in fixtures:
        home = normalize_team(match.get("home", ""))
        away = normalize_team(match.get("away", ""))
        if match.get("type") != "COMPLETED":
            continue
        if match.get("homeScore") is None or match.get("awayScore") is None:
            continue
        hg = int(match["homeScore"])
        ag = int(match["awayScore"])
        for team, pts, gf, ga in (
            (home, _result_points(hg, ag, True), hg, ag),
            (away, _result_points(hg, ag, False), ag, hg),
        ):
            rec = records[team]
            rec.points += pts
            rec.gf += gf
            rec.ga += ga
            rec.played += 1

    pending = defaultdict(int)
    for match in fixtures:
        if match.get("type") == "COMPLETED":
            continue
        home = normalize_team(match.get("home", ""))
        away = normalize_team(match.get("away", ""))
        pending[home] += 1
        pending[away] += 1

    for team, rec in records.items():
        rec.remaining = pending.get(team, 0)

    return records


def _apply_outcome(
    records: dict[str, TeamRecord],
    home: str,
    away: str,
    home_pts: int,
    away_pts: int,
    home_goals: int,
    away_goals: int,
) -> dict[str, TeamRecord]:
    trial = {
        t: TeamRecord(
            team=t,
            points=r.points,
            gf=r.gf,
            ga=r.ga,
            played=r.played,
            remaining=r.remaining,
        )
        for t, r in records.items()
    }
    th, ta = trial[home], trial[away]
    th.played += 1
    ta.played += 1
    th.remaining = max(0, th.remaining - 1)
    ta.remaining = max(0, ta.remaining - 1)
    th.points += home_pts
    ta.points += away_pts
    th.gf += home_goals
    th.ga += away_goals
    ta.gf += away_goals
    ta.ga += home_goals
    return trial


def _is_top_two(records: dict[str, TeamRecord], team: str) -> bool:
    ranked = sorted(records.values(), key=lambda r: (r.points, r.gd, r.gf), reverse=True)
    return team in {ranked[0].team, ranked[1].team}


def _simulate_all_outcomes(
    team: str,
    records: dict[str, TeamRecord],
    pending_pairs: list[tuple[str, str]],
) -> bool:
    """Return True if team is eliminated in every possible remaining outcome."""
    if not pending_pairs:
        return not _is_top_two(records, team)

    home, away = pending_pairs[0]
    rest = pending_pairs[1:]
    outcomes = (
        (3, 0, 1, 0),
        (1, 1, 1, 1),
        (0, 3, 0, 1),
    )
    for hp, ap, hg, ag in outcomes:
        trial = _apply_outcome(records, home, away, hp, ap, hg, ag)
        if not _simulate_all_outcomes(team, trial, rest):
            return False
    return True


def _is_eliminated_with_pending(
    team: str,
    records: dict[str, TeamRecord],
    pending_pairs: list[tuple[str, str]],
) -> bool:
    me = records[team]
    my_max = me.max_points
    others = [records[t] for t in records if t != team]

    if sum(1 for o in others if o.points > my_max) >= 2:
        return True

    if me.remaining == 0 and all(o.remaining == 0 for o in others):
        return not _is_top_two(records, team)

    if not pending_pairs:
        return False

    return _simulate_all_outcomes(team, records, pending_pairs)


def compute_knockout_eliminated(matches: list[dict]) -> set[str]:
    """Teams that cannot reach the Round of 32 via a top-two group finish."""
    groups = build_group_membership(matches)
    eliminated: set[str] = set()

    for group_teams in groups.values():
        if len(group_teams) < 2:
            continue
        fixtures = _group_fixtures(matches, group_teams)
        records = _build_records(group_teams, fixtures)

        pending_pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for match in fixtures:
            if match.get("type") == "COMPLETED":
                continue
            home = normalize_team(match.get("home", ""))
            away = normalize_team(match.get("away", ""))
            key = tuple(sorted((home, away)))
            if key not in seen:
                seen.add(key)
                pending_pairs.append((home, away))

        for team in group_teams:
            if _is_eliminated_with_pending(team, records, pending_pairs):
                eliminated.add(team)

    return eliminated


def apply_eliminated_tournament_odds(matches: list[dict], eliminated: set[str]) -> int:
    """Force tournament winner futures to 0.0% for eliminated teams."""
    updated = 0
    for match in matches:
        for side, team_key in (("Home", "home"), ("Away", "away")):
            team = normalize_team(match.get(team_key, ""))
            if team not in eliminated:
                continue
            for prefix in ("kalshiWinProb", "polymarketWinProb"):
                field = f"{prefix}{side}"
                if match.get(field) != "0.0%":
                    match[field] = "0.0%"
                    updated += 1
    return updated
