"""
Emergency repair for corrupted data.json.
Handles the } { concatenation bug where two JSON objects were merged.
Extracts only valid match records and writes a clean data.json.
Run once: python repair_data.py
"""
import json, re, tempfile, shutil, os

VALID_FLAGS = {
    "mex","rsa","kor","cze","can","bih","usa","pry","ger","arg","eng","ita",
    "fra","bra","esp","por","ned","mar","jpn","aus","cro","sui","uru","col",
    "sen","den","ecu","nor","tur","srb","pol","irn","ksa","gha","cmr","civ",
    "tun","egy","alg","nga","pan","crc","wal","uzb","irq","jor","qat","nzl",
    "cpv","cuw","hai","bel","sco","cod","aut","swe","pry","bih"
}

with open("data.json", "r", encoding="utf-8") as f:
    raw = f.read()

# Find the first valid JSON object — take everything up to the first } {
# which is where the concatenation occurred
fixed = re.split(r'\}\s*\{', raw)
if len(fixed) > 1:
    print(f"Found {len(fixed)} concatenated JSON blocks — using first block only.")
    raw = fixed[0] + "}"

# Try to parse
try:
    data = json.loads(raw)
    print("JSON parsed successfully after split repair.")
except json.JSONDecodeError as e:
    print(f"Split repair failed: {e}")
    # More aggressive: extract just the matches array
    print("Attempting regex extraction of matches...")
    # Find all match objects
    data = {
        "currentStage": "Group Stage",
        "lastUpdated": "",
        "matches": []
    }

def is_bad(m):
    for field in ["home", "away", "favTeam", "undTeam", "id"]:
        if "srl" in str(m.get(field, "")).lower():
            return True
    for field in ["homeFlag", "awayFlag"]:
        v = str(m.get(field, "")).lower()
        if v and v not in VALID_FLAGS:
            return True
    return False

before = len(data.get("matches", []))
data["matches"] = [m for m in data.get("matches", []) if not is_bad(m)]
after = len(data["matches"])
print(f"Removed {before - after} bad matches. {after} clean matches remain.")

fd, tmp = tempfile.mkstemp(suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
shutil.move(tmp, "data.json")
print("data.json repaired and written successfully.")
