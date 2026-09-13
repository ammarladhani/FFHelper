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
    if not player_ids:
        return {
            "weekly": {
                week: 0.0
                for week in range(start_week, end_week + 1)
            },
            "total": 0.0,
        }

    # Load player metadata once instead of once per player per week.
    players_by_id = {}

    for pid in player_ids:
        info = repo.get_player_info(conn, pid)
        if info:
            players_by_id[pid] = info

    valid_player_ids = list(players_by_id.keys())

    if not valid_player_ids:
        return {
            "weekly": {
                week: 0.0
                for week in range(start_week, end_week + 1)
            },
            "total": 0.0,
        }

    # Load every projection needed for this roster in one query.
    placeholders = ",".join("?" for _ in valid_player_ids)

    rows = conn.execute(
        f"""
        SELECT player_id, week, projected_points
        FROM projections
        WHERE league_key = ?
          AND player_id IN ({placeholders})
          AND week BETWEEN ? AND ?
        """,
        (
            league_key,
            *valid_player_ids,
            start_week,
            end_week,
        ),
    ).fetchall()

    projections = {
        (row[0], row[1]): row[2]
        for row in rows
    }

    weekly = {}

    for week in range(start_week, end_week + 1):
        players = []

        for pid in valid_player_ids:
            info = players_by_id[pid]

            players.append({
                "player_id": pid,
                "name": info["name"],
                "position": info["position"],
                "eligible_slots": info["eligible_slots"],
                "projected": projections.get((pid, week)),
            })

        result = optimize_lineup(players, slot_counts)
        weekly[week] = result["total_points"]

    return {
        "weekly": weekly,
        "total": sum(weekly.values()),
    }



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
