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
import payouts
from simulator import simulate_roster

def _reject_reserved(conn, league_key, team_id, player_ids, as_of_week):
    reserved = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    locked = [pid for pid in player_ids if pid in reserved]
    if locked:
        names = [repo.get_player_info(conn, pid)["name"] for pid in locked]
        raise ValueError(f"Can't trade {', '.join(names)}: currently on IR/Taxi.")

def evaluate_trade(
    conn,
    league_key: str,
    team_a_id: int,
    team_a_gives: list,
    team_b_id: int,
    team_b_gives: list,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    decay: Optional[float] = None,
    objective: str = "points",
) -> dict:
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    _reject_reserved(conn, league_key, team_a_id, team_a_gives, as_of_week)
    _reject_reserved(conn, league_key, team_b_id, team_b_gives, as_of_week)
    slot_counts = repo.get_slot_counts(conn, league_key)

    money_context = None
    sim_end_week = end_week
    sim_decay = decay
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )
        sim_end_week = money_context["playoff_settings"]["end_week"]
        sim_decay = None

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    baseline_a = simulate_roster(
        conn, league_key, a_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )
    baseline_b = simulate_roster(
        conn, league_key, b_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    new_a = simulate_roster(
        conn, league_key, new_a_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )
    new_b = simulate_roster(
        conn, league_key, new_b_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )

    money_before = {}
    money_after = {}
    if objective == "money":
        money_before = payouts.expected_money_from_context(
            money_context,
            focus_team_ids={team_a_id, team_b_id},
        )
        money_after = payouts.expected_money_from_context(
            money_context,
            weekly_overrides={
                team_a_id: new_a["weekly"],
                team_b_id: new_b["weekly"],
            },
            focus_team_ids={team_a_id, team_b_id},
        )

    point_delta_a = new_a["total"] - baseline_a["total"]
    point_delta_b = new_b["total"] - baseline_b["total"]
    money_delta_a = (
        money_after[team_a_id]["expected_total"] - money_before[team_a_id]["expected_total"]
        if objective == "money" else None
    )
    money_delta_b = (
        money_after[team_b_id]["expected_total"] - money_before[team_b_id]["expected_total"]
        if objective == "money" else None
    )

    objective_delta_a = money_delta_a if objective == "money" else point_delta_a
    objective_delta_b = money_delta_b if objective == "money" else point_delta_b

    return {
        "objective": objective,
        "team_a": {
            "team_id": team_a_id,
            "before": round(
                money_before[team_a_id]["expected_total"]
                if objective == "money"
                else baseline_a["total"],
                2,
            ),
            "after": round(
                money_after[team_a_id]["expected_total"]
                if objective == "money"
                else new_a["total"],
                2,
            ),
            "delta": round(objective_delta_a, 2),
            "raw_before": baseline_a["raw_total"],
            "raw_after": new_a["raw_total"],
            "raw_delta": round(new_a["raw_total"] - baseline_a["raw_total"], 2),
            "projected_before": baseline_a["total"],
            "projected_after": new_a["total"],
            "projected_delta": round(point_delta_a, 2),
            "money_before": (
                round(money_before[team_a_id]["expected_total"], 2)
                if objective == "money" else None
            ),
            "money_after": (
                round(money_after[team_a_id]["expected_total"], 2)
                if objective == "money" else None
            ),
            "money_delta": round(money_delta_a, 2) if money_delta_a is not None else None,
        },
        "team_b": {
            "team_id": team_b_id,
            "before": round(
                money_before[team_b_id]["expected_total"]
                if objective == "money"
                else baseline_b["total"],
                2,
            ),
            "after": round(
                money_after[team_b_id]["expected_total"]
                if objective == "money"
                else new_b["total"],
                2,
            ),
            "delta": round(objective_delta_b, 2),
            "raw_before": baseline_b["raw_total"],
            "raw_after": new_b["raw_total"],
            "raw_delta": round(new_b["raw_total"] - baseline_b["raw_total"], 2),
            "projected_before": baseline_b["total"],
            "projected_after": new_b["total"],
            "projected_delta": round(point_delta_b, 2),
            "money_before": (
                round(money_before[team_b_id]["expected_total"], 2)
                if objective == "money" else None
            ),
            "money_after": (
                round(money_after[team_b_id]["expected_total"], 2)
                if objective == "money" else None
            ),
            "money_delta": round(money_delta_b, 2) if money_delta_b is not None else None,
        },
    }


# 2-for-1 / 2-for-2 is combinatorially much more expensive (see the
# candidate_prefilter note below) - default to 1-for-1 only, same as
# the README documents. Pass combo_sizes=(1, 2) explicitly, with a
# smaller candidate_prefilter, if you want the wider (slower) search.
DEFAULT_COMBO_SIZES = (1, 2)


def suggest_trades(
    conn,
    league_key: str,
    my_team_id: int,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    candidate_prefilter: int = 12,
    combo_sizes=DEFAULT_COMBO_SIZES,
    partner_team_id: int = None,
    progress_callback=None,
    decay: Optional[float] = None,
    objective: str = "points",
) -> list:
    """
    Search win-win 1-for-1 (and optionally larger) trades.

    `objective="points"` preserves the existing behavior.

    `objective="money"` keeps only trades where both teams' expected prize
    money increases. Weekly high-score prizes and end-of-season placement
    prizes are both included.
    """
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    slot_counts = repo.get_slot_counts(conn, league_key)

    teams = repo.get_teams(conn, league_key)
    if partner_team_id is not None:
        teams = [t for t in teams if t["team_id"] == partner_team_id]

    player_info_cache: dict = {}
    projection_cache: dict = {}

    money_context = None
    sim_end_week = end_week
    sim_decay = decay
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )
        sim_end_week = money_context["playoff_settings"]["end_week"]
        sim_decay = None

    my_ids_full = repo.get_roster_player_ids(conn, league_key, my_team_id, as_of_week)
    my_reserved = repo.get_reserved_player_ids(conn, league_key, my_team_id, as_of_week)
    my_tradeable_ids = [pid for pid in my_ids_full if pid not in my_reserved]

    my_candidates = _top_players_by_rest_of_season(
        conn,
        league_key,
        my_tradeable_ids,
        start_week,
        end_week,
        candidate_prefilter,
        sim_decay,
    )

    my_baseline_sim = simulate_roster(
        conn,
        league_key,
        my_ids_full,
        slot_counts,
        start_week,
        sim_end_week,
        player_info_cache,
        projection_cache,
        decay=sim_decay,
    )
    my_baseline = my_baseline_sim["total"]
    my_baseline_raw = my_baseline_sim["raw_total"]

    baseline_money_by_team = None
    my_money_before = None
    if objective == "money":
        baseline_money_by_team = payouts.expected_money_from_context(money_context)
        my_money_before = baseline_money_by_team[my_team_id]["expected_total"]

    # First pass: build each partner's candidate pool.
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
            conn,
            league_key,
            other_tradeable_ids,
            start_week,
            end_week,
            candidate_prefilter,
            sim_decay,
        )

        other_baseline_sim = simulate_roster(
            conn,
            league_key,
            other_ids_full,
            slot_counts,
            start_week,
            sim_end_week,
            player_info_cache,
            projection_cache,
            decay=sim_decay,
        )
        other_money_before = None
        if objective == "money":
            other_money_before = baseline_money_by_team[other_id]["expected_total"]

        team_data[other_id] = (
            other_ids_full,
            other_candidates,
            other_baseline_sim,
            other_money_before,
        )

        for size in combo_sizes:
            if len(my_candidates) >= size and len(other_candidates) >= size:
                total_combos += comb(len(my_candidates), size) * comb(
                    len(other_candidates), size
                )

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

        (
            other_ids_full,
            other_candidates,
            other_baseline_sim,
            other_money_before,
        ) = team_data[other_id]

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
                        conn,
                        league_key,
                        trial_a_ids,
                        slot_counts,
                        start_week,
                        sim_end_week,
                        player_info_cache,
                        projection_cache,
                        decay=sim_decay,
                    )

                    point_my_delta = new_a_sim["total"] - my_baseline

                    if objective == "money":
                        money_after = payouts.expected_money_from_context(
                            money_context,
                            weekly_overrides={my_team_id: new_a_sim["weekly"]},
                            focus_team_ids={my_team_id},
                        )
                        money_my_delta = (
                            money_after[my_team_id]["expected_total"]
                            - my_money_before
                        )
                        my_delta = money_my_delta
                    else:
                        money_my_delta = None
                        my_delta = point_my_delta

                    if my_delta <= 0:
                        continue

                    new_b_ids = [pid for pid in other_ids_full if pid not in other_combo] + list(my_combo)
                    new_b_sim = simulate_roster(
                        conn,
                        league_key,
                        new_b_ids,
                        slot_counts,
                        start_week,
                        sim_end_week,
                        player_info_cache,
                        projection_cache,
                        decay=sim_decay,
                    )

                    point_partner_delta = new_b_sim["total"] - other_baseline

                    if objective == "money":
                        money_after = payouts.expected_money_from_context(
                            money_context,
                            weekly_overrides={
                                my_team_id: new_a_sim["weekly"],
                                other_id: new_b_sim["weekly"],
                            },
                            focus_team_ids={my_team_id, other_id},
                        )
                        money_my_delta = (
                            money_after[my_team_id]["expected_total"]
                            - my_money_before
                        )
                        money_partner_delta = (
                            money_after[other_id]["expected_total"]
                            - other_money_before
                        )
                        # Use the two-team evaluation so the final deltas are
                        # calculated from the same hypothetical league state.
                        my_delta = money_my_delta
                        partner_delta = money_partner_delta

                        # The partner's roster change can alter the money value of
                        # the trade for BOTH sides.  The earlier `my_delta <= 0`
                        # check happened before that second-team override, so
                        # re-check both final values here.  Otherwise a trade can
                        # reach the final sort with a negative product and
                        # math.sqrt() raises: "expected a nonnegative input".
                        if my_delta <= 0 or partner_delta <= 0:
                            continue
                    else:
                        money_partner_delta = None
                        partner_delta = point_partner_delta

                    if partner_delta <= 0:
                        continue

                    proposals.append({
                        "objective": objective,
                        "give": [
                            repo.get_player_info(conn, pid, cache=player_info_cache)
                            for pid in my_combo
                        ],
                        "get": [
                            repo.get_player_info(conn, pid, cache=player_info_cache)
                            for pid in other_combo
                        ],
                        "partner_team_id": other_id,
                        "partner_team_name": team["team_name"],
                        "my_delta": round(my_delta, 2),
                        "partner_delta": round(partner_delta, 2),
                        "my_raw_delta": round(
                            new_a_sim["raw_total"] - my_baseline_raw, 2
                        ),
                        "partner_raw_delta": round(
                            new_b_sim["raw_total"] - other_baseline_raw, 2
                        ),
                        "my_projected_delta": round(point_my_delta, 2),
                        "partner_projected_delta": round(point_partner_delta, 2),
                        "my_money_delta": round(money_my_delta, 2)
                        if objective == "money" else None,
                        "partner_money_delta": round(money_partner_delta, 2)
                        if objective == "money" else None,
                    })

    _report(force=True)

    proposals.sort(
        key=lambda p: math.sqrt(p["my_delta"] * p["partner_delta"]),
        reverse=True,
    )
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


