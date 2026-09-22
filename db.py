"""
SQLite storage layer. One local database file holds every league.
"""

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
    league_key   TEXT PRIMARY KEY,
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
    player_id      TEXT PRIMARY KEY,
    name           TEXT,
    position       TEXT,
    pro_team_id    INTEGER,
    eligible_slots TEXT
);

CREATE TABLE IF NOT EXISTS projections (
    league_key       TEXT NOT NULL,
    player_id        TEXT NOT NULL,
    week             INTEGER NOT NULL,
    projected_points REAL,
    PRIMARY KEY (league_key, player_id, week)
);

CREATE TABLE IF NOT EXISTS ownership (
    league_key   TEXT NOT NULL,
    player_id    TEXT NOT NULL,
    week         INTEGER NOT NULL,
    team_id      INTEGER,
    reserved     TEXT,  -- 'IR' / 'TAXI' / NULL (normal active roster spot)
    PRIMARY KEY (league_key, player_id, week)
);

-- Regular-season head-to-head matchups. One row per matchup (byes have no
-- row). score_a/score_b are ACTUAL final scores for weeks that have already
-- been played, NULL for weeks still to come (those get projected instead).
CREATE TABLE IF NOT EXISTS schedule (
    league_key   TEXT NOT NULL,
    week         INTEGER NOT NULL,
    team_a       INTEGER NOT NULL,
    team_b       INTEGER NOT NULL,
    score_a      REAL,
    score_b      REAL,
    PRIMARY KEY (league_key, week, team_a)
);
"""


def get_conn(db_path: str) -> sqlite3.Connection:
    # check_same_thread=False: Streamlit's caching/rerun model can execute
    # a cached resource (like this connection) from a different thread
    # than the one that created it. This app only ever does one thing at
    # a time per connection (no real concurrent access), so relaxing
    # SQLite's default same-thread check is safe here.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(players)").fetchall()
    }

    if "eligible_slots" not in columns:
        conn.execute("ALTER TABLE players ADD COLUMN eligible_slots TEXT")
    
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(ownership)").fetchall()
    }
    if "reserved" not in columns:
        conn.execute("ALTER TABLE ownership ADD COLUMN reserved TEXT")
    conn.commit()


def upsert_league(conn, league_key, league_id, season):
    conn.execute(
        "INSERT INTO leagues (league_key, league_id, season) VALUES (?, ?, ?) "
        "ON CONFLICT(league_key) DO UPDATE SET "
        "league_id=excluded.league_id, season=excluded.season",
        (league_key, league_id, season),
    )


def upsert_team(conn, league_key, team_id, team_name, manager_name):
    conn.execute(
        "INSERT INTO teams "
        "(league_key, team_id, team_name, manager_name) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, team_id) DO UPDATE SET "
        "team_name=excluded.team_name, "
        "manager_name=excluded.manager_name",
        (league_key, team_id, team_name, manager_name),
    )


def upsert_setting(conn, league_key, slot_id, slot_name, count):
    conn.execute(
        "INSERT INTO league_settings "
        "(league_key, slot_id, slot_name, count) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, slot_id) DO UPDATE SET "
        "count=excluded.count",
        (league_key, slot_id, slot_name, count),
    )


def upsert_player(
    conn,
    player_id,
    name,
    position,
    pro_team_id,
    eligible_slots=None,
):
    eligible_slots_json = (
        json.dumps(sorted(set(eligible_slots)))
        if eligible_slots is not None
        else None
    )

    conn.execute(
        """
        INSERT INTO players
            (player_id, name, position, pro_team_id, eligible_slots)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            name=excluded.name,
            position=excluded.position,
            pro_team_id=excluded.pro_team_id,
            eligible_slots=excluded.eligible_slots
        """,
        (
            player_id,
            name,
            position,
            pro_team_id,
            eligible_slots_json,
        ),
    )


def upsert_projection(conn, league_key, player_id, week, projected_points):
    conn.execute(
        "INSERT INTO projections "
        "(league_key, player_id, week, projected_points) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(league_key, player_id, week) DO UPDATE SET "
        "projected_points=excluded.projected_points",
        (league_key, player_id, week, projected_points),
    )


def upsert_ownership(conn, league_key, player_id, week, team_id, reserved=None):
    conn.execute(
        "INSERT INTO ownership "
        "(league_key, player_id, week, team_id, reserved) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(league_key, player_id, week) DO UPDATE SET "
        "team_id=excluded.team_id, reserved=excluded.reserved",
        (league_key, player_id, week, team_id, reserved),
    )


def replace_schedule(conn, league_key, matchups):
    """
    Replace a league's entire stored regular-season schedule.

    matchups: iterable of (week, team_a, team_b, score_a, score_b) where
    the two scores are the actual final scores for a week that's already
    been played and None otherwise. Deleting first (rather than upserting)
    keeps re-ingestion idempotent even if the platform reshuffled a
    matchup.
    """
    conn.execute("DELETE FROM schedule WHERE league_key = ?", (league_key,))
    conn.executemany(
        "INSERT INTO schedule (league_key, week, team_a, team_b, score_a, score_b) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(league_key, w, a, b, sa, sb) for (w, a, b, sa, sb) in matchups],
    )
    conn.commit()


def move_player(conn, league_key: str, player_id: str, new_team_id, from_week: int) -> int:
    """
    Manually reassign a player to a different team (or to free agency,
    if new_team_id is None) starting at from_week and for every later
    week already sitting in the ownership table.

    This is a local-only override for the "move a player" UI control in
    app.py's Rosters tab - it doesn't talk to ESPN/Sleeper at all, it
    just edits the ownership rows that simulator.py/waiver.py/trades.py
    already read from. It intentionally updates every week >= from_week
    (not just one), since the whole point is to change what the season
    simulation assumes the roster looks like going forward - a single
    week's ownership row wouldn't affect any of those season totals.

    Clears any IR/Taxi `reserved` flag on the affected rows: a manual
    move is by definition putting the player on an active roster spot
    (or off the roster entirely, for free agency), not into a reserved
    slot - there's no UI for reserving a player, only for moving one.

    NOTE: this is overwritten the next time `ingest.py` runs, since
    ingestion re-derives real ownership from the platform for every
    ingested week. That's expected - this is a scratch/what-if edit,
    not a substitute for actually making the move on ESPN/Sleeper.

    Returns the number of ownership rows updated (0 usually means
    from_week is later than every ingested week for this league, or
    the player has no ownership rows in this league at all).
    """
    cur = conn.execute(
        "UPDATE ownership SET team_id = ?, reserved = NULL "
        "WHERE league_key = ? AND player_id = ? AND week >= ?",
        (new_team_id, league_key, player_id, from_week),
    )
    conn.commit()
    return cur.rowcount