"""
Quick read-only inspector for the local SQLite database - schema dump,
plus optional filtered look at one team's roster or one player's
ownership/projection history.

Usage:
    python inspect_db.py fantasy.db
    python inspect_db.py fantasy.db --team 11
    python inspect_db.py fantasy.db --player "DeMarcus Lawrence"
    python inspect_db.py fantasy.db --team 11 --player Lawrence
"""

import argparse
import sqlite3
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


def show_schema(conn):
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    print_rows("TABLES", ["name"], tables)

    for (table_name,) in tables:
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        print_rows(f"SCHEMA: {table_name}", ["cid", "name", "type", "notnull", "default", "pk"], columns)


def show_leagues_and_teams(conn):
    rows = conn.execute("SELECT league_key, league_id, season FROM leagues ORDER BY league_key").fetchall()
    print_rows("LEAGUES", ["league_key", "league_id", "season"], rows)

    rows = conn.execute(
        "SELECT league_key, team_id, team_name, manager_name FROM teams ORDER BY league_key, team_id"
    ).fetchall()
    print_rows("TEAMS", ["league_key", "team_id", "team_name", "manager_name"], rows)

    rows = conn.execute(
        "SELECT league_key, slot_id, slot_name, count FROM league_settings ORDER BY league_key, slot_id"
    ).fetchall()
    print_rows("LEAGUE SETTINGS / SLOTS", ["league_key", "slot_id", "slot_name", "count"], rows)


def show_team(conn, team_id):
    rows = conn.execute(
        "SELECT league_key, team_id, team_name, manager_name FROM teams WHERE team_id = ?",
        (team_id,),
    ).fetchall()
    print_rows(f"TEAM ID {team_id}", ["league_key", "team_id", "team_name", "manager_name"], rows)

    rows = conn.execute(
        """
        SELECT o.league_key, o.player_id, p.name, p.position, o.week, o.team_id
        FROM ownership o
        LEFT JOIN players p ON p.player_id = o.player_id
        WHERE o.team_id = ?
        ORDER BY o.league_key, o.week, p.name
        """,
        (team_id,),
    ).fetchall()
    print_rows(
        f"OWNERSHIP FOR TEAM {team_id}",
        ["league_key", "player_id", "name", "position", "week", "team_id"],
        rows,
    )


def show_player(conn, name_fragment):
    like = f"%{name_fragment.lower()}%"

    rows = conn.execute(
        """
        SELECT player_id, name, position, pro_team_id, eligible_slots
        FROM players
        WHERE LOWER(name) LIKE ?
        ORDER BY name
        """,
        (like,),
    ).fetchall()
    print_rows(
        f"PLAYERS MATCHING '{name_fragment}'",
        ["player_id", "name", "position", "pro_team_id", "eligible_slots"],
        rows,
    )

    rows = conn.execute(
        """
        SELECT o.league_key, o.player_id, p.name, p.position, o.week, o.team_id
        FROM ownership o
        JOIN players p ON p.player_id = o.player_id
        WHERE LOWER(p.name) LIKE ?
        ORDER BY o.league_key, o.week
        """,
        (like,),
    ).fetchall()
    print_rows(
        f"OWNERSHIP RECORDS MATCHING '{name_fragment}'",
        ["league_key", "player_id", "name", "position", "week", "team_id"],
        rows,
    )

    rows = conn.execute(
        """
        SELECT pr.league_key, pr.player_id, p.name, pr.week, pr.projected_points
        FROM projections pr
        JOIN players p ON p.player_id = pr.player_id
        WHERE LOWER(p.name) LIKE ?
        ORDER BY pr.league_key, pr.week
        """,
        (like,),
    ).fetchall()
    print_rows(
        f"PROJECTIONS MATCHING '{name_fragment}'",
        ["league_key", "player_id", "name", "week", "projected_points"],
        rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db_path", type=Path, help="path to fantasy.db")
    parser.add_argument("--team", type=int, help="show roster/ownership for this team_id")
    parser.add_argument("--player", type=str, help="show info for players whose name matches this substring")
    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"Database not found: {args.db_path}")
        raise SystemExit(1)

    conn = sqlite3.connect(args.db_path)
    try:
        show_schema(conn)
        show_leagues_and_teams(conn)
        if args.team is not None:
            show_team(conn, args.team)
        if args.player:
            show_player(conn, args.player)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
