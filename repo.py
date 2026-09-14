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


def get_free_agents_ranked_by_position(conn, league_key: str, as_of_week: int, start_week: int,
                                        end_week: int, limit_per_position: int = 8) -> list:
    """
    Free agent player_ids, ranked by naive rest-of-season projected sum
    WITHIN each position, then the top `limit_per_position` from EVERY
    position group are combined into one list.

    This matters because a flat top-N-by-raw-points cut (get_free_agents_ranked)
    is naturally dominated by RB/WR/QB - a mediocre bench running back
    almost always out-projects a good kicker or streaming defense in raw
    points, so a flat top-40 can end up never even looking at a free
    agent K/DEF/IDP, no matter how good they are relative to their own
    position. Ranking within each position first guarantees every
    position gets a fair, proportionate shot at being considered - and
    in practice lets each position use a smaller budget than a flat cut
    would need to get the same coverage, which is a real speedup, not
    just a fairness fix.
    """
    rows = conn.execute(
        """
        SELECT o.player_id, p.position, SUM(COALESCE(pr.projected_points, 0)) AS total
        FROM ownership o
        JOIN players p ON p.player_id = o.player_id
        LEFT JOIN projections pr
          ON pr.league_key = o.league_key
         AND pr.player_id = o.player_id
         AND pr.week BETWEEN ? AND ?
        WHERE o.league_key = ? AND o.team_id IS NULL AND o.week = ?
        GROUP BY o.player_id
        ORDER BY p.position, total DESC
        """,
        (start_week, end_week, league_key, as_of_week),
    ).fetchall()

    by_position = {}
    for pid, position, _total in rows:
        by_position.setdefault(position, []).append(pid)

    ranked = []
    for pids in by_position.values():
        ranked.extend(pids[:limit_per_position])
    return ranked


def get_roster_players_ranked_worst_first(conn, league_key: str, player_ids: list,
                                           start_week: int, end_week: int, limit: int) -> list:
    """
    Given a roster's player_ids, rank them WORST first by naive sum of
    projected points over the remaining weeks, capped to `limit`. Used to
    narrow which of your own players are even worth testing as a "drop"
    candidate in a waiver search - dropping a top performer for a random
    free agent is essentially never correct, so there's no need to run
    the expensive lineup-optimizer delta check against every single
    roster spot, just the weakest ones.
    """
    if not player_ids:
        return []
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, SUM(COALESCE(projected_points, 0)) AS total
        FROM projections
        WHERE league_key = ? AND week BETWEEN ? AND ? AND player_id IN ({placeholders})
        GROUP BY player_id
        ORDER BY total ASC
        """,
        [league_key, start_week, end_week] + player_ids,
    ).fetchall()
    ranked = [r[0] for r in rows]
    # Players with no projection rows at all in this range are unknowns,
    # not necessarily good - put them at the front (most droppable) rather
    # than silently excluding them from consideration.
    missing = [pid for pid in player_ids if pid not in ranked]
    return (missing + ranked)[:limit]


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
    player_info_cache: dict = None,
    projection_cache: dict = None,
) -> list:
    """Build player dicts for the lineup optimizer - one batched query for
    player info, one for that week's projections, instead of two queries
    PER PLAYER. This function gets called once per week inside every
    simulate_roster call, which itself gets called many times over by
    waiver/trade search - the per-player query pattern was the single
    biggest cost multiplier in the whole system.

    player_info_cache / projection_cache are OPTIONAL dicts the caller can
    create once and pass into many calls (e.g. across every candidate
    roster tried by waiver.py / trades.py) so the same player's info, and
    the same week's full projection map, only get fetched from SQLite
    once per search run - not once per candidate roster tried. If omitted,
    each call just uses its own throwaway cache (same behavior as before).

    - player_info_cache is keyed by player_id (player info never changes
      within a run).
    - projection_cache is keyed by (league_key, week) -> {player_id: pts}
      for the WHOLE week (not just player_ids), since different candidate
      rosters overlap heavily in which weeks/players they touch, and
      fetching the whole week once is cheap and reusable.
    """
    if not player_ids:
        return []

    if player_info_cache is None:
        player_info_cache = {}
    if projection_cache is None:
        projection_cache = {}

    missing_ids = [pid for pid in player_ids if pid not in player_info_cache]
    if missing_ids:
        placeholders = ",".join("?" for _ in missing_ids)
        rows = conn.execute(
            f"""
            SELECT player_id, name, position, eligible_slots
            FROM players
            WHERE player_id IN ({placeholders})
            """,
            missing_ids,
        ).fetchall()
        for pid, name, position, eligible_slots_json in rows:
            try:
                eligible_slots = json.loads(eligible_slots_json) if eligible_slots_json else []
            except (TypeError, json.JSONDecodeError):
                eligible_slots = []
            player_info_cache[pid] = {
                "name": name,
                "position": position,
                "eligible_slots": eligible_slots,
            }

    week_key = (league_key, week)
    week_projections = projection_cache.get(week_key)
    if week_projections is None:
        rows = conn.execute(
            "SELECT player_id, projected_points FROM projections WHERE league_key = ? AND week = ?",
            (league_key, week),
        ).fetchall()
        week_projections = {pid: pts for pid, pts in rows}
        projection_cache[week_key] = week_projections

    players = []
    for pid in player_ids:
        info = player_info_cache.get(pid)
        if not info:
            continue
        players.append({
            "player_id": pid,
            "name": info["name"],
            "position": info["position"],
            "eligible_slots": info["eligible_slots"],
            "projected": week_projections.get(pid),
        })

    return players