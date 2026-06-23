"""
Tournament Momentum Factor (χ) — Supercharger+ engine.

Computes per-match momentum from completed results and injects rolling
team averages into upcoming fixtures in data.json.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from typing import Any

from data_utils import OUTPUT_FILE, atomic_write, load_data
from fetch_odds import elo_probs

log = logging.getLogger(__name__)

ARCHIVE_FILE = "completed_matches.json"
W1 = 0.6
W2 = 0.4
ELO_SOURCE = "ELO/Poisson Model"
PLACEHOLDER_ELO = ("34.0%", "33.0%", "33.0%")


def parse_pct(value: Any) -> float:
    if value is None:
        return 0.0
    s = str(value).strip().replace("%", "")
    try:
        return float(s) / 100.0
    except ValueError:
        return 0.0


def format_chi(value: float) -> str:
    if value >= 0:
        return f"+{value:.3f}"
    return f"{value:.3f}"


def _elo_layer(match: dict) -> dict | None:
    for layer in match.get("layers") or []:
        if layer.get("source") == ELO_SOURCE:
            return layer
    return None


def _is_placeholder_elo(layer: dict) -> bool:
    return (
        layer.get("fav") == PLACEHOLDER_ELO[0]
        and layer.get("draw") == PLACEHOLDER_ELO[1]
        and layer.get("und") == PLACEHOLDER_ELO[2]
    )


def _win_prob_from_elo_layer(team: str, home: str, away: str, match: dict) -> float:
    layer = _elo_layer(match)
    fav_team = match.get("favTeam") or home
    is_group = (match.get("stage") or "Group Stage") == "Group Stage"

    if layer and not _is_placeholder_elo(layer):
        fav_p = parse_pct(layer.get("fav"))
        und_p = parse_pct(layer.get("und"))
        if team == fav_team:
            return fav_p
        if team == home:
            return fav_p if fav_team == home else und_p
        return und_p if fav_team == home else fav_p

    probs = elo_probs(home, away, group_stage=is_group)
    return probs["home"] if team == home else probs["away"]


def _outcome(home_score: int, away_score: int, for_home: bool) -> float:
    if home_score == away_score:
        return 0.5
    home_won = home_score > away_score
    if for_home:
        return 1.0 if home_won else 0.0
    return 0.0 if home_won else 1.0


def _xg_pair(match: dict) -> tuple[float, float]:
    xg = (
        (match.get("advancedMetrics") or {}).get("xg")
        or (match.get("metrics") or {}).get("xg")
        or (match.get("boxScore") or {}).get("xg")
        or {}
    )
    return float(xg.get("home") or 0.0), float(xg.get("away") or 0.0)


def compute_match_chi(match: dict) -> tuple[float, float]:
    """Return (χ_home, χ_away) for a completed match record."""
    home = match["home"]
    away = match["away"]
    hg = int(match.get("homeScore") or 0)
    ag = int(match.get("awayScore") or 0)
    xg_h, xg_a = _xg_pair(match)

    p_home = _win_prob_from_elo_layer(home, home, away, match)
    p_away = _win_prob_from_elo_layer(away, home, away, match)

    out_home = _outcome(hg, ag, True)
    out_away = _outcome(hg, ag, False)

    xg_diff_home = xg_h - xg_a
    score_diff_home = hg - ag
    tel_home = xg_diff_home - score_diff_home

    xg_diff_away = xg_a - xg_h
    score_diff_away = ag - hg
    tel_away = xg_diff_away - score_diff_away

    chi_home = W1 * (out_home - p_home) + W2 * tel_home
    chi_away = W1 * (out_away - p_away) + W2 * tel_away
    return round(chi_home, 4), round(chi_away, 4)


def build_team_rolling_chi(completed: list[dict]) -> dict[str, float]:
    """Rolling mean χ per team across all completed tournament matches."""
    buckets: dict[str, list[float]] = defaultdict(list)

    for match in completed:
        if match.get("type") not in (None, "COMPLETED"):
            continue
        if match.get("homeScore") is None and match.get("awayScore") is None:
            continue
        chi_h, chi_a = compute_match_chi(match)
        buckets[match["home"]].append(chi_h)
        buckets[match["away"]].append(chi_a)

    return {team: round(sum(vals) / len(vals), 4) for team, vals in buckets.items()}


def load_completed_archive(path: str = ARCHIVE_FILE) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("Could not read %s — using data.json completed matches only.", path)
        return []


def apply_momentum_to_data(data: dict, team_chi: dict[str, float]) -> int:
    updated = 0
    for match in data.get("matches") or []:
        if match.get("type") != "UPCOMING":
            continue
        home = match.get("home", "")
        away = match.get("away", "")
        match["homeMomentumFactor"] = format_chi(team_chi.get(home, 0.0))
        match["awayMomentumFactor"] = format_chi(team_chi.get(away, 0.0))
        updated += 1
    return updated


def run(path: str = OUTPUT_FILE, archive_path: str = ARCHIVE_FILE) -> dict[str, float]:
    archive = load_completed_archive(archive_path)
    if not archive:
        data = load_data(path)
        archive = [m for m in data.get("matches", []) if m.get("type") == "COMPLETED"]

    team_chi = build_team_rolling_chi(archive)
    data = load_data(path)
    count = apply_momentum_to_data(data, team_chi)
    atomic_write(path, data)
    log.info("Applied Supercharger+ momentum to %d upcoming match(es).", count)
    log.info("Teams tracked: %d", len(team_chi))
    return team_chi


def _verify_jordan_example() -> None:
    """Guardrail sanity check from specification."""
    match = {
        "home": "Jordan",
        "away": "Opponent",
        "favTeam": "Opponent",
        "stage": "Group Stage",
        "homeScore": 0,
        "awayScore": 1,
        "layers": [{"source": ELO_SOURCE, "fav": "95.0%", "draw": "20.0%", "und": "5.0%"}],
        "advancedMetrics": {"xg": {"home": 1.42, "away": 0.85}},
    }
    chi_h, _ = compute_match_chi(match)
    expected = 0.598
    if not math.isclose(chi_h, expected, abs_tol=0.01):
        raise AssertionError(f"Jordan guardrail failed: got {chi_h}, expected {expected}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _verify_jordan_example()
    log.info("Jordan guardrail example passed (χ ≈ +0.598).")
    rolling = run()
    top = sorted(rolling.items(), key=lambda x: x[1], reverse=True)[:5]
    for team, chi in top:
        log.info("  %s → %s", team, format_chi(chi))
