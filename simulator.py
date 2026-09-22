"""
Season simulator. Given a fixed set of players for a team (their
roster as of some week), projects the optimal starting lineup's score
for every remaining week and sums it into a season total.

This is deliberately "static roster" - it assumes no further
add/drops happen. The waiver optimizer and trade engine layer on top
of this by testing *modified* rosters and comparing the resulting
totals against this baseline.

Every result carries both the plain and the recency-weighted total
(see weighting.py). `total` is the one everything ranks by: it equals
the weighted total when a `decay` is passed and the plain sum when it
isn't.
"""

from typing import Optional

import repo
import weighting
from lineup_optimizer import optimize_lineup


def simulate_roster(conn, league_key: str, player_ids: list, slot_counts: dict,
                     start_week: int, end_week: int,
                     player_info_cache: Optional[dict] = None,
                     projection_cache: Optional[dict] = None,
                     decay: Optional[float] = None) -> dict:
    """
    `player_info_cache` / `projection_cache` are optional shared dicts -
    pass the same ones across many simulate_roster() calls (waiver.py
    and trades.py both do this) to avoid re-fetching the same player's
    info/projections from SQLite on every single candidate roster
    tried. Safe to omit for a one-off call.

    `decay` (None = off) recency-weights the season total; see
    weighting.py. `weekly` is always the raw per-week projected points.

    Returns {"weekly": {week: pts}, "weights": {week: w},
             "raw_total": plain sum, "weighted_total": weighted sum,
             "total": weighted_total (== raw_total when decay is None)}.
    """
    weights = weighting.week_weights(start_week, end_week, decay)
    weekly = {}
    for week in range(start_week, end_week + 1):
        players = repo.get_roster_with_projection(
            conn, league_key, player_ids, week,
            player_info_cache=player_info_cache,
            projection_cache=projection_cache,
        )
        result = optimize_lineup(players, slot_counts)
        weekly[week] = result["total_points"]

    raw_total = sum(weekly.values())
    weighted_total = weighting.weighted_total(weekly, weights)
    return {
        "weekly": weekly,
        "weights": weights,
        "raw_total": raw_total,
        "weighted_total": weighted_total,
        "total": weighted_total,
    }


def simulate_team_season(conn, league_key: str, team_id: int, start_week: int,
                          end_week: int, as_of_week: int = None,
                          decay: Optional[float] = None) -> dict:
    as_of_week = as_of_week or start_week
    player_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    slot_counts = repo.get_slot_counts(conn, league_key)
    return simulate_roster(conn, league_key, player_ids, slot_counts, start_week, end_week,
                           decay=decay)


def simulate_all_teams(conn, league_key: str, start_week: int, end_week: int,
                        as_of_week: int = None, decay: Optional[float] = None) -> dict:
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
            decay=decay,
        )
        results[team["team_id"]] = {
            "team_name": team["team_name"],
            "manager_name": team["manager_name"],
            **sim,
        }
    return results