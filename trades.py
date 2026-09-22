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

Every entry point takes an optional `decay` (None = off). When given,
the candidate prefilter, the win-win test, and the sort order all use
recency-weighted totals (see weighting.py) - near-term weeks count for
more. Returned `delta` numbers are in that same weighted metric; the
`raw_*` twins are the plain, unweighted point changes.
"""

from itertools import combinations
from math import comb
import math
from typing import Optional

import repo
from simulator import simulate_roster

def _reject_reserved(conn, league_key, team_id, player_ids, as_of_week):
    reserved = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    locked = [pid for pid in player_ids if pid in reserved]
    if locked:
        names = [repo.get_player_info(conn, pid)["name"] for pid in locked]
        raise ValueError(f"Can't trade {', '.join(names)}: currently on IR/Taxi.")

def evaluate_trade(conn, league_key: str, team_a_id: int, team_a_gives: list,
                    team_b_id: int, team_b_gives: list, start_week: int, end_week: int,
                    as_of_week: int = None, decay: Optional[float] = None) -> dict:
    as_of_week = as_of_week or start_week
    _reject_reserved(conn, league_key, team_a_id, team_a_gives, as_of_week)
    _reject_reserved(conn, league_key, team_b_id, team_b_gives, as_of_week)
    slot_counts = repo.get_slot_counts(conn, league_key)

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    baseline_a = simulate_roster(conn, league_key, a_ids, slot_counts, start_week, end_week, decay=decay)
    baseline_b = simulate_roster(conn, league_key, b_ids, slot_counts, start_week, end_week, decay=decay)

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    new_a = simulate_roster(conn, league_key, new_a_ids, slot_counts, start_week, end_week, decay=decay)
    new_b = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week, decay=decay)

    return {
        "team_a": {"team_id": team_a_id, "before": baseline_a["total"], "after": new_a["total"],
                   "delta": round(new_a["total"] - baseline_a["total"], 2),
                   "raw_before": baseline_a["raw_total"], "raw_after": new_a["raw_total"],
                   "raw_delta": round(new_a["raw_total"] - baseline_a["raw_total"], 2)},
        "team_b": {"team_id": team_b_id, "before": baseline_b["total"], "after": new_b["total"],
                   "delta": round(new_b["total"] - baseline_b["total"], 2),
                   "raw_before": baseline_b["raw_total"], "raw_after": new_b["raw_total"],
                   "raw_delta": round(new_b["raw_total"] - baseline_b["raw_total"], 2)},
    }


# 2-for-1 / 2-for-2 is combinatorially much more expensive (see the
# candidate_prefilter note below) - default to 1-for-1 only, same as
# the README documents. Pass combo_sizes=(1, 2) explicitly, with a
# smaller candidate_prefilter, if you want the wider (slower) search.
DEFAULT_COMBO_SIZES = (1, 2)


def suggest_trades(conn, league_key: str, my_team_id: int, start_week: int, end_week: int,
                    as_of_week: int = None, candidate_prefilter: int = 12,
                    combo_sizes=DEFAULT_COMBO_SIZES, partner_team_id: int = None,
                    progress_callback=None, decay: Optional[float] = None) -> list:
    """
    Search win-win 1-for-1 (and optionally larger, via combo_sizes)
    trades between my_team_id and every other team in the league - or,
    if partner_team_id is given, just that one team.

    Returns every win-win trade found, sorted by your gain descending -
    the caller decides how much of that to display vs. summarize.

    candidate_prefilter limits each side's roster to its top N players
    by naive rest-of-season projection sum (recency-weighted if `decay`
    is given) before generating trade combinations, since full
    combinatorics over a ~15-man roster on both sides explodes quickly.

    Performance notes:
    - each team's "no trade happened" baseline total is computed ONCE
      (not once per candidate combo, which is what a naive call to
      evaluate_trade() in a loop would do) - and if a combo doesn't
      even help your own team, the partner's side is never simulated
      at all.
    - player info/eligibility and per-week projections are cached and
      shared across every simulate_roster() call in this search, since
      the same handful of players get re-simulated in combo after
      combo.
    Both were the dominant costs here, since a full season simulation
    runs the lineup optimizer for every week.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)

    teams = repo.get_teams(conn, league_key)
    if partner_team_id is not None:
        teams = [t for t in teams if t["team_id"] == partner_team_id]

    player_info_cache: dict = {}
    projection_cache: dict = {}

    my_ids_full = repo.get_roster_player_ids(conn, league_key, my_team_id, as_of_week)
    my_reserved = repo.get_reserved_player_ids(conn, league_key, my_team_id, as_of_week)
    my_tradeable_ids = [pid for pid in my_ids_full if pid not in my_reserved]
    my_candidates = _top_players_by_rest_of_season(
        conn, league_key, my_tradeable_ids, start_week, end_week, candidate_prefilter, decay,
    )
    my_baseline_sim = simulate_roster(
        conn, league_key, my_ids_full, slot_counts, start_week, end_week,
        player_info_cache, projection_cache, decay=decay,
    )
    my_baseline = my_baseline_sim["total"]
    my_baseline_raw = my_baseline_sim["raw_total"]

    # First pass: figure out each opposing team's candidate pool up front
    # (cheap - just SQL + sorting, no simulation) so we can both (a) reuse
    # it in the real search below without recomputing it, and (b) total up
    # how many (my_combo, other_combo) pairs will be tried overall, which
    # is what makes a meaningful progress bar possible.
    team_data = {}
    total_combos = 0
    for team in teams:
        other_id = team["team_id"]
        if other_id == my_team_id:
            continue
        other_ids_full = repo.get_roster_player_ids(conn, league_key, other_id, as_of_week)
        other_reserved = repo.get_reserved_player_ids(conn, league_key, other_id, as_of_week)
        other_tradeable_ids = [pid for pid in other_ids_full if pid not in other_reserved]
        other_candidates = _top_players_by_rest_of_season(
            conn, league_key, other_tradeable_ids, start_week, end_week, candidate_prefilter, decay,
        )
        team_data[other_id] = (other_ids_full, other_candidates)

        for size in combo_sizes:
            if len(my_candidates) >= size and len(other_candidates) >= size:
                total_combos += comb(len(my_candidates), size) * comb(len(other_candidates), size)

    # Call the callback roughly 100-200 times over the whole search rather
    # than on every combo, which would dominate runtime with UI updates.
    update_every = max(1, total_combos // 150)
    considered = 0

    def _report(force=False):
        if progress_callback and (force or considered % update_every == 0):
            progress_callback(considered, total_combos)

    proposals = []

    for team in teams:
        other_id = team["team_id"]
        if other_id == my_team_id:
            continue

        other_ids_full, other_candidates = team_data[other_id]
        other_baseline_sim = simulate_roster(
            conn, league_key, other_ids_full, slot_counts, start_week, end_week,
            player_info_cache, projection_cache, decay=decay,
        )
        other_baseline = other_baseline_sim["total"]
        other_baseline_raw = other_baseline_sim["raw_total"]

        for size in combo_sizes:
            for my_combo in combinations(my_candidates, size):
                new_a_ids = [pid for pid in my_ids_full if pid not in my_combo]

                for other_combo in combinations(other_candidates, size):
                    considered += 1
                    _report()

                    trial_a_ids = new_a_ids + list(other_combo)
                    new_a_sim = simulate_roster(
                        conn, league_key, trial_a_ids, slot_counts, start_week, end_week,
                        player_info_cache, projection_cache, decay=decay,
                    )
                    my_delta = round(new_a_sim["total"] - my_baseline, 2)
                    if my_delta <= 0:
                        continue  # doesn't even help me - skip the pricier partner-side check

                    new_b_ids = [pid for pid in other_ids_full if pid not in other_combo] + list(my_combo)
                    new_b_sim = simulate_roster(
                        conn, league_key, new_b_ids, slot_counts, start_week, end_week,
                        player_info_cache, projection_cache, decay=decay,
                    )
                    partner_delta = round(new_b_sim["total"] - other_baseline, 2)
                    if partner_delta <= 0:
                        continue

                    proposals.append({
                        "give": [repo.get_player_info(conn, pid, cache=player_info_cache) for pid in my_combo],
                        "get": [repo.get_player_info(conn, pid, cache=player_info_cache) for pid in other_combo],
                        "partner_team_id": other_id,
                        "partner_team_name": team["team_name"],
                        "my_delta": my_delta,
                        "partner_delta": partner_delta,
                        "my_raw_delta": round(new_a_sim["raw_total"] - my_baseline_raw, 2),
                        "partner_raw_delta": round(new_b_sim["raw_total"] - other_baseline_raw, 2),
                    })

    _report(force=True)  # make sure the caller sees 100% at the end

    proposals.sort(key=lambda p: math.sqrt(p["my_delta"] * p["partner_delta"]), reverse=True)
    return proposals


def _top_players_by_rest_of_season(conn, league_key, player_ids, start_week, end_week, limit,
                                    decay: Optional[float] = None):
    """Rank player_ids by rest-of-season projection - recency-weighted
    if `decay` is given, plain sum otherwise - and keep the top `limit`."""
    if not player_ids:
        return []
    totals = repo.get_projection_totals(conn, league_key, player_ids, start_week, end_week, decay)
    # Every requested id is in `totals` (0.0 if it had no projection rows
    # at all), so nobody gets silently dropped; sorted() is stable, so ties
    # keep the roster's original order.
    ranked = sorted(player_ids, key=lambda pid: totals[pid]["weighted"], reverse=True)
    return ranked[:limit]


def explain_trade(conn, league_key: str, team_a_id: int, team_a_gives: list,
                   team_b_id: int, team_b_gives: list, start_week: int, end_week: int,
                   as_of_week: int = None, decay: Optional[float] = None) -> dict:
    """
    Week-by-week comparison of both teams' projected totals with vs
    without a specific trade, for the "why does this help" view.

    weekly_before/weekly_after are raw per-week points. total_*/delta are
    recency-weighted when `decay` is set (plain sums otherwise);
    raw_total_*/raw_delta are always the plain sums; `weights` is the
    {week: weight} used.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    before_a = simulate_roster(conn, league_key, a_ids, slot_counts, start_week, end_week, decay=decay)
    before_b = simulate_roster(conn, league_key, b_ids, slot_counts, start_week, end_week, decay=decay)

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    after_a = simulate_roster(conn, league_key, new_a_ids, slot_counts, start_week, end_week, decay=decay)
    after_b = simulate_roster(conn, league_key, new_b_ids, slot_counts, start_week, end_week, decay=decay)

    def _side(team_id, before, after):
        return {
            "team_id": team_id,
            "weekly_before": before["weekly"], "weekly_after": after["weekly"],
            "weights": before["weights"],
            "total_before": before["total"], "total_after": after["total"],
            "delta": after["total"] - before["total"],
            "raw_total_before": before["raw_total"], "raw_total_after": after["raw_total"],
            "raw_delta": after["raw_total"] - before["raw_total"],
        }

    return {
        "team_a": _side(team_a_id, before_a, after_a),
        "team_b": _side(team_b_id, before_b, after_b),
    }