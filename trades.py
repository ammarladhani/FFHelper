"""
Trade engine with two entry points:

- evaluate_trade: given an explicit trade (players each side gives up),
  compute the before/after remaining-season projected total for both
  teams.
- suggest_trades: search 1-for-1 (and optionally 2-for-1/2-for-2) swaps
  between your team and every other team, keeping only trades where BOTH
  sides' projected totals improve.

Candidate pools are no longer ranking-based (there used to be a
`candidate_prefilter` that kept only the top-N players by rest-of-season
projection on each side). Instead, the caller passes explicit
`excluded_player_ids` (players who must never appear in a proposed trade -
this is what actually shrinks the combinatorics now) and
`included_player_ids` (if this set is non-empty, every returned proposal
must contain at least one of these players, on either side). Both default
to empty, which is the same as "consider every tradeable player."

Every entry point takes an optional `decay` (None = off). When given, the
win-win test and the sort order both use recency-weighted totals (see
weighting.py) - near-term weeks count for more. Returned `delta` numbers
are in that same weighted metric; the `raw_*` twins are the plain,
unweighted point changes.

Search sizing: `suggest_trades` defaults to 1-for-1 swaps only
(`combo_sizes=(1,)`), matching the README; pass `combo_sizes=(1, 2)`
explicitly for 2-for-1/2-for-2 too - it searches far more combinations and
is much slower, especially with a large candidate pool.

In money mode, a team's CURRENT (unmodified) roster is never re-simulated
here - `payouts.build_expected_money_context()` has already simulated every
team's current roster once to build the money context, so each baseline is
reconstructed from that context via `payouts.baseline_sim_from_context()`
instead of paying for the lineup optimizer a second time. Only HYPOTHETICAL
(post-trade) rosters still go through `simulate_roster()`, since the context
has no way to know about those.
"""

from itertools import combinations
from math import comb
import math
from typing import Optional

import repo
import payouts
from simulator import simulate_roster

DEFAULT_COMBO_SIZES = (1,)


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

    if objective == "money":
        # Already simulated once inside build_expected_money_context().
        baseline_a = payouts.baseline_sim_from_context(
            money_context, team_a_id, start_week, sim_end_week
        )
        baseline_b = payouts.baseline_sim_from_context(
            money_context, team_b_id, start_week, sim_end_week
        )
    else:
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


def suggest_trades(
    conn,
    league_key: str,
    my_team_id: int,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    combo_sizes=DEFAULT_COMBO_SIZES,
    partner_team_id: int = None,
    progress_callback=None,
    decay: Optional[float] = None,
    objective: str = "points",
    excluded_player_ids: Optional[set] = None,
    included_player_ids: Optional[set] = None,
) -> list:
    """
    Search win-win 1-for-1 (and optionally larger) trades.

    `excluded_player_ids`: players (yours or any opponent's) that are
    never considered as part of a trade - removed from the candidate pool
    on whichever side they belong to before any combinations are built.
    This is what keeps the search tractable now that there's no top-N
    ranking prefilter.

    `included_player_ids`: if non-empty, only proposals that contain at
    least one of these players (give side OR get side) are returned. This
    is checked before the (expensive) roster simulation for each combo, so
    it also cuts down the work done, not just what gets shown.

    `objective="points"` preserves the existing behavior.

    `objective="money"` keeps only trades where both teams' expected prize
    money increases. Weekly high-score prizes and end-of-season placement
    prizes are both included.
    """
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    # Normalize IDs because the frontend sends JSON strings while SQLite
    # / repo data may expose IDs using another scalar type.
    excluded_player_ids = {str(pid) for pid in (excluded_player_ids or ())}
    included_player_ids = {str(pid) for pid in (included_player_ids or ())}

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

    my_candidates = [
        pid for pid in my_tradeable_ids
        if str(pid) not in excluded_player_ids
    ]

    if objective == "money":
        # Already simulated once inside build_expected_money_context().
        my_baseline_sim = payouts.baseline_sim_from_context(
            money_context, my_team_id, start_week, sim_end_week
        )
    else:
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

        other_candidates = [
            pid for pid in other_tradeable_ids
            if str(pid) not in excluded_player_ids
        ]

        if objective == "money":
            # Already simulated once inside build_expected_money_context().
            other_baseline_sim = payouts.baseline_sim_from_context(
                money_context, other_id, start_week, sim_end_week
            )
        else:
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

        my_included = {
            pid for pid in my_candidates
            if str(pid) in included_player_ids
        }
        other_included = {
            pid for pid in other_candidates
            if str(pid) in included_player_ids
        }

        for size in combo_sizes:
            if len(my_candidates) < size or len(other_candidates) < size:
                continue

            if not included_player_ids:
                total_combos += comb(len(my_candidates), size) * comb(
                    len(other_candidates), size
                )
            else:
                # Count only combinations that can actually satisfy the
                # "at least one included player" requirement. This makes the
                # progress total reflect the real search space too.
                my_inc_count = (
                    comb(len(my_candidates), size)
                    - comb(len(my_candidates) - len(my_included), size)
                    if len(my_candidates) - len(my_included) >= size
                    else comb(len(my_candidates), size)
                )
                my_noninc_count = (
                    comb(len(my_candidates) - len(my_included), size)
                    if len(my_candidates) - len(my_included) >= size
                    else 0
                )
                other_inc_count = (
                    comb(len(other_candidates), size)
                    - comb(len(other_candidates) - len(other_included), size)
                    if len(other_candidates) - len(other_included) >= size
                    else comb(len(other_candidates), size)
                )
                other_nonrestricted_count = comb(len(other_candidates), size)

                total_combos += (
                    my_inc_count * other_nonrestricted_count
                    + my_noninc_count * other_inc_count
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
            if len(my_candidates) < size or len(other_candidates) < size:
                continue

            if included_player_ids:
                my_inc_set = {
                    pid for pid in my_candidates
                    if str(pid) in included_player_ids
                }
                other_inc_set = {
                    pid for pid in other_candidates
                    if str(pid) in included_player_ids
                }

                all_my_combos = list(combinations(my_candidates, size))
                my_inc_combos = [c for c in all_my_combos if my_inc_set.intersection(c)]
                my_noninc_combos = [c for c in all_my_combos if not my_inc_set.intersection(c)]

                all_other_combos = list(combinations(other_candidates, size))
                other_inc_combos = [c for c in all_other_combos if other_inc_set.intersection(c)]

                combo_pairs = (
                    ((my_combo, other_combo) for my_combo in my_inc_combos for other_combo in all_other_combos),
                    ((my_combo, other_combo) for my_combo in my_noninc_combos for other_combo in other_inc_combos),
                )
            else:
                combo_pairs = (
                    ((my_combo, other_combo)
                     for my_combo in combinations(my_candidates, size)
                     for other_combo in combinations(other_candidates, size)),
                )

            for pair_group in combo_pairs:
                for my_combo, other_combo in pair_group:
                    considered += 1
                    _report()

                    new_a_ids = [pid for pid in my_ids_full if pid not in my_combo]
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
    if `decay` is given, plain sum otherwise - and keep the top `limit`.

    No longer used by suggest_trades (candidate pools there are now just
    "everything not excluded"), but kept around as a general-purpose
    ranking helper.
    """
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

    if objective == "money":
        # Already simulated once inside build_expected_money_context().
        before_a = payouts.baseline_sim_from_context(
            money_context, team_a_id, start_week, sim_end_week
        )
        before_b = payouts.baseline_sim_from_context(
            money_context, team_b_id, start_week, sim_end_week
        )
    else:
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