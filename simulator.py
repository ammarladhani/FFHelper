"""
Season simulator. Given a fixed set of players for a team (their
roster as of some week), projects the optimal starting lineup's score
for every remaining week and sums it into a season total.

This is deliberately "static roster" - it assumes no further
add/drops happen. The waiver optimizer and trade engine layer on top
of this by testing *modified* rosters and comparing the resulting
totals against this baseline.
"""

from typing import Optional

import repo
from lineup_optimizer import optimize_lineup


def simulate_roster(conn, league_key: str, player_ids: list, slot_counts: dict,
                     start_week: int, end_week: int,
                     player_info_cache: Optional[dict] = None,
                     projection_cache: Optional[dict] = None) -> dict:
    """
    `player_info_cache` / `projection_cache` are optional shared dicts -
    pass the same ones across many simulate_roster() calls (waiver.py
    and trades.py both do this) to avoid re-fetching the same player's
    info/projections from SQLite on every single candidate roster
    tried. Safe to omit for a one-off call.
    """
    weekly = {}
    for week in range(start_week, end_week + 1):
        players = repo.get_roster_with_projection(
            conn, league_key, player_ids, week,
            player_info_cache=player_info_cache,
            projection_cache=projection_cache,
        )
        result = optimize_lineup(players, slot_counts)
        weekly[week] = result["total_points"]
    return {"weekly": weekly, "total": sum(weekly.values())}


def simulate_team_season(conn, league_key: str, team_id: int, start_week: int,
                          end_week: int, as_of_week: int = None) -> dict:
    as_of_week = as_of_week or start_week
    player_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    slot_counts = repo.get_slot_counts(conn, league_key)
    return simulate_roster(conn, league_key, player_ids, slot_counts, start_week, end_week)


def simulate_all_teams(conn, league_key: str, start_week: int, end_week: int,
                        as_of_week: int = None) -> dict:
    teams = repo.get_teams(conn, league_key)
    # Shared across every team - rosters differ, but player info/eligibility
    # doesn't, and there's plenty of cross-team overlap in projections
    # fetched per week too.
    player_info_cache: dict = {}
    projection_cache: dict = {}

    results = {}
    for team in teams:
        as_of = as_of_week or start_week
        player_ids = repo.get_roster_player_ids(conn, league_key, team["team_id"], as_of)
        slot_counts = repo.get_slot_counts(conn, league_key)
        sim = simulate_roster(
            conn, league_key, player_ids, slot_counts, start_week, end_week,
            player_info_cache=player_info_cache,
            projection_cache=projection_cache,
        )
        results[team["team_id"]] = {
            "team_name": team["team_name"],
            "manager_name": team["manager_name"],
            **sim,
        }
    return results
