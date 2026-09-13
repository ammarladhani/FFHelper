import sqlite3
import sys
from pathlib import Path


def print_rows(title, columns, rows):
    print(f"\n{'=' * 70}")
    print(title)
    print('=' * 70)

    if not rows:
        print("(no rows)")
        return

    print(" | ".join(columns))
    print("-" * 70)

    for row in rows:
        print(" | ".join(str(x) if x is not None else "NULL" for x in row))


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python inspect_db.py path/to/your.db")
        sys.exit(1)

    db_path = Path(sys.argv[1])

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)

    try:
        # ------------------------------------------------------------
        # Tables
        # ------------------------------------------------------------
        tables = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
        """).fetchall()

        print_rows(
            "TABLES",
            ["name"],
            tables,
        )

        # ------------------------------------------------------------
        # Schema
        # ------------------------------------------------------------
        for (table_name,) in tables:
            columns = conn.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()

            print_rows(
                f"SCHEMA: {table_name}",
                ["cid", "name", "type", "notnull", "default", "pk"],
                columns,
            )

        # ------------------------------------------------------------
        # Leagues
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT league_key, league_id, season
            FROM leagues
            ORDER BY league_key
        """).fetchall()

        print_rows(
            "LEAGUES",
            ["league_key", "league_id", "season"],
            rows,
        )

        # ------------------------------------------------------------
        # Teams
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT league_key, team_id, team_name, manager_name
            FROM teams
            ORDER BY league_key, team_id
        """).fetchall()

        print_rows(
            "TEAMS",
            ["league_key", "team_id", "team_name", "manager_name"],
            rows,
        )

        # ------------------------------------------------------------
        # Team 11 specifically
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT league_key, team_id, team_name, manager_name
            FROM teams
            WHERE team_id = 11
        """).fetchall()

        print_rows(
            "TEAM ID 11",
            ["league_key", "team_id", "team_name", "manager_name"],
            rows,
        )

        # ------------------------------------------------------------
        # DeMarcus Lawrence
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT
                player_id,
                name,
                position,
                pro_team_id,
                eligible_slots
            FROM players
            WHERE LOWER(name) LIKE '%demarcus%'
               OR LOWER(name) LIKE '%lawrence%'
            ORDER BY name
        """).fetchall()

        print_rows(
            "PLAYERS MATCHING DEMARCUS / LAWRENCE",
            ["player_id", "name", "position", "pro_team_id", "eligible_slots"],
            rows,
        )

        # ------------------------------------------------------------
        # Ownership records for Team 11
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT
                o.league_key,
                o.player_id,
                p.name,
                p.position,
                o.week,
                o.team_id
            FROM ownership o
            LEFT JOIN players p
                ON p.player_id = o.player_id
            WHERE o.team_id = 11
            ORDER BY o.league_key, o.week, p.name
        """).fetchall()

        print_rows(
            "OWNERSHIP FOR TEAM 11",
            [
                "league_key",
                "player_id",
                "name",
                "position",
                "week",
                "team_id",
            ],
            rows,
        )

        # ------------------------------------------------------------
        # Any ownership records for DeMarcus Lawrence
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT
                o.league_key,
                o.player_id,
                p.name,
                p.position,
                o.week,
                o.team_id
            FROM ownership o
            JOIN players p
                ON p.player_id = o.player_id
            WHERE LOWER(p.name) LIKE '%demarcus%'
               OR LOWER(p.name) LIKE '%lawrence%'
            ORDER BY o.league_key, o.week
        """).fetchall()

        print_rows(
            "OWNERSHIP RECORDS FOR DEMARCUS / LAWRENCE",
            [
                "league_key",
                "player_id",
                "name",
                "position",
                "week",
                "team_id",
            ],
            rows,
        )

        # ------------------------------------------------------------
        # Settings / lineup slots
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT
                league_key,
                slot_id,
                slot_name,
                count
            FROM league_settings
            ORDER BY league_key, slot_id
        """).fetchall()

        print_rows(
            "LEAGUE SETTINGS / SLOTS",
            ["league_key", "slot_id", "slot_name", "count"],
            rows,
        )

        # ------------------------------------------------------------
        # Projections for DeMarcus
        # ------------------------------------------------------------
        rows = conn.execute("""
            SELECT
                pr.league_key,
                pr.player_id,
                p.name,
                pr.week,
                pr.projected_points
            FROM projections pr
            JOIN players p
                ON p.player_id = pr.player_id
            WHERE LOWER(p.name) LIKE '%demarcus%'
               OR LOWER(p.name) LIKE '%lawrence%'
            ORDER BY pr.league_key, pr.week
        """).fetchall()

        print_rows(
            "PROJECTIONS FOR DEMARCUS / LAWRENCE",
            [
                "league_key",
                "player_id",
                "name",
                "week",
                "projected_points",
            ],
            rows,
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
