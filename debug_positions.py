import sqlite3

conn = sqlite3.connect("fantasy.db")
rows = conn.execute(
    """
    SELECT position, MIN(name) AS example_name, COUNT(*) AS n
    FROM players
    WHERE position LIKE 'pos_%'
    GROUP BY position
    ORDER BY position
    """
).fetchall()

for pos, name, n in rows:
    print(pos, "-", name, f"({n} players)")