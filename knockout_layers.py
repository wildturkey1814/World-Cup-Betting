"""
Baseline prediction layers for knockout fixtures imported from ESPN.

ESPN sync creates rows with empty `layers`. This module fills ELO/Poisson
probabilities (knockout mode — no host bonus, reduced draw weight) so cards
render before the next OddsPapi / Kalshi CI run. Existing sportsbook or
prediction-market layers are never overwritten.
"""

from __future__ import annotations

from fetch_odds import elo_probs
from match_stats import infer_fav_team


def _pct(v: float | None) -> str:
    return f"{round(v * 100, 1)}%" if v is not None else "--"


def build_elo_layer(home: str, away: str, *, knockout: bool = True) -> dict:
    ep = elo_probs(home, away, group_stage=not knockout)
    return {
        "source": "ELO/Poisson Model",
        "fav": _pct(ep["home"]),
        "draw": "--" if knockout else _pct(ep["draw"]),
        "und": _pct(ep["away"]),
    }


def _layer_key(layer: dict) -> str:
    source = str(layer.get("source", "")).lower()
    if "sportsbook" in source:
        return "sportsbook"
    if "elo" in source or "poisson" in source:
        return "elo"
    if "kalshi" in source or "polymarket" in source or "prediction" in source:
        return "prediction"
    return source


def merge_layers(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for layer in existing:
        by_key[_layer_key(layer)] = layer
    for layer in incoming:
        key = _layer_key(layer)
        if key not in by_key or not by_key[key].get("fav") or by_key[key].get("fav") == "--":
            by_key[key] = layer
    order = ["sportsbook", "elo", "prediction"]
    rest = [k for k in by_key if k not in order]
    merged: list[dict] = []
    for key in order + rest:
        if key in by_key:
            merged.append(by_key[key])
    return merged


def fav_from_elo(home: str, away: str) -> str:
    ep = elo_probs(home, away, group_stage=False)
    return home if ep["home"] >= ep["away"] else away


def enrich_knockout_match(match: dict) -> dict:
    """Ensure knockout rows have ELO layers and a favTeam for card rendering."""
    home = match.get("home", "")
    away = match.get("away", "")
    if not home or not away:
        return match

    existing = list(match.get("layers") or [])
    has_elo = any("elo" in str(l.get("source", "")).lower() for l in existing)
    if not has_elo:
        existing = merge_layers(existing, [build_elo_layer(home, away)])

    match["layers"] = existing

    fav = infer_fav_team(match)
    if fav:
        match["favTeam"] = fav
    elif not match.get("favTeam"):
        match["favTeam"] = fav_from_elo(home, away)

    return match


def enrich_all_knockout_matches(matches: list[dict]) -> int:
    touched = 0
    for match in matches:
        stage = match.get("stage") or "Group Stage"
        if stage == "Group Stage":
            continue
        before = len(match.get("layers") or [])
        enrich_knockout_match(match)
        if match.get("layers") or match.get("favTeam"):
            touched += 1
        _ = before  # noqa: F841 — kept for potential debug logging
    return touched
