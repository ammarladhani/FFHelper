"""
Season simulator. Given a fixed set of players for a team (their
roster as of some week), projects the optimal starting lineup's score
for every remaining week and sums it into a season total.

This is deliberately "static roster" - it assumes no further
add/drops happen. The waiver optimizer and trade engine layer on top
of this by testing *modified* rosters and comparing the resulting
totals against this baseline.
"""

import repo
from lineup_optimizer import optimize_lineup


def simulate_roster(conn, league_key: str, player_ids: list, slot_counts: dict,
                     start_week: int, end_week: int,
                     player_info_cache: dict = None, projection_cache: dict = None) -> dict:
    """
    player_info_cache / projection_cache are optional - pass shared dicts
    in when calling this many times in a row over overlapping weeks/rosters
    (waiver search, trade search) to avoid re-querying the same player info
    and the same week's projections from SQLite over and over. See
    repo.get_roster_with_projection for details. If omitted, a throwaway
    cache is used per call (same behavior as before caching existed).
    """
    if player_info_cache is None:
        player_info_cache = {}
    if projection_cache is None:
        projection_cache = {}

    weekly = {}
    for week in range(start_week, end_week + 1):
        players = repo.get_roster_with_projection(
            conn, league_key, player_ids, week, player_info_cache, projection_cache
        )
        result = optimize_lineup(players, slot_counts)
        weekly[week] = result["total_points"]
    return {"weekly": weekly, "total": sum(weekly.values())}


def simulate_team_season(conn, league_key: str, team_id: int, start_week: int,
                          end_week: int, as_of_week: int = None,
                          player_info_cache: dict = None, projection_cache: dict = None) -> dict:
    as_of_week = as_of_week or start_week
    player_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    slot_counts = repo.get_slot_counts(conn, league_key)
    return simulate_roster(
        conn, league_key, player_ids, slot_counts, start_week, end_week,
        player_info_cache, projection_cache,
    )


def simulate_all_teams(conn, league_key: str, start_week: int, end_week: int,
                        as_of_week: int = None) -> dict:
    teams = repo.get_teams(conn, league_key)

    # Shared across every team in this run: every team's simulation touches
    # the same weeks, so fetching each week's full projection map once (and
    # reusing it for all teams) instead of once per team is a big win on
    # leagues with a lot of teams / a long week range.
    player_info_cache = {}
    projection_cache = {}

    results = {}
    for team in teams:
        sim = simulate_team_season(
            conn, league_key, team["team_id"], start_week, end_week, as_of_week,
            player_info_cache, projection_cache,
        )
        results[team["team_id"]] = {
            "team_name": team["team_name"],
            "manager_name": team["manager_name"],
            **sim,
        }
    return results