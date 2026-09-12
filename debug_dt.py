import sqlite3

conn = sqlite3.connect("fantasy.db")

row = conn.execute("""
    SELECT player_id, name, position, eligible_slots
    FROM players
    WHERE player_id = 4035495
""").fetchone()

print(row)

conn.close()
