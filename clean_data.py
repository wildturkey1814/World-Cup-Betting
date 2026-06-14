"""
Removes SRL simulator matches and fixes bad flag codes from data.json.
Run via GitHub Actions before fetch_scores.py.
"""
import json, tempfile, shutil, os, re

with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

before = len(data["matches"])

def is_srl(m):
    for field in ["home", "away", "favTeam", "undTeam", "id"]:
        v = str(m.get(field, "")).lower()
        if "srl" in v:
            return True
    return False

data["matches"] = [m for m in data["matches"] if not is_srl(m)]
after = len(data["matches"])

fd, tmp = tempfile.mkstemp(suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
shutil.move(tmp, "data.json")

print(f"Removed {before - after} SRL matches. {after} matches remain.")
