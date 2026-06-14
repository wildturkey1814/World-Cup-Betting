"""
inject_keys.py — Injects API keys as JS constants into index.html
Runs in GitHub Actions after fetch_scores.py, before git commit.
Reads API_FOOTBALL_KEY and API_FOOTBALL_KEY2 from environment and
writes them as const declarations at the top of the <script> block.
"""
import os, re

KEY1 = os.environ.get("API_FOOTBALL_KEY",  "")
KEY2 = os.environ.get("API_FOOTBALL_KEY2", "")

with open("index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Replace or insert the key constants right after <script>
injection = f"""const API_FOOTBALL_KEY  = "{KEY1}";
const API_FOOTBALL_KEY2 = "{KEY2}";
"""

# Remove any existing injection
content = re.sub(
    r'const API_FOOTBALL_KEY\s*=.*?\n.*?const API_FOOTBALL_KEY2.*?\n',
    '', content, flags=re.DOTALL
)

# Insert after first <script> tag
content = content.replace("<script>\n", "<script>\n" + injection, 1)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(content)

print(f"Injected API-Football keys (key1={'SET' if KEY1 else 'MISSING'}, key2={'SET' if KEY2 else 'MISSING'})")
