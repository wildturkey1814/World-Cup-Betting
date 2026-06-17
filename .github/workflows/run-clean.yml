"""
One-time cleanup: removes SRL simulator/ghost matches from data.json.
Only removes records where home or away team name contains 'SRL' or 'Srl'.
Does not touch any other fields or real match records.
"""
import json, tempfile, shutil, os

with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

before = len(data["matches"])

def is_srl(m):
    for field in ["home", "away"]:
        v = str(m.get(field, ""))
        if "SRL" in v or "Srl" in v:
            return True
    return False

removed = [m for m in data["matches"] if is_srl(m)]
data["matches"] = [m for m in data["matches"] if not is_srl(m)]
after = len(data["matches"])

for m in removed:
    print(f"  Removed: {m.get('home')} vs {m.get('away')}")

fd, tmp = tempfile.mkstemp(suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
shutil.move(tmp, "data.json")

print(f"Removed {before - after} SRL ghost matches. {after} remain.")