def explain_trade(
    conn,
    league_key: str,
    team_a_id: int,
    team_a_gives: list,
    team_b_id: int,
    team_b_gives: list,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    decay: Optional[float] = None,
    objective: str = "points",
) -> dict:
    """
    Week-by-week comparison of both teams' projected totals with vs without
    a specific trade. In money mode, also returns expected prize-money impact.
    """
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    _reject_reserved(conn, league_key, team_a_id, team_a_gives, as_of_week)
    _reject_reserved(conn, league_key, team_b_id, team_b_gives, as_of_week)

    slot_counts = repo.get_slot_counts(conn, league_key)
    money_context = None
    sim_end_week = end_week
    sim_decay = decay
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )
        sim_end_week = money_context["playoff_settings"]["end_week"]
        sim_decay = None

    a_ids = repo.get_roster_player_ids(conn, league_key, team_a_id, as_of_week)
    b_ids = repo.get_roster_player_ids(conn, league_key, team_b_id, as_of_week)

    before_a = simulate_roster(
        conn, league_key, a_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )
    before_b = simulate_roster(
        conn, league_key, b_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )

    new_a_ids = [pid for pid in a_ids if pid not in team_a_gives] + team_b_gives
    new_b_ids = [pid for pid in b_ids if pid not in team_b_gives] + team_a_gives

    after_a = simulate_roster(
        conn, league_key, new_a_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )
    after_b = simulate_roster(
        conn, league_key, new_b_ids, slot_counts, start_week, sim_end_week, decay=sim_decay
    )

    money_before = money_after = {}
    if objective == "money":
        money_before = payouts.expected_money_from_context(
            money_context,
            focus_team_ids={team_a_id, team_b_id},
        )
        money_after = payouts.expected_money_from_context(
            money_context,
            weekly_overrides={
                team_a_id: after_a["weekly"],
                team_b_id: after_b["weekly"],
            },
            focus_team_ids={team_a_id, team_b_id},
        )

    def _side(team_id, before, after):
        projected_delta = after["total"] - before["total"]
        money_delta = (
            money_after[team_id]["expected_total"]
            - money_before[team_id]["expected_total"]
            if objective == "money"
            else None
        )
        objective_delta = money_delta if objective == "money" else projected_delta

        return {
            "team_id": team_id,
            "weekly_before": before["weekly"],
            "weekly_after": after["weekly"],
            "weights": before["weights"],
            "total_before": (
                money_before[team_id]["expected_total"]
                if objective == "money"
                else before["total"]
            ),
            "total_after": (
                money_after[team_id]["expected_total"]
                if objective == "money"
                else after["total"]
            ),
            "delta": objective_delta,
            "raw_total_before": before["raw_total"],
            "raw_total_after": after["raw_total"],
            "raw_delta": after["raw_total"] - before["raw_total"],
            "projected_before": before["total"],
            "projected_after": after["total"],
            "projected_delta": projected_delta,
            "money_before": (
                money_before[team_id]["expected_total"]
                if objective == "money"
                else None
            ),
            "money_after": (
                money_after[team_id]["expected_total"]
                if objective == "money"
                else None
            ),
            "money_delta": round(money_delta, 2)
            if money_delta is not None
            else None,
        }

    return {
        "objective": objective,
        "team_a": _side(team_a_id, before_a, after_a),
        "team_b": _side(team_b_id, before_b, after_b),
    }
