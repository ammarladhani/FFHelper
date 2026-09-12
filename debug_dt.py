import sqlite3

conn = sqlite3.connect("fantasy.db")

print("=== Derrick Brown ===")

rows = conn.execute("""
    SELECT player_id, name, position, pro_team_id
    FROM players
    WHERE LOWER(name) LIKE '%derrick brown%'
""").fetchall()

for row in rows:
    print(row)

print("\n=== Players with 'Brown' in name ===")

rows = conn.execute("""
    SELECT player_id, name, position, pro_team_id
    FROM players
    WHERE LOWER(name) LIKE '%brown%'
    ORDER BY name
""").fetchall()

for row in rows:
    print(row)

conn.close()
