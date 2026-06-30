"""
Knockout stage detection and normalization for the 2026 World Cup pipeline.
"""

from __future__ import annotations

from match_stats import pair_key

GROUP_STAGE = "Group Stage"
ROUND_OF_32 = "Round of 32"
ROUND_OF_16 = "Round of 16"
QUARTER_FINALS = "Quarter-Finals"
SEMI_FINALS = "Semi-Finals"
FINAL = "Final"

KNOCKOUT_STAGES = frozenset(
    {ROUND_OF_32, ROUND_OF_16, QUARTER_FINALS, SEMI_FINALS, FINAL}
)

# Last group-stage fixtures that kick off on R32 opening days (UTC).
GROUP_STAGE_LATE_PAIRS = frozenset(
    {
        pair_key("Algeria", "Austria"),
        pair_key("Jordan", "Argentina"),
    }
)

R32_WINDOW_START = "2026-06-28"
R32_WINDOW_END = "2026-07-03"


def is_placeholder_team(name: str) -> bool:
    n = (name or "").strip().lower()
    return not n or "winner" in n or "tbd" in n or "round of" in n


def normalize_stage(raw: str | None) -> str:
    if not raw:
        return GROUP_STAGE
    s = str(raw).strip()
    low = s.lower()
    if "group" in low:
        return GROUP_STAGE
    if "32" in low and "16" not in low:
        return ROUND_OF_32
    if "16" in low and "round" in low:
        return ROUND_OF_16
    if "quarter" in low:
        return QUARTER_FINALS
    if "semi" in low:
        return SEMI_FINALS
    if low == "final" or low.endswith(" final"):
        return FINAL
    return s


def is_knockout_stage(stage: str | None) -> bool:
    return normalize_stage(stage) in KNOCKOUT_STAGES


def infer_stage(home: str, away: str, kickoff: str | None) -> str:
    """Infer Group Stage vs Round of 32 from kickoff window and fixture pair."""
    if is_placeholder_team(home) or is_placeholder_team(away):
        day = (kickoff or "")[:10]
        if day >= R32_WINDOW_START:
            return ROUND_OF_32
        return GROUP_STAGE

    pk = pair_key(home, away)
    day = (kickoff or "")[:10]
    if not day or day < R32_WINDOW_START:
        return GROUP_STAGE
    if day > R32_WINDOW_END:
        return ROUND_OF_16
    if pk in GROUP_STAGE_LATE_PAIRS:
        return GROUP_STAGE
    return ROUND_OF_32


def parse_penalty_winner(note: str | None, home: str, away: str) -> str | None:
    """Return winning team name from ESPN penalty note, or None."""
    if not note or "penalt" not in note.lower():
        return None
    low = note.lower()
    for team in (home, away):
        if team and team.lower() in low and "advance" in low:
            return team
    return None
