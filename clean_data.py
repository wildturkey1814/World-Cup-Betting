"""
One-time cleanup: removes SRL simulator matches from data.json.
Run once manually: python clean_data.py
"""
import json, re, tempfile, shutil, os

with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

before = len(data["matches"])

def is_srl(m):
    for field in ["home", "away", "favTeam", "undTeam"]:
        v = str(m.get(field, ""))
        if "srl" in v.lower() or "simulator" in v.lower():
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
