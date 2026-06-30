"""
2026 FIFA World Cup group stage matchday calendar.

72 group matches in three blocks of 24:
  Matchday 1 — June 11–17 (includes late Jun 17 US → early Jun 18 UTC kicks)
  Matchday 2 — June 18–23 (includes late Jun 23 US → early Jun 24 UTC kicks)
  Matchday 3 — June 24–28 (final group round, Round of 32 follows)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

GROUP_STAGE_TOTAL = 72
MATCHES_PER_MATCHDAY = 24
TOURNAMENT_OPEN = date(2026, 6, 11)
MATCHDAY_3_START = date(2026, 6, 24)


def _parse_kickoff(kickoff: str) -> datetime | None:
    if not kickoff:
        return None
    try:
        return datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def assign_matchday(kickoff: str) -> int | None:
    """
    Return 1, 2, or 3 for group-stage fixtures based on kickoff time.
    Late-evening US slots that fall after midnight UTC stay on the prior matchday.
    """
    dt = _parse_kickoff(kickoff)
    if not dt:
        return None

    d = dt.date()
    if d <= date(2026, 6, 17):
        return 1
    if d == date(2026, 6, 18) and dt.hour < 6:
        return 1
    if d <= date(2026, 6, 23):
        return 2
    if d == date(2026, 6, 24) and dt.hour < 6:
        return 2
    if d >= MATCHDAY_3_START:
        return 3
    return 3


def matchday_label(matchday: int, lang: str = "en") -> str:
    labels = {
        "en": {1: "Matchday 1", 2: "Matchday 2", 3: "Matchday 3"},
        "ko": {1: "1차전", 2: "2차전", 3: "3차전"},
        "ja": {1: "第1節", 2: "第2節", 3: "第3節"},
    }
    return labels.get(lang, labels["en"]).get(matchday, f"Matchday {matchday}")


def group_stage_progress(matches: list[dict]) -> dict:
    """Summarize layer-3 completion against the 72-match group stage."""
    completed = [
        m for m in matches
        if m.get("type") == "COMPLETED"
        and (m.get("stage") or "Group Stage") == "Group Stage"
    ]
    upcoming = [
        m for m in matches
        if m.get("type") == "UPCOMING"
        and (m.get("stage") or "Group Stage") == "Group Stage"
    ]
    in_play = [
        m for m in matches
        if m.get("type") == "IN_PLAY"
        and (m.get("stage") or "Group Stage") == "Group Stage"
    ]

    by_md: dict[int, int] = {1: 0, 2: 0, 3: 0}
    for m in completed:
        md = m.get("matchday") or assign_matchday(m.get("kickoff", ""))
        if md in by_md:
            by_md[md] += 1

    if by_md[1] < MATCHES_PER_MATCHDAY:
        current = 1
    elif by_md[2] < MATCHES_PER_MATCHDAY:
        current = 2
    else:
        current = 3

    return {
        "totalGroupMatches": GROUP_STAGE_TOTAL,
        "matchesPerMatchday": MATCHES_PER_MATCHDAY,
        "completedGroupMatches": len(completed),
        "upcomingGroupMatches": len(upcoming),
        "liveGroupMatches": len(in_play),
        "matchdayCompleted": by_md,
        "currentMatchday": current,
        "matchday3Starts": "2026-06-24",
    }


def tag_matchdays(matches: list[dict]) -> int:
    """Set matchday on every group-stage match record. Returns update count."""
    updated = 0
    for match in matches:
        stage = str(match.get("stage") or "Group Stage")
        if stage != "Group Stage":
            continue
        md = assign_matchday(match.get("kickoff", ""))
        if md is None:
            continue
        if match.get("matchday") != md:
            match["matchday"] = md
            updated += 1
    return updated
