"""
Read-only query helpers over the local database. Everything downstream
(simulator, waiver optimizer, trade engine) goes through these instead
of writing raw SQL inline.
"""

import json
from typing import Optional


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
                                        end_week: int, limit_per_position: int = 10) -> list:
    """
    Like get_free_agents_ranked, but ranks and truncates SEPARATELY per
    position before combining - e.g. top 10 free agent RBs + top 10 WRs +
    top 10 QBs, etc., rather than one global top-N list that a deep
    position (WR) can flood out a shallow one (QB, TE, K) from entirely.
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
        GROUP BY o.player_id, p.position
        ORDER BY p.position, total DESC
        """,
        (start_week, end_week, league_key, as_of_week),
    ).fetchall()

    by_position: dict = {}
    for player_id, position, _total in rows:
        by_position.setdefault(position, []).append(player_id)

    combined = []
    for ids in by_position.values():
        combined.extend(ids[:limit_per_position])
    return combined

# player_id is always TEXT (platform-prefixed, e.g. "espn_4046692" or
# "sleeper_4984" - the latter sometimes non-numeric, e.g. "sleeper_BUF").
# It was numeric pre-Sleeper-support; keeping `int` in these signatures
# was a leftover that happened to still work for ESPN-only setups and
# would silently misbehave (or just never match) for Sleeper leagues.

def get_player_info(conn, player_id: str, cache: Optional[dict] = None) -> Optional[dict]:
    """Look up one player. Pass a shared `cache` dict when calling this
    in a loop (waiver/trade search does) so repeat lookups of the same
    player don't round-trip to SQLite every time."""
    if cache is not None and player_id in cache:
        return cache[player_id]

    row = conn.execute(
        """
        SELECT player_id, name, position, eligible_slots
        FROM players
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()

    if not row:
        if cache is not None:
            cache[player_id] = None
        return None

    info = {
        "player_id": row[0],
        "name": row[1],
        "position": row[2],
        "eligible_slots": _load_eligible_slots(row[3]),
    }

    if cache is not None:
        cache[player_id] = info
    return info


# NOTE: waiver.py/trades.py also maintain a *separate* player_info_cache
# (keyed the same way, player_id -> info dict) that they pass into
# repo.get_roster_with_projection(). That cache's entries come from a
# batched `players` query and are shaped identically to what this
# function returns, so get_player_info(..., cache=that_same_dict) is
# safe to call against it interchangeably - see waiver.best_pickups.


def get_projection(conn, league_key: str, player_id: str, week: int):
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
    player_info_cache: Optional[dict] = None,
    projection_cache: Optional[dict] = None,
) -> list:
    """Build player dicts for the lineup optimizer.

    `player_info_cache` (keyed by player_id) and `projection_cache`
    (keyed by (player_id, week)) are optional dicts the caller can
    share across many calls - e.g. waiver/trade search calls this
    once per week for every candidate roster it tries, and a player's
    name/position/eligibility never changes between those calls, so
    there's no reason to re-fetch it from SQLite each time. Only the
    cache misses get batched into a single query, same as before.
    """
    if not player_ids:
        return []

    if player_info_cache is None:
        player_info_cache = {}
    if projection_cache is None:
        projection_cache = {}

    missing_info_ids = [pid for pid in player_ids if pid not in player_info_cache]
    if missing_info_ids:
        placeholders = ",".join("?" for _ in missing_info_ids)
        player_rows = conn.execute(
            f"""
            SELECT player_id, name, position, eligible_slots
            FROM players
            WHERE player_id IN ({placeholders})
            """,
            missing_info_ids,
        ).fetchall()
        fetched_ids = set()
        for pid, name, position, eligible_slots_json in player_rows:
            # Same shape as get_player_info()'s cache entries below -
            # waiver.py/trades.py pass this exact dict into BOTH
            # get_roster_with_projection() and get_player_info() as one
            # shared cache, so the two must agree on what they store or
            # whichever one populates the cache first silently corrupts
            # what the other one reads back out.
            player_info_cache[pid] = {
                "player_id": pid,
                "name": name,
                "position": position,
                "eligible_slots": _load_eligible_slots(eligible_slots_json),
            }
            fetched_ids.add(pid)
        # Players with no row in `players` at all (shouldn't normally
        # happen) - cache the miss so we don't keep re-querying for them.
        for pid in missing_info_ids:
            if pid not in fetched_ids:
                player_info_cache[pid] = None

    missing_proj_ids = [pid for pid in player_ids if (pid, week) not in projection_cache]
    if missing_proj_ids:
        placeholders = ",".join("?" for _ in missing_proj_ids)
        proj_rows = conn.execute(
            f"""
            SELECT player_id, projected_points
            FROM projections
            WHERE league_key = ? AND week = ? AND player_id IN ({placeholders})
            """,
            [league_key, week] + missing_proj_ids,
        ).fetchall()
        fetched_ids = set()
        for pid, pts in proj_rows:
            projection_cache[(pid, week)] = pts
            fetched_ids.add(pid)
        for pid in missing_proj_ids:
            if pid not in fetched_ids:
                projection_cache[(pid, week)] = None

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
            "projected": projection_cache.get((pid, week)),
        })

    return players


def _load_eligible_slots(eligible_slots_json) -> list:
    try:
        return json.loads(eligible_slots_json) if eligible_slots_json else []
    except (TypeError, json.JSONDecodeError):
        return []
