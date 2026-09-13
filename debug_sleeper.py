import requests
import json

SEASON = 2026
WEEK = 2

url = f"https://api.sleeper.app/projections/nfl/{SEASON}/{WEEK}?season_type=regular"
data = requests.get(url, timeout=30).json()

# Find the first QB with a nonzero half-PPR projection, for a clean real example
for entry in data:
    player = entry.get("player") or {}
    positions = player.get("fantasy_positions") or []
    stats = entry.get("stats") or {}
    if "QB" in positions and stats.get("pts_half_ppr", 0) > 5:
        print(json.dumps(entry, indent=2))
        break