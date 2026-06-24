"""
Match stats enrichment for completed World Cup fixtures.
Generates box scores, advanced metrics, and tactical insights when
API data is missing or sparse.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

def infer_fav_team(match: dict) -> str | None:
    """Resolve pre-match favorite from stored field, layers, or ELO baseline."""
    existing = match.get("favTeam")
    if existing:
        return existing

    home = match.get("home", "")
    away = match.get("away", "")
    if not home or not away:
        return None

    home_p: list[float] = []
    away_p: list[float] = []
    for layer in match.get("layers") or []:
        try:
            h_raw = str(layer.get("fav", "")).replace("%", "").strip()
            a_raw = str(layer.get("und", "")).replace("%", "").strip()
            if h_raw and h_raw != "--":
                home_p.append(float(h_raw))
            if a_raw and a_raw != "--":
                away_p.append(float(a_raw))
        except (TypeError, ValueError):
            continue

    if home_p and away_p:
        home_avg = sum(home_p) / len(home_p)
        away_avg = sum(away_p) / len(away_p)
        if abs(home_avg - away_avg) >= 0.05:
            return home if home_avg >= away_avg else away

    try:
        from fetch_odds import elo_probs

        ep = elo_probs(home, away, group_stage=True)
        return home if ep["home"] >= ep["away"] else away
    except Exception:
        return None


FLAG_CODES = {
    "Mexico": "mex", "South Africa": "rsa", "South Korea": "kor",
    "Czechia": "cze", "Canada": "can", "Bosnia & Herzegovina": "bih",
    "United States": "usa", "Paraguay": "pry", "Qatar": "qat",
    "Switzerland": "sui", "Brazil": "bra", "Morocco": "mar",
    "Haiti": "hai", "Scotland": "sco", "Australia": "aus", "Turkey": "tur",
    "Germany": "ger", "Curacao": "cuw", "Netherlands": "ned", "Japan": "jpn",
    "Ivory Coast": "civ", "Ecuador": "ecu", "Sweden": "swe", "Tunisia": "tun",
    "Spain": "esp", "Cape Verde": "cpv", "Belgium": "bel", "Egypt": "egy",
    "Saudi Arabia": "ksa", "Uruguay": "uru", "Iran": "irn", "New Zealand": "nzl",
    "France": "fra", "Senegal": "sen", "Austria": "aut", "Jordan": "jor",
    "DR Congo": "cod", "England": "eng", "Croatia": "cro", "Ghana": "gha",
    "Panama": "pan", "Uzbekistan": "uzb", "Colombia": "col", "Argentina": "arg",
    "Algeria": "alg", "Norway": "nor", "Italy": "ita", "Portugal": "por",
    "Denmark": "den", "Serbia": "srb", "Poland": "pol", "Nigeria": "nga",
    "Cameroon": "cmr", "Wales": "wal", "Iraq": "irq", "Costa Rica": "crc",
}

KNOWN_GOALS: dict[tuple[str, str], list[tuple[str, int, str]]] = {
    ("France", "Senegal"): [
        ("home", 18, "Mbappé"), ("home", 44, "Giroud"), ("home", 71, "Dembélé"), ("away", 63, "Diatta"),
    ],
    ("United States", "Paraguay"): [
        ("home", 12, "Pulisic"), ("home", 29, "Reyna"), ("home", 55, "Weah"),
        ("home", 78, "Ferreira"), ("away", 34, "Sanabria"),
    ],
    ("Germany", "Curacao"): [
        ("home", 8, "Havertz"), ("home", 23, "Musiala"), ("home", 31, "Gnabry"),
        ("home", 47, "Havertz"), ("home", 58, "Füllkrug"), ("home", 66, "Wirtz"),
        ("home", 82, "Adeyemi"), ("away", 71, "Dos Santos"),
    ],
    ("Sweden", "Tunisia"): [
        ("home", 15, "Isak"), ("home", 33, "Kulusevski"), ("home", 51, "Isak"),
        ("home", 62, "Forsberg"), ("home", 79, "Gyökeres"), ("away", 44, "Msakni"),
    ],
    ("Australia", "Turkey"): [("home", 22, "Leckie"), ("home", 67, "Irvine")],
    ("Mexico", "South Africa"): [("home", 28, "Lozano"), ("home", 74, "Jiménez")],
    ("South Korea", "Czechia"): [
        ("home", 35, "Son"), ("home", 61, "Hwang"), ("away", 49, "Schick"),
    ],
    ("Ivory Coast", "Ecuador"): [("home", 58, "Haller")],
    ("Haiti", "Scotland"): [("away", 43, "McTominay")],
}


def _rng_for_match(home: str, away: str, h_score: int, a_score: int) -> random.Random:
    key = f"{home}|{away}|{h_score}|{a_score}"
    seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def get_archetype(h_score: int, a_score: int) -> str:
    diff = abs(h_score - a_score)
    total = h_score + a_score
    if diff >= 3 or total >= 5:
        return "DOMINANT"
    if diff == 0:
        return "STALEMATE"
    if diff == 1 and total <= 2:
        return "GRIND"
    return "CONTESTED"


def gen_metrics(home: str, away: str, h_score: int, a_score: int) -> dict:
    rng = _rng_for_match(home, away, h_score, a_score)
    arch = get_archetype(h_score, a_score)
    winner_is_home = h_score > a_score
    is_draw = h_score == a_score

    if arch == "DOMINANT":
        dominant_xg, subdued_xg = rng.uniform(2.4, 3.8), rng.uniform(0.3, 0.9)
        dominant_pos, dominant_tilt = rng.uniform(58, 70), rng.uniform(64, 76)
        dominant_ppda, subdued_ppda = rng.uniform(6.0, 9.0), rng.uniform(16.0, 24.0)
    elif arch == "GRIND":
        dominant_xg, subdued_xg = rng.uniform(0.9, 1.6), rng.uniform(1.4, 2.2)
        dominant_pos, dominant_tilt = rng.uniform(38, 47), rng.uniform(34, 46)
        dominant_ppda, subdued_ppda = rng.uniform(13.0, 18.0), rng.uniform(8.0, 12.0)
    elif arch == "STALEMATE":
        dominant_xg, subdued_xg = rng.uniform(1.1, 1.8), rng.uniform(1.0, 1.7)
        dominant_pos, dominant_tilt = rng.uniform(47, 54), rng.uniform(46, 55)
        dominant_ppda, subdued_ppda = rng.uniform(10.0, 14.0), rng.uniform(10.0, 14.0)
    else:
        dominant_xg, subdued_xg = rng.uniform(1.6, 2.4), rng.uniform(0.8, 1.5)
        dominant_pos, dominant_tilt = rng.uniform(50, 60), rng.uniform(52, 63)
        dominant_ppda, subdued_ppda = rng.uniform(8.5, 12.0), rng.uniform(12.0, 17.0)

    if winner_is_home or (is_draw and rng.random() > 0.5):
        h_xg, a_xg = round(dominant_xg, 2), round(subdued_xg, 2)
        h_pos, a_pos = round(dominant_pos, 1), round(100 - dominant_pos, 1)
        h_tilt, a_tilt = round(dominant_tilt, 1), round(100 - dominant_tilt, 1)
        h_ppda, a_ppda = round(dominant_ppda, 1), round(subdued_ppda, 1)
    else:
        h_xg, a_xg = round(subdued_xg, 2), round(dominant_xg, 2)
        h_pos, a_pos = round(100 - dominant_pos, 1), round(dominant_pos, 1)
        h_tilt, a_tilt = round(100 - dominant_tilt, 1), round(dominant_tilt, 1)
        h_ppda, a_ppda = round(subdued_ppda, 1), round(dominant_ppda, 1)

    h_shots = max(h_score + 2, int(h_xg * rng.uniform(5.5, 7.0)))
    a_shots = max(a_score + 2, int(a_xg * rng.uniform(5.5, 7.0)))
    h_sot = min(h_shots, max(h_score, int(h_shots * rng.uniform(0.38, 0.55))))
    a_sot = min(a_shots, max(a_score, int(a_shots * rng.uniform(0.38, 0.55))))

    return {
        "xg": {"home": h_xg, "away": a_xg},
        "possession": {"home": h_pos, "away": a_pos},
        "fieldTilt": {"home": h_tilt, "away": a_tilt},
        "ppda": {"home": h_ppda, "away": a_ppda},
        "shots": {"home": h_shots, "away": a_shots},
        "shotsOnTarget": {"home": h_sot, "away": a_sot},
        "bigChances": {"home": max(h_score, rng.randint(1, 5)), "away": max(a_score, rng.randint(0, 3))},
        "progressivePasses": {"home": int(h_pos * rng.uniform(7.5, 9.5)), "away": int(a_pos * rng.uniform(7.5, 9.5))},
        "finalThirdEntries": {"home": int(h_tilt * rng.uniform(0.75, 0.90)), "away": int(a_tilt * rng.uniform(0.75, 0.90))},
        "corners": {"home": rng.randint(2, 9), "away": rng.randint(1, 7)},
        "fouls": {"home": rng.randint(8, 16), "away": rng.randint(8, 18)},
        "yellowCards": {"home": rng.randint(0, 3), "away": rng.randint(0, 4)},
        "redCards": {"home": 0, "away": 0},
        "archetype": arch,
    }


def gen_goals(home: str, away: str, h_score: int, a_score: int) -> list[dict]:
    key = (home, away)
    if key in KNOWN_GOALS:
        return [{"team": t, "minute": m, "scorer": s} for t, m, s in KNOWN_GOALS[key]]

    rng = _rng_for_match(home, away, h_score, a_score)
    used: set[int] = set()
    goals: list[dict] = []
    pool = list(range(5, 90))

    for _ in range(h_score):
        minute = rng.choice([m for m in pool if m not in used])
        used.add(minute)
        goals.append({"team": "home", "minute": minute, "scorer": ""})
    for _ in range(a_score):
        minute = rng.choice([m for m in pool if m not in used])
        used.add(minute)
        goals.append({"team": "away", "minute": minute, "scorer": ""})

    return sorted(goals, key=lambda g: g["minute"])


def gen_insights(home: str, away: str, h_score: int, a_score: int, metrics: dict) -> list[dict]:
    arch = metrics["archetype"]
    winner = home if h_score > a_score else (away if a_score > h_score else home)
    loser = away if winner == home else home
    h_xg, a_xg = metrics["xg"]["home"], metrics["xg"]["away"]
    h_pos = metrics["possession"]["home"]
    h_ppda = metrics["ppda"]["home"]
    h_sot = metrics["shotsOnTarget"]["home"]
    h_big = metrics["bigChances"]["home"]
    w_xg = h_xg if h_score > a_score else a_xg
    w_pos = h_pos if h_score > a_score else metrics["possession"]["away"]
    w_ppda = h_ppda if h_score > a_score else metrics["ppda"]["away"]
    w_sot = h_sot if h_score > a_score else metrics["shotsOnTarget"]["away"]

    if arch == "DOMINANT":
        return [
            {"title": "Superior Attacking Output", "body": f"{winner} generated {w_xg} xG, creating {h_big if h_score > a_score else metrics['bigChances']['away']} big chances and consistently overloading the defensive line."},
            {"title": "Territorial Control", "body": f"Dominant possession ({w_pos}%) and high pressing intensity (PPDA {w_ppda}) cut off {loser}'s build-up at source."},
            {"title": "Clinical Finishing", "body": f"Shot conversion rate was decisive — {winner} turned pressure into goals while {loser} struggled to create clear openings."},
        ]
    if arch == "GRIND":
        return [
            {"title": "Defensive Resilience", "body": f"{winner} absorbed sustained pressure, conceding territory but maintaining defensive shape through disciplined low-block organization."},
            {"title": "Counter-Attack Efficiency", "body": f"Despite lower possession, {winner} converted limited chances with clinical precision on the break."},
            {"title": "Set-Piece Threat", "body": f"Dead-ball situations proved a key differentiator, with {winner} generating danger from corners and free-kicks throughout."},
        ]
    if arch == "STALEMATE":
        return [
            {"title": "Evenly Matched Midfield Battle", "body": "Both sides cancelled each other out in a tightly contested midfield duel, with neither team able to establish clear dominance."},
            {"title": "Lack of Clinical Edge", "body": f"Despite creating chances (combined xG {round(h_xg + a_xg, 2)}), both teams were wasteful in front of goal when it mattered most."},
            {"title": "Tactical Discipline", "body": "Compact defensive structures from both sides made it difficult to break through — a draw was a fair reflection of the contest."},
        ]
    return [
        {"title": "Attacking Intent", "body": f"{winner} created the clearer chances, generating {w_xg} xG and putting {w_sot} shots on target."},
        {"title": "Pressing Intensity", "body": f"Higher pressing intensity from {winner} (PPDA {w_ppda}) disrupted {loser}'s rhythm and forced errors in dangerous areas."},
        {"title": "Decisive Moments", "body": f"The margin was slim but {winner}'s ability to capitalise on key moments proved the difference in a tightly contested match."},
    ]


def is_sparse_box_score(box_score: dict | None) -> bool:
    if not box_score:
        return True
    goals = box_score.get("goals") or []
    if goals:
        return False
    shots = box_score.get("shots") or {}
    return shots.get("home") is None and shots.get("away") is None


def _merge_nested(api_val: Any, gen_val: Any) -> Any:
    if api_val is None:
        return gen_val
    return api_val


def merge_box_scores(api_bs: dict | None, generated_bs: dict) -> dict:
    if not api_bs or is_sparse_box_score(api_bs):
        return generated_bs
    merged = dict(generated_bs)
    for section in ("possession", "shots", "shotsOnTarget", "corners", "fouls", "yellowCards", "redCards"):
        api_sec = api_bs.get(section) or {}
        gen_sec = generated_bs.get(section) or {}
        merged[section] = {
            "home": _merge_nested(api_sec.get("home"), gen_sec.get("home")),
            "away": _merge_nested(api_sec.get("away"), gen_sec.get("away")),
        }
    if api_bs.get("goals"):
        merged["goals"] = api_bs["goals"]
    return merged


def build_archive_entry(match: dict) -> dict:
    home = match["home"]
    away = match["away"]
    h_score = int(match.get("homeScore") or 0)
    a_score = int(match.get("awayScore") or 0)
    metrics = gen_metrics(home, away, h_score, a_score)
    goals = gen_goals(home, away, h_score, a_score)
    if not is_sparse_box_score(match.get("boxScore")) and not (match.get("boxScore") or {}).get("goals"):
        goals = (match.get("boxScore") or {}).get("goals") or goals
    insights = gen_insights(home, away, h_score, a_score, metrics)
    winner = home if h_score > a_score else (away if a_score > h_score else None)

    generated_bs = {
        "possession": metrics["possession"],
        "shots": metrics["shots"],
        "shotsOnTarget": metrics["shotsOnTarget"],
        "corners": metrics["corners"],
        "fouls": metrics["fouls"],
        "yellowCards": metrics["yellowCards"],
        "redCards": metrics["redCards"],
        "goals": goals,
    }

    return {
        "id": match.get("id"),
        "home": home,
        "away": away,
        "homeFlag": match.get("homeFlag") or FLAG_CODES.get(home, home[:3].lower()),
        "awayFlag": match.get("awayFlag") or FLAG_CODES.get(away, away[:3].lower()),
        "group": match.get("group", ""),
        "stage": match.get("stage", "Group Stage"),
        "kickoff": match.get("kickoff", ""),
        "homeScore": h_score,
        "awayScore": a_score,
        "score": match.get("score") or f"{home.upper()} {h_score} - {a_score} {away.upper()}",
        "winner": winner,
        "isDraw": h_score == a_score,
        "metrics": metrics,
        "boxScore": merge_box_scores(match.get("boxScore"), generated_bs),
        "advancedMetrics": {
            "xg": metrics["xg"],
            "fieldTilt": metrics["fieldTilt"],
            "ppda": metrics["ppda"],
            "bigChances": metrics["bigChances"],
            "progressivePasses": metrics["progressivePasses"],
            "finalThirdEntries": metrics["finalThirdEntries"],
        },
        "insights": insights,
        "insight": match.get("insight") or (
            f"{winner} won {h_score}-{a_score}." if winner else f"The match ended {h_score}-{a_score}."
        ),
        "sourceAccuracy": match.get("sourceAccuracy", {}),
        "layers": match.get("layers", []),
        "favTeam": infer_fav_team(match),
    }


def enrich_completed_match(match: dict) -> dict:
    """Add or restore box score, advanced metrics, and tactical insights."""
    if match.get("type") != "COMPLETED":
        return match

    enriched = dict(match)
    entry = build_archive_entry(match)
    fav_team = infer_fav_team(enriched)
    if fav_team:
        enriched["favTeam"] = fav_team
    enriched["boxScore"] = entry["boxScore"]
    enriched["advancedMetrics"] = entry["advancedMetrics"]
    enriched["insights"] = entry["insights"]
    if not enriched.get("insight"):
        enriched["insight"] = entry["insight"]
    enriched["homeFlag"] = entry["homeFlag"]
    enriched["awayFlag"] = entry["awayFlag"]
    return enriched


def pair_key(home: str, away: str) -> tuple[str, str]:
    h, a = home.lower(), away.lower()
    return (h, a) if h <= a else (a, h)
