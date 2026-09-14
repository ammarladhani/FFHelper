"""
Read-only query helpers over the local database. Everything downstream
(simulator, waiver optimizer, trade engine) goes through these instead
of writing raw SQL inline.
"""

import json

def get_slot_counts(conn, league_key: str) -> dict:
    rows = conn.execute(
        "SELECT slot_name, count FROM league_settings WHERE league_key = ?",
        (league_key,),
    ).fetchall()
    return {name: count for name, count in rows}


def get_teams(conn, league_key: str) -> list:
    rows = conn.execute(
        "SELECT team_id, team_name, manager_name FROM teams WHERE league_key = ?",
        (league_key,),
    ).fetchall()
    return [{"team_id": r[0], "team_name": r[1], "manager_name": r[2]} for r in rows]


def get_roster_player_ids(conn, league_key: str, team_id: int, as_of_week: int) -> list:
    """Which players belong to this team, based on ownership at as_of_week."""
    rows = conn.execute(
        "SELECT player_id FROM ownership WHERE league_key = ? AND team_id = ? AND week = ?",
        (league_key, team_id, as_of_week),
    ).fetchall()
    return [r[0] for r in rows]


def get_free_agent_ids(conn, league_key: str, as_of_week: int) -> list:
    rows = conn.execute(
        "SELECT player_id FROM ownership WHERE league_key = ? AND team_id IS NULL AND week = ?",
        (league_key, as_of_week),
    ).fetchall()
    return [r[0] for r in rows]


def get_free_agents_ranked(conn, league_key: str, as_of_week: int, start_week: int,
                            end_week: int, limit: int = 40) -> list:
    """
    Free agent player_ids, pre-ranked by naive sum of projected points
    over the remaining weeks. Used to cut down the search space before
    running the (much more expensive) lineup-optimizer-based delta
    calculation on each candidate.
    """
    rows = conn.execute(
        """
        SELECT o.player_id, SUM(COALESCE(pr.projected_points, 0)) AS total
        FROM ownership o
        LEFT JOIN projections pr
          ON pr.league_key = o.league_key
         AND pr.player_id = o.player_id
         AND pr.week BETWEEN ? AND ?
        WHERE o.league_key = ? AND o.team_id IS NULL AND o.week = ?
        GROUP BY o.player_id
        ORDER BY total DESC
        LIMIT ?
        """,
        (start_week, end_week, league_key, as_of_week, limit),
    ).fetchall()
    return [r[0] for r in rows]


def get_player_info(conn, player_id: int) -> dict:
    row = conn.execute(
        """
        SELECT player_id, name, position, eligible_slots
        FROM players
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()

    if not row:
        return None

    try:
        eligible_slots = json.loads(row[3]) if row[3] else []
    except (TypeError, json.JSONDecodeError):
        eligible_slots = []

    return {
        "player_id": row[0],
        "name": row[1],
        "position": row[2],
        "eligible_slots": eligible_slots,
    }


def get_projection(conn, league_key: str, player_id: int, week: int):
    row = conn.execute(
        """
        SELECT projected_points
        FROM projections
        WHERE league_key = ?
          AND player_id = ?
          AND week = ?
        """,
        (league_key, player_id, week),
    ).fetchone()

    return row[0] if row else None


def get_roster_with_projection(
    conn,
    league_key: str,
    player_ids: list,
    week: int,
) -> list:
    """Build player dicts for the lineup optimizer - one batched query for
    player info, one for that week's projections, instead of two queries
    PER PLAYER. This function gets called once per week inside every
    simulate_roster call, which itself gets called many times over by
    waiver/trade search - the per-player query pattern was the single
    biggest cost multiplier in the whole system."""
    if not player_ids:
        return []

    placeholders = ",".join("?" for _ in player_ids)

    player_rows = conn.execute(
        f"""
        SELECT player_id, name, position, eligible_slots
        FROM players
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()

    info_map = {}
    for pid, name, position, eligible_slots_json in player_rows:
        try:
            eligible_slots = json.loads(eligible_slots_json) if eligible_slots_json else []
        except (TypeError, json.JSONDecodeError):
            eligible_slots = []
        info_map[pid] = {"name": name, "position": position, "eligible_slots": eligible_slots}

    proj_rows = conn.execute(
        f"""
        SELECT player_id, projected_points
        FROM projections
        WHERE league_key = ? AND week = ? AND player_id IN ({placeholders})
        """,
        [league_key, week] + player_ids,
    ).fetchall()
    proj_map = {pid: pts for pid, pts in proj_rows}

    players = []
    for pid in player_ids:
        info = info_map.get(pid)
        if not info:
            continue
        players.append({
            "player_id": pid,
            "name": info["name"],
            "position": info["position"],
            "eligible_slots": info["eligible_slots"],
            "projected": proj_map.get(pid),
        })

    return players