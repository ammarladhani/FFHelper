"""
Trade engine with two entry points:

- evaluate_trade: given an explicit trade (players each side gives up),
  compute the before/after remaining-season projected total for both
  teams.
- suggest_trades: search 1-for-1 (and optionally 2-for-1/2-for-2)
  swaps between your team and every other team, keeping only trades
  where BOTH sides' projected totals improve.

The search is capped to a prefiltered subset of each roster to keep
the combinatorics sane - see `candidate_prefilter`.
"""

from itertools import combinations

import repo
from simulator import simulate_roster


def evaluate_trade(conn, league_key: str, team_a_id: int, team_a_gives: list,
                    team_b_id: int, team_b_gives: list, start_week: int, end_week: int,
                    as_of_week: int = None) -> dict:
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    baseline_a = simulate_roster(conn, league_key, a_ids, slot_counts, start_week, end_week)
    baseline_b = simulate_roster(conn, league_key, b_ids, slot_counts, start_week, end_week)

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    new_a = simulate_roster(conn, league_key, new_a_ids, slot_counts, start_week, end_week)
    new_b = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week)

    return {
        "team_a": {"team_id": team_a_id, "before": baseline_a["total"], "after": new_a["total"],
                   "delta": round(new_a["total"] - baseline_a["total"], 2)},
        "team_b": {"team_id": team_b_id, "before": baseline_b["total"], "after": new_b["total"],
                   "delta": round(new_b["total"] - baseline_b["total"], 2)},
    }


def suggest_trades(conn, league_key: str, my_team_id: int, start_week: int, end_week: int,
                    as_of_week: int = None, top_n: int = 10, candidate_prefilter: int = 12,
                    combo_sizes=(1,)) -> list:
    """
    Search win-win 1-for-1 (and optionally larger, via combo_sizes)
    trades between my_team_id and every other team in the league.

    candidate_prefilter limits each side's roster to its top N players
    by naive rest-of-season projected sum before generating trade
    combinations, since full combinatorics over a ~15-man roster on
    both sides explodes quickly.
    """
    as_of_week = as_of_week or start_week
    teams = repo.get_teams(conn, league_key)
    my_ids_full = repo.get_roster_player_ids(conn, league_key, my_team_id, as_of_week)
    my_candidates = _top_players_by_rest_of_season(conn, league_key, my_ids_full, start_week, end_week, candidate_prefilter)

    proposals = []

    for team in teams:
        other_id = team["team_id"]
        if other_id == my_team_id:
            continue

        other_ids_full = repo.get_roster_player_ids(conn, league_key, other_id, as_of_week)
        other_candidates = _top_players_by_rest_of_season(conn, league_key, other_ids_full, start_week, end_week, candidate_prefilter)

        for size in combo_sizes:
            for my_combo in combinations(my_candidates, size):
                for other_combo in combinations(other_candidates, size):
                    result = evaluate_trade(
                        conn, league_key, my_team_id, list(my_combo),
                        other_id, list(other_combo), start_week, end_week, as_of_week,
                    )
                    if result["team_a"]["delta"] > 0 and result["team_b"]["delta"] > 0:
                        proposals.append({
                            "give": [repo.get_player_info(conn, pid) for pid in my_combo],
                            "get": [repo.get_player_info(conn, pid) for pid in other_combo],
                            "partner_team_id": other_id,
                            "partner_team_name": team["team_name"],
                            "my_delta": result["team_a"]["delta"],
                            "partner_delta": result["team_b"]["delta"],
                        })

    proposals.sort(key=lambda p: p["my_delta"], reverse=True)
    return proposals[:top_n]


def _top_players_by_rest_of_season(conn, league_key, player_ids, start_week, end_week, limit):
    scored = []
    for pid in player_ids:
        total = 0.0
        for week in range(start_week, end_week + 1):
            proj = repo.get_projection(conn, league_key, pid, week)
            total += proj or 0.0
        scored.append((pid, total))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [pid for pid, _ in scored[:limit]]
