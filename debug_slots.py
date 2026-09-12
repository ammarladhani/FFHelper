import sqlite3

conn = sqlite3.connect("fantasy.db")

print("=== LEAGUE SLOTS ===")
rows = conn.execute("""
    SELECT slot_id, slot_name, count
    FROM league_settings
    WHERE league_key = 'my_league'
    ORDER BY slot_id
""").fetchall()

for row in rows:
    print(row)

conn.close()
