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

    # Shared across all four simulate_roster calls below - they all cover
    # the same week range, so the same weeks' projections get fetched once.
    player_info_cache = {}
    projection_cache = {}

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    baseline_a = simulate_roster(conn, league_key, a_ids, slot_counts, start_week, end_week,
                                  player_info_cache, projection_cache)
    baseline_b = simulate_roster(conn, league_key, b_ids, slot_counts, start_week, end_week,
                                  player_info_cache, projection_cache)

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    new_a = simulate_roster(conn, league_key, new_a_ids, slot_counts, start_week, end_week,
                             player_info_cache, projection_cache)
    new_b = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week,
                             player_info_cache, projection_cache)

    return {
        "team_a": {"team_id": team_a_id, "before": baseline_a["total"], "after": new_a["total"],
                   "delta": round(new_a["total"] - baseline_a["total"], 2)},
        "team_b": {"team_id": team_b_id, "before": baseline_b["total"], "after": new_b["total"],
                   "delta": round(new_b["total"] - baseline_b["total"], 2)},
    }


def suggest_trades(conn, league_key: str, my_team_id: int, start_week: int, end_week: int,
                    as_of_week: int = None, candidate_prefilter: int = 12,
                    combo_sizes=(1,), partner_team_id: int = None) -> list:
    """
    Search win-win 1-for-1 (and optionally larger, via combo_sizes)
    trades between my_team_id and every other team in the league - or,
    if partner_team_id is given, just that one team.

    Returns every win-win trade found, sorted by your gain descending -
    the caller decides how much of that to display vs. summarize.

    candidate_prefilter limits each side's roster to its top N players
    by naive rest-of-season projected sum before generating trade
    combinations, since full combinatorics over a ~15-man roster on
    both sides explodes quickly.

    combo_sizes defaults to (1,) - i.e. 1-for-1 trades only. Trying
    combo_sizes=(1, 2) also searches 2-for-2 swaps, which is supported
    but MUCH slower (combinatorics grow fast), so it's opt-in rather
    than the default.

    Performance notes:
    - Each team's "no trade happened" baseline total is computed ONCE
      per opposing team (not once per candidate combo, which is what a
      naive call to evaluate_trade() in a loop would do) - and if a
      combo doesn't even help your own team, the partner's side is
      never simulated at all.
    - A player_info_cache / projection_cache pair is created once for
      the entire search and shared across every simulate_roster() call
      made here (baselines and every candidate trial, for every
      partner team). Every trial only swaps 1-2 players in/out of a
      ~15-man roster, and all trials cover the same week range, so the
      same players' info and the same weeks' full projection maps get
      reused thousands of times instead of re-queried from SQLite for
      every single candidate. This is the single biggest lever on
      trade-search runtime, on top of not defaulting to 2-for-2.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)

    player_info_cache = {}
    projection_cache = {}

    teams = repo.get_teams(conn, league_key)
    if partner_team_id is not None:
        teams = [t for t in teams if t["team_id"] == partner_team_id]

    my_ids_full = repo.get_roster_player_ids(conn, league_key, my_team_id, as_of_week)
    my_candidates = _top_players_by_rest_of_season(conn, league_key, my_ids_full, start_week, end_week, candidate_prefilter)
    my_baseline = simulate_roster(conn, league_key, my_ids_full, slot_counts, start_week, end_week,
                                   player_info_cache, projection_cache)["total"]

    proposals = []

    for team in teams:
        other_id = team["team_id"]
        if other_id == my_team_id:
            continue

        other_ids_full = repo.get_roster_player_ids(conn, league_key, other_id, as_of_week)
        other_candidates = _top_players_by_rest_of_season(conn, league_key, other_ids_full, start_week, end_week, candidate_prefilter)
        other_baseline = simulate_roster(conn, league_key, other_ids_full, slot_counts, start_week, end_week,
                                          player_info_cache, projection_cache)["total"]

        for size in combo_sizes:
            for my_combo in combinations(my_candidates, size):
                new_a_ids = [pid for pid in my_ids_full if pid not in my_combo]

                for other_combo in combinations(other_candidates, size):
                    trial_a_ids = new_a_ids + list(other_combo)
                    new_a_total = simulate_roster(conn, league_key, trial_a_ids, slot_counts, start_week, end_week,
                                                   player_info_cache, projection_cache)["total"]
                    my_delta = round(new_a_total - my_baseline, 2)
                    if my_delta <= 0:
                        continue  # doesn't even help me - skip the pricier partner-side check

                    new_b_ids = [pid for pid in other_ids_full if pid not in other_combo] + list(my_combo)
                    new_b_total = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week,
                                                   player_info_cache, projection_cache)["total"]
                    partner_delta = round(new_b_total - other_baseline, 2)
                    if partner_delta <= 0:
                        continue

                    proposals.append({
                        "give": [repo.get_player_info(conn, pid) for pid in my_combo],
                        "get": [repo.get_player_info(conn, pid) for pid in other_combo],
                        "partner_team_id": other_id,
                        "partner_team_name": team["team_name"],
                        "my_delta": my_delta,
                        "partner_delta": partner_delta,
                    })

    proposals.sort(key=lambda p: p["my_delta"], reverse=True)
    return proposals


def _top_players_by_rest_of_season(conn, league_key, player_ids, start_week, end_week, limit):
    if not player_ids:
        return []
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, SUM(COALESCE(projected_points, 0)) AS total
        FROM projections
        WHERE league_key = ? AND week BETWEEN ? AND ? AND player_id IN ({placeholders})
        GROUP BY player_id
        ORDER BY total DESC
        """,
        [league_key, start_week, end_week] + player_ids,
    ).fetchall()
    ranked = [r[0] for r in rows]
    # Any player with literally no projection rows at all in this range
    # (shouldn't normally happen - ingestion writes a row every week even
    # when the value is None) won't appear in the GROUP BY result; tack
    # them on at the bottom rather than silently dropping them.
    missing = [pid for pid in player_ids if pid not in ranked]
    return (ranked + missing)[:limit]


def explain_trade(conn, league_key: str, team_a_id: int, team_a_gives: list,
                   team_b_id: int, team_b_gives: list, start_week: int, end_week: int,
                   as_of_week: int = None) -> dict:
    """
    Week-by-week comparison of both teams' projected totals with vs
    without a specific trade, for the "why does this help" view.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)

    player_info_cache = {}
    projection_cache = {}

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    before_a = simulate_roster(conn, league_key, a_ids, slot_counts, start_week, end_week,
                                player_info_cache, projection_cache)
    before_b = simulate_roster(conn, league_key, b_ids, slot_counts, start_week, end_week,
                                player_info_cache, projection_cache)

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    after_a = simulate_roster(conn, league_key, new_a_ids, slot_counts, start_week, end_week,
                               player_info_cache, projection_cache)
    after_b = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week,
                               player_info_cache, projection_cache)

    return {
        "team_a": {
            "team_id": team_a_id,
            "weekly_before": before_a["weekly"], "weekly_after": after_a["weekly"],
            "total_before": before_a["total"], "total_after": after_a["total"],
            "delta": after_a["total"] - before_a["total"],
        },
        "team_b": {
            "team_id": team_b_id,
            "weekly_before": before_b["weekly"], "weekly_after": after_b["weekly"],
            "total_before": before_b["total"], "total_after": after_b["total"],
            "delta": after_b["total"] - before_b["total"],
        },
    }