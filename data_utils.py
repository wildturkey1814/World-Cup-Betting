"""
Shared utilities for World Cup data ingestion scripts.
Handles cross-platform timestamps, SRL ghost filtering, safe I/O, and merge logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

OUTPUT_FILE = "data.json"

# Official 2026 World Cup participants (canonical names used across scripts)
KNOWN_WC_TEAMS = frozenset({
    "Mexico", "South Africa", "South Korea", "Czechia", "Canada",
    "Bosnia & Herzegovina", "United States", "Paraguay", "Qatar",
    "Switzerland", "Brazil", "Morocco", "Haiti", "Scotland", "Australia",
    "Turkey", "Germany", "Curacao", "Netherlands", "Japan", "Ivory Coast",
    "Ecuador", "Sweden", "Tunisia", "Spain", "Cape Verde", "Belgium",
    "Egypt", "Saudi Arabia", "Uruguay", "Iran", "New Zealand", "France",
    "Senegal", "Austria", "Jordan", "DR Congo", "England", "Croatia",
    "Ghana", "Panama", "Uzbekistan", "Colombia", "Italy", "Argentina",
    "Portugal", "Denmark", "Norway", "Serbia", "Poland", "Nigeria",
    "Cameroon", "Algeria", "Costa Rica", "Wales", "Iraq",
})

VALID_FLAGS = frozenset({
    "mex", "rsa", "kor", "cze", "can", "bih", "usa", "pry", "ger", "arg",
    "eng", "ita", "fra", "bra", "esp", "por", "ned", "mar", "jpn", "aus",
    "cro", "sui", "uru", "col", "sen", "den", "ecu", "nor", "tur", "srb",
    "pol", "irn", "ksa", "gha", "cmr", "civ", "tun", "egy", "alg", "nga",
    "pan", "crc", "wal", "uzb", "irq", "jor", "qat", "nzl", "cpv", "cuw",
    "hai", "bel", "sco", "cod", "aut", "swe",
})

# Fields preserved when merging API odds into an existing match record
PRESERVE_FIELDS = (
    "type", "score", "homeScore", "awayScore", "liveScore", "boxScore",
    "advancedMetrics", "insights", "sourceAccuracy", "insight",
    "kalshiWinProbHome", "kalshiWinProbAway",
    "polymarketWinProbHome", "polymarketWinProbAway",
    "homeMomentumFactor", "awayMomentumFactor",
)

SRL_PATTERN = re.compile(r"\bsrl\b|(?:^|\s)srl(?:\s|$)|simulator|simulated", re.IGNORECASE)


def format_utc_display(dt: datetime) -> str:
    """Cross-platform UTC timestamp for lastUpdated fields."""
    day = dt.day
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%B')} {day}, {dt.strftime('%Y')} · {hour}:{dt.strftime('%M')} {ampm} UTC"


def format_edt_meta(dt: datetime, group: str) -> str:
    """Cross-platform kickoff meta string (EDT)."""
    from datetime import timedelta
    edt = dt - timedelta(hours=4)
    hour = edt.hour % 12 or 12
    ampm = "AM" if edt.hour < 12 else "PM"
    return f"{edt.strftime('%B')} {edt.day} · {hour}:{edt.strftime('%M')} {ampm} EDT · {group}"


def normalize_match_id(raw_id: Any) -> str:
    """Normalize fixture IDs to bare numeric strings (matches football-data.org)."""
    s = str(raw_id or "").strip()
    if s.startswith("match_"):
        s = s[6:]
    return s


def id_variants(match_id: Any) -> set[str]:
    """All ID forms for deduplication across data sources."""
    bare = normalize_match_id(match_id)
    if not bare:
        return set()
    return {bare, f"match_{bare}"}


def is_srl_text(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    if SRL_PATTERN.search(text):
        return True
    # OddsPapi sometimes labels sim leagues without the word SRL
    lower = text.lower()
    return "sim league" in lower or "simulation" in lower


def is_known_wc_team(name: str) -> bool:
    return bool(name) and name in KNOWN_WC_TEAMS


def is_ghost_match(match: dict) -> bool:
    """True for SRL simulator fixtures and other invalid records."""
    for field in ("home", "away", "favTeam", "undTeam", "id"):
        if is_srl_text(match.get(field)):
            return True

    home = str(match.get("home") or "")
    away = str(match.get("away") or "")

    if home and not is_known_wc_team(home):
        return True
    if away and not is_known_wc_team(away):
        return True

    for field in ("homeFlag", "awayFlag"):
        flag = str(match.get(field) or "").lower()
        if flag and flag not in VALID_FLAGS:
            return True

    return False


def filter_ghost_matches(matches: list[dict]) -> list[dict]:
    clean = [m for m in matches if not is_ghost_match(m)]
    removed = len(matches) - len(clean)
    if removed:
        log.info("Filtered %d ghost/invalid match(es).", removed)
    return clean


def match_pair_key(match: dict) -> tuple[str, str]:
    home = str(match.get("home") or "")
    away = str(match.get("away") or "")
    if home <= away:
        return home, away
    return away, home


def repair_raw_json(raw: str) -> str:
    """Fix concatenated JSON objects (} { bug from concurrent writes)."""
    if re.search(r"\}\s*\{", raw):
        log.warning("Detected concatenated JSON — using first object only.")
        return re.split(r"\}\s*\{", raw, maxsplit=1)[0] + "}"
    return raw


def load_data(path: str = OUTPUT_FILE) -> dict:
    if not os.path.exists(path):
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        raw = repair_raw_json(raw)
        data = json.loads(raw)
        if not isinstance(data.get("matches"), list):
            data["matches"] = []
        data["matches"] = filter_ghost_matches(data["matches"])
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s) — starting fresh.", path, exc)
        return {"currentStage": "Group Stage", "lastUpdated": "", "matches": []}


def atomic_write(path: str, data: dict) -> None:
    data = dict(data)
    data["matches"] = filter_ghost_matches(data.get("matches") or [])
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.move(tmp, path)
        log.info("Wrote %s (%d matches).", path, len(data["matches"]))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def merge_odds_record(existing: dict, incoming: dict) -> dict:
    """Merge fresh odds layers into an existing match without losing live state."""
    merged = dict(incoming)
    if existing.get("id"):
        merged["id"] = normalize_match_id(existing["id"])
    for field in PRESERVE_FIELDS:
        if existing.get(field) is not None:
            merged[field] = existing[field]
    # Never downgrade a finished or live match to upcoming
    if existing.get("type") in ("COMPLETED", "IN_PLAY"):
        merged["type"] = existing["type"]
    return merged


def is_fixture_done(match: dict, done_ids: set[str]) -> bool:
    return bool(id_variants(match.get("id")) & done_ids)


def merge_odds_fetch(existing_data: dict, api_records: list[dict]) -> list[dict]:
    """
    Merge OddsPapi fixtures into existing matches.
    Preserves COMPLETED, IN_PLAY, scores, and market enrichments.
    """
    existing = filter_ghost_matches(existing_data.get("matches") or [])
    api_records = filter_ghost_matches(api_records)

    done_ids: set[str] = set()
    for m in existing:
        if m.get("type") == "COMPLETED":
            done_ids |= id_variants(m.get("id"))

    api_by_pair: dict[tuple[str, str], dict] = {}
    for rec in api_records:
        if is_fixture_done(rec, done_ids):
            continue
        api_by_pair[match_pair_key(rec)] = rec

    output: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for old in existing:
        pair = match_pair_key(old)
        seen_pairs.add(pair)

        if old.get("type") == "COMPLETED":
            output.append(old)
            continue

        if old.get("type") == "IN_PLAY":
            output.append(old)
            continue

        incoming = api_by_pair.get(pair)
        if incoming:
            output.append(merge_odds_record(old, incoming))
        else:
            output.append(old)

    for pair, rec in api_by_pair.items():
        if pair not in seen_pairs:
            output.append(rec)
            seen_pairs.add(pair)

    return filter_ghost_matches(output)


def filter_participants_map(raw_map: dict) -> dict:
    """Drop SRL / simulator entries from OddsPapi participant lookups."""
    return {
        str(k): v for k, v in raw_map.items()
        if not is_srl_text(v)
    }


def is_real_fixture(
    fixture: dict,
    pmap: dict | None = None,
    normalize=None,
) -> bool:
    """Reject SRL/simulator fixtures before record building."""
    names = [
        fixture.get("participant1Name"),
        fixture.get("participant2Name"),
    ]
    if pmap:
        names.extend([
            pmap.get(str(fixture.get("participant1Id", ""))),
            pmap.get(str(fixture.get("participant2Id", ""))),
        ])
    if any(is_srl_text(n) for n in names if n):
        return False

    if normalize:
        resolved = []
        for i, key in enumerate(("participant1Name", "participant2Name"), start=1):
            raw = fixture.get(key) or (pmap or {}).get(str(fixture.get(f"participant{i}Id", ""))) or ""
            name = normalize(str(raw).strip()) if raw else ""
            if name:
                resolved.append(name)
        if len(resolved) == 2:
            if not is_known_wc_team(resolved[0]) or not is_known_wc_team(resolved[1]):
                return False
    return True
