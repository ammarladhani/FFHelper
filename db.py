"""
SQLite storage layer. One local database file holds every league.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
    league_key   TEXT PRIMARY KEY,   -- the short slug from config.py, e.g. "my_league"
    league_id    INTEGER NOT NULL,
    season       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    league_key   TEXT NOT NULL,
    team_id      INTEGER NOT NULL,
    team_name    TEXT,
    manager_name TEXT,
    PRIMARY KEY (league_key, team_id)
);

CREATE TABLE IF NOT EXISTS league_settings (
    league_key   TEXT NOT NULL,
    slot_id      INTEGER NOT NULL,
    slot_name    TEXT,
    count        INTEGER NOT NULL,
    PRIMARY KEY (league_key, slot_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id    INTEGER PRIMARY KEY,
    name         TEXT,
    position     TEXT,
    pro_team_id  INTEGER
);

CREATE TABLE IF NOT EXISTS projections (
    league_key       TEXT NOT NULL,
    player_id        INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    projected_points REAL,
    PRIMARY KEY (league_key, player_id, week)
);

CREATE TABLE IF NOT EXISTS ownership (
    league_key   TEXT NOT NULL,
    player_id    INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    team_id      INTEGER,            -- NULL = free agent
    PRIMARY KEY (league_key, player_id, week)
);
"""


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_league(conn, league_key, league_id, season):
    conn.execute(
        "INSERT INTO leagues (league_key, league_id, season) VALUES (?, ?, ?) "
        "ON CONFLICT(league_key) DO UPDATE SET league_id=excluded.league_id, season=excluded.season",
        (league_key, league_id, season),
    )


def upsert_team(conn, league_key, team_id, team_name, manager_name):
    conn.execute(
        "INSERT INTO teams (league_key, team_id, team_name, manager_name) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, team_id) DO UPDATE SET team_name=excluded.team_name, manager_name=excluded.manager_name",
        (league_key, team_id, team_name, manager_name),
    )


def upsert_setting(conn, league_key, slot_id, slot_name, count):
    conn.execute(
        "INSERT INTO league_settings (league_key, slot_id, slot_name, count) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, slot_id) DO UPDATE SET count=excluded.count",
        (league_key, slot_id, slot_name, count),
    )


def upsert_player(conn, player_id, name, position, pro_team_id):
    conn.execute(
        "INSERT INTO players (player_id, name, position, pro_team_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(player_id) DO UPDATE SET name=excluded.name, position=excluded.position, pro_team_id=excluded.pro_team_id",
        (player_id, name, position, pro_team_id),
    )


def upsert_projection(conn, league_key, player_id, week, projected_points):
    conn.execute(
        "INSERT INTO projections (league_key, player_id, week, projected_points) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, player_id, week) DO UPDATE SET projected_points=excluded.projected_points",
        (league_key, player_id, week, projected_points),
    )


def upsert_ownership(conn, league_key, player_id, week, team_id):
    conn.execute(
        "INSERT INTO ownership (league_key, player_id, week, team_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, player_id, week) DO UPDATE SET team_id=excluded.team_id",
        (league_key, player_id, week, team_id),
    )
