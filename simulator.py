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
                     start_week: int, end_week: int) -> dict:
    weekly = {}
    for week in range(start_week, end_week + 1):
        players = repo.get_roster_with_projection(conn, league_key, player_ids, week)
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
    results = {}
    for team in teams:
        sim = simulate_team_season(conn, league_key, team["team_id"], start_week, end_week, as_of_week)
        results[team["team_id"]] = {
            "team_name": team["team_name"],
            "manager_name": team["manager_name"],
            **sim,
        }
    return results
