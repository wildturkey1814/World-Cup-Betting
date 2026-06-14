"""
Removes SRL simulator matches and bad flag codes from data.json.
"""
import json, tempfile, shutil, os

VALID_FLAGS = {
    "mex","rsa","kor","cze","can","bih","usa","pry","ger","arg","eng","ita",
    "fra","bra","esp","por","ned","mar","jpn","aus","cro","sui","uru","col",
    "sen","den","ecu","nor","tur","srb","pol","irn","ksa","gha","cmr","civ",
    "tun","egy","alg","nga","pan","crc","wal","uzb","irq","jor","qat","nzl",
    "cpv","cuw","hai","bel","sco","cod","aut","swe","pry"
}

with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

before = len(data["matches"])

def is_bad(m):
    # Check for SRL in any name field
    for field in ["home", "away", "favTeam", "undTeam", "id"]:
        v = str(m.get(field, "")).lower()
        if "srl" in v:
            return True
    # Check for invalid flag codes
    for field in ["homeFlag", "awayFlag"]:
        v = str(m.get(field, "")).lower()
        if v and v not in VALID_FLAGS:
            return True
    return False

removed = [m for m in data["matches"] if is_bad(m)]
data["matches"] = [m for m in data["matches"] if not is_bad(m)]
after = len(data["matches"])

for m in removed:
    print(f"  Removed: {m.get('home')} vs {m.get('away')} (flags: {m.get('homeFlag')}/{m.get('awayFlag')})")

fd, tmp = tempfile.mkstemp(suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
shutil.move(tmp, "data.json")

print(f"Removed {before - after} bad matches. {after} remain.")
